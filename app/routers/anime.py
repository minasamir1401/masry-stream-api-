import logging
import re
from datetime import date
from typing import Optional
from urllib.parse import urlsplit
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.services.anime_service import anime_service
from app.services.anime_override_service import anime_override_service
from app.services.playback_sources import playback_sources
from app.services.hls_gateway import relay as relay_hls
from app.services.video_http import get_video_client
from app.services.animetna import HLS_MEDIA_SUFFIXES, MP4_MEDIA_SUFFIXES, is_anime4up_host, media_request_headers
from app.services.hero_images import hero_images
from app.services.airing_schedule import airing_schedule
from app.services.security_shield import security_shield

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/anime", tags=["Anime"])


@router.get("/homepage")
async def get_homepage():
    try:
        return await anime_service.get_homepage()
    except Exception as error:
        logger.warning("Homepage metadata unavailable: %s", type(error).__name__)
        raise HTTPException(502, "تعذّر تحميل بيانات الأنمي حاليًا") from error


@router.get("/schedule")
async def get_schedule(day: Optional[date] = None):
    try:
        return await airing_schedule.get_week(day)
    except Exception as error:
        logger.warning("Airing schedule unavailable: %s", type(error).__name__)
        raise HTTPException(502, "تعذّر جلب مواعيد العرض حاليًا. حاول مرة أخرى.") from error


@router.get("/catalog")
async def browse_catalog(
    page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=50),
    q: Optional[str] = Query(None, max_length=200), genre: Optional[str] = None,
    year: Optional[int] = Query(None, ge=1940, le=2100),
    season: Optional[str] = Query(None, pattern="^(WINTER|SPRING|SUMMER|FALL)$"),
    status: Optional[str] = Query(None, pattern="^(FINISHED|RELEASING|NOT_YET_RELEASED|CANCELLED|HIATUS)$"),
    format: Optional[str] = Query(None, pattern="^(TV|TV_SHORT|MOVIE|SPECIAL|OVA|ONA|MUSIC)$"),
    sort: str = Query("POPULARITY_DESC", pattern="^(POPULARITY_DESC|SCORE_DESC|START_DATE_DESC|TITLE_ROMAJI|TRENDING_DESC|EPISODES_DESC)$"),
):
    try:
        return await anime_service.browse(page, page_size, q, genre, year, season, status, format, sort)
    except Exception as error:
        logger.warning("Catalog lookup failed: %s", type(error).__name__)
        raise HTTPException(502, "تعذّر تحميل مكتبة الأنمي، حاول مرة أخرى") from error


def sanitize_server_name(raw_name: str, server_num: int) -> str:
    if not raw_name:
        return f"سيرفر {server_num}"
    name_lower = raw_name.lower()
    blocked_words = [
        "anime3rb", "anime4up", "animetna", "anslayer", "ok.ru",
        "mp4upload", "share4max", "vidara", "mixdrop", "lulustream",
        "voe", "uqload", ".shop", ".site", ".org", ".com", ".net", ".to", ".is"
    ]
    has_blocked = any(word in name_lower for word in blocked_words)
    quality_match = re.search(r"(2160p?|4k|1080p?|fhd|720p?|hd|480p?|sd|360p?)", name_lower)
    quality = quality_match.group(1).upper() if quality_match else ""
    if "FHD" in quality or "1080" in quality:
        quality_label = "FHD 1080p"
    elif "720" in quality or "HD" in quality:
        quality_label = "HD 720p"
    elif "480" in quality or "SD" in quality:
        quality_label = "SD 480p"
    elif quality:
        quality_label = quality
    else:
        quality_label = ""

    if has_blocked or not raw_name.strip():
        if quality_label:
            return f"سيرفر {server_num} ({quality_label})"
        return f"سيرفر المشاهدة {server_num}"
    return raw_name



@router.get("/trending")
async def get_trending(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=50),
):
    try:
        return await anime_service.get_trending(page=page, per_page=page_size)
    except Exception as e:
        logger.error("Failed to fetch trending anime: %s", e)
        raise HTTPException(status_code=502, detail="تعذر جلب قائمة الأنمي الشائعة")


@router.get("/popular-series")
async def get_popular_series(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=50),
):
    try:
        return await anime_service.get_popular_series(page=page, per_page=page_size)
    except Exception as e:
        logger.error("Failed to fetch popular anime series: %s", e)
        raise HTTPException(status_code=502, detail="تعذر جلب مسلسلات الأنمي الشهيرة")


@router.get("/movies")
async def get_anime_movies(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=50),
):
    try:
        return await anime_service.get_anime_movies(page=page, per_page=page_size)
    except Exception as e:
        logger.error("Failed to fetch anime movies: %s", e)
        raise HTTPException(status_code=502, detail="تعذر جلب أفلام الأنمي")


@router.get("/top-rated")
async def get_top_rated(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=50),
):
    try:
        return await anime_service.get_top_rated(page=page, per_page=page_size)
    except Exception as e:
        logger.error("Failed to fetch top rated anime: %s", e)
        raise HTTPException(status_code=502, detail="تعذر جلب الأنميات الأعلى تقييماً")


@router.get("/search")
async def search_anime(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=50),
):
    try:
        return await anime_service.search(query=q, page=page, per_page=page_size)
    except Exception as e:
        logger.error("Failed to search anime: %s", e)
        raise HTTPException(status_code=502, detail="فشل البحث عن الأنمي")


@router.get("/{anime_id}")
async def get_anime_details(anime_id: int):
    hidden_ids = anime_override_service.get_hidden_anime_ids_sync()
    if anime_id in hidden_ids:
        raise HTTPException(status_code=404, detail="الأنمي المطلوب غير متاح حالياً")
    item = await anime_service.get_by_id(anime_id)
    if not item:
        raise HTTPException(status_code=404, detail="الأنمي المطلوب غير موجود")
    return item


@router.get("/{anime_id}/stream")
@router.get("/{anime_id}/stream/{episode}")
async def get_anime_stream(request: Request, anime_id: int, episode: int = 1, refresh: bool = False):
    is_blocked, block_reason, retry_after = security_shield.validate_request_access(request)
    if is_blocked:
        await security_shield.apply_tarpit()
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        raise HTTPException(403, block_reason, headers=headers)

    hidden_ids = anime_override_service.get_hidden_anime_ids_sync()
    if anime_id in hidden_ids:
        raise HTTPException(status_code=404, detail="الأنمي المطلوب غير متاح حالياً")
    if episode < 1:
        raise HTTPException(422, "رقم الحلقة غير صالح")

    client_ip = security_shield.get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    sources = await anime_service.get_stream_sources(anime_id=anime_id, episode=episode, refresh=refresh)
    if sources.get("availability") == "metadata_unavailable":
        raise HTTPException(503, "تعذّر جلب بيانات هذا الأنمي. أعد المحاولة بعد قليل.", headers={"Retry-After": "15"})
    servers = []
    for idx, source in enumerate(sources.get("servers", [])):
        clean_name = sanitize_server_name(source.get("name", ""), idx + 1)
        source = {**source, "name": clean_name}
        if source.get("type") == "direct" and not source.get("is_custom"):
            token = await playback_sources.register(anime_id, episode, source)
            st_token = security_shield.generate_token(f"{anime_id}:{episode}:{token}", client_ip, user_agent)
            source = {**source, "playback_url": f"/api/anime/{anime_id}/video/{episode}?source={token}&st={st_token}"}
        elif source.get("type") == "hls" and (urlsplit(source["url"]).hostname or "").endswith(HLS_MEDIA_SUFFIXES) and not source.get("is_custom"):
            token = await playback_sources.register(anime_id, episode, source)
            # A stream token must remain valid while HLS.js requests later playlist
            # segments. The default five minute API token expires mid-episode.
            st_token = security_shield.generate_token(
                f"{anime_id}:{episode}:{token}", client_ip, user_agent, ttl_seconds=14400
            )
            source = {**source, "playback_url": f"/api/anime/{anime_id}/hls/{episode}?source={token}&st={st_token}"}
        servers.append(source)
    return {**sources, "servers": servers}


@router.get("/{anime_id}/hls/{episode}")
async def get_hls_asset(
    anime_id: int,
    episode: int,
    request: Request,
    source: str,
    asset: Optional[str] = None,
    st: Optional[str] = None,
):
    return await relay_hls(
        anime_id=anime_id,
        episode=episode,
        source=source,
        asset=asset,
        byte_range=request.headers.get("range"),
        request=request,
        st=st,
    )


@router.get("/{anime_id}/video/{episode}")
async def get_anime_video(
    anime_id: int,
    episode: int,
    request: Request,
    server: int = Query(0, ge=0),
    source: Optional[str] = None,
    st: Optional[str] = None,
):
    """Relay a verified episode source; requires security token validation."""
    is_blocked, block_reason, retry_after = security_shield.validate_request_access(request)
    if is_blocked:
        await security_shield.apply_tarpit()
        headers = {"Retry-After": str(retry_after)} if retry_after else None
        raise HTTPException(403, block_reason, headers=headers)

    if not security_shield.verify_referrer_origin(request):
        await security_shield.apply_tarpit()
        raise HTTPException(403, "Direct hotlinking is prohibited")

    client_ip = security_shield.get_client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    if source:
        if not re.fullmatch(r"[a-f0-9]{24}", source):
            raise HTTPException(422, "رابط التشغيل غير صالح")
        valid, reason = security_shield.verify_token(st, f"{anime_id}:{episode}:{source}", client_ip, user_agent)
        if not valid:
            await security_shield.apply_tarpit(delay=1.0)
            raise HTTPException(403, f"انتهت صلاحية جلسة المشاهدة أو الرمز غير صالح ({reason})")
        selected = await playback_sources.get(anime_id, episode, source)
    else:
        sources = await anime_service.get_stream_sources(anime_id, episode)
        servers = sources.get("servers", [])
        selected = servers[server] if server < len(servers) else None
    if not selected or selected.get("type") != "direct":
        raise HTTPException(404, "سيرفر الفيديو غير متاح")
    url = selected["url"]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(MP4_MEDIA_SUFFIXES):
        raise HTTPException(404, "رابط الفيديو غير صالح")
    requested = request.headers.get("range", "bytes=0-")
    match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
    if not match:
        raise HTTPException(416, "نطاق الفيديو غير صالح")
    start = int(match[1])
    # Small ranges avoid large upstream requests and stop promptly on seeking.
    end = min(int(match[2]), start + 2 * 1024 * 1024 - 1) if match[2] else start + 2 * 1024 * 1024 - 1
    if end < start:
        raise HTTPException(416, "نطاق الفيديو غير صالح")
    client = get_video_client()
    try:
        upstream = await client.send(client.build_request("GET", url, headers={
            **media_request_headers(url),
            "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity",
        }), stream=True)
        if source and upstream.status_code in (401, 403, 404, 410):
            await upstream.aclose()
            renewed = await playback_sources.renew(client, anime_id, episode, source, selected)
            if renewed:
                url = renewed["url"]
                upstream = await client.send(client.build_request("GET", url, headers={
                    **media_request_headers(url),
                    "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity",
                }), stream=True)
        if upstream.status_code != 206:
            status = 416 if upstream.status_code == 416 else 502
            await upstream.aclose()
            raise HTTPException(status, "تعذّر تحميل الفيديو من السيرفر")
        if upstream.url.scheme != "https" or not (upstream.url.host or "").endswith(MP4_MEDIA_SUFFIXES):
            await upstream.aclose()
            raise HTTPException(502, "رابط الفيديو غير صالح")
    except (httpx.HTTPError, HTTPException) as error:
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(502, "تعذّر الاتصال بسيرفر الفيديو") from error

    async def chunks():
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()

    headers = {key: upstream.headers[key] for key in ("Content-Range", "Content-Length") if key in upstream.headers}
    headers.update({"Accept-Ranges": "bytes", "Cache-Control": "no-store", "Content-Encoding": "identity", "X-Content-Type-Options": "nosniff"})
    return StreamingResponse(chunks(), status_code=206, headers=headers, media_type="video/mp4")


@router.get("/{anime_id}/episodes")
async def get_anime_episodes(anime_id: int):
    result = await anime_service.episodes_for_anime(anime_id)
    if result is None:
        raise HTTPException(404, "تعذّر العثور على بيانات هذا الأنمي")
    return result


@router.get("/{anime_id}/hero-image")
async def get_hero_image(anime_id: int):
    item = await anime_service.get_by_id(anime_id)
    if not item:
        raise HTTPException(404, "الأنمي المطلوب غير موجود")
    image = await hero_images.get(item)
    if not image:
        raise HTTPException(503, "تعذّر تحميل صورة عرضية لهذا العمل حاليًا", headers={"Retry-After": "60"})
    return {"anime_id": anime_id, "id_mal": item.get("id_mal"), "image_url": image["image_url"], "source": image["source"], "alternatives": image.get("alternatives", []), "stale": image["stale"]}


@router.get("/{anime_id}/subtitles/{episode}/ar.vtt")
async def get_arabic_vtt(anime_id: int, episode: int = 1, server: int = Query(0, ge=0)):
    sources = await anime_service.get_stream_sources(anime_id, episode)
    servers = sources.get("servers", [])
    if server >= len(servers):
        raise HTTPException(404, "لا يوجد ملف ترجمة منفصل لهذا السيرفر")
    url = servers[server].get("subtitle_url", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not is_anime4up_host(parsed.hostname) or not parsed.path.endswith(".vtt"):
        raise HTTPException(404, "لا يوجد ملف ترجمة منفصل لهذا السيرفر")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            if not response.text.lstrip("\ufeff").startswith("WEBVTT"):
                raise HTTPException(502, "ملف الترجمة غير صالح")
            return Response(response.content, media_type="text/vtt; charset=utf-8")
    except httpx.HTTPError as error:
        raise HTTPException(502, "تعذّر تحميل ملف الترجمة") from error
