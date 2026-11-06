"""Relay only playlist-discovered HLS assets through the site's own origin with security enforcement."""
import hashlib
import re
from typing import Optional
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.services.animetna import HLS_MEDIA_SUFFIXES
from app.services.db_cache import db_cache
from app.services.playback_sources import playback_sources
from app.services.security_shield import security_shield
from app.services.video_http import get_video_client


def trusted_media(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and (parsed.hostname or "").endswith(HLS_MEDIA_SUFFIXES) and parsed.port in (None, 443)
    except ValueError:
        return False


def rewrite_playlist(
    text: str,
    upstream_url: str,
    anime_id: int,
    episode: int,
    source: str,
    client_ip: str,
    user_agent: str,
) -> tuple[str, dict]:
    assets = {}
    segment_token = security_shield.generate_token(
        f"{anime_id}:{episode}:{source}",
        client_ip=client_ip,
        user_agent=user_agent,
        ttl_seconds=14400,
    )

    def rewrite(uri: str) -> str:
        url = urljoin(upstream_url, uri)
        if not trusted_media(url):
            raise HTTPException(502, "رابط غير صالح داخل قائمة الفيديو")
        token = hashlib.sha256(url.encode()).hexdigest()[:24]
        assets[f"hls-asset:{source}:{token}"] = url
        return f"/api/anime/{anime_id}/hls/{episode}?source={source}&asset={token}&st={segment_token}"

    lines = []
    for line in text.lstrip("\ufeff").splitlines():
        if line and not line.startswith("#"):
            line = rewrite(line.strip())
        elif line.startswith("#"):
            line = re.sub(r'\bURI="([^"]+)"', lambda match: f'URI="{rewrite(match[1])}"', line)
        lines.append(line)
    return "\n".join(lines) + "\n", assets


async def relay(
    anime_id: int,
    episode: int,
    source: str,
    asset: Optional[str] = None,
    byte_range: Optional[str] = None,
    request: Optional[Request] = None,
    st: Optional[str] = None,
):
    if not re.fullmatch(r"[a-f0-9]{24}", source) or (asset and not re.fullmatch(r"[a-f0-9]{24}", asset)):
        raise HTTPException(422, "رابط تشغيل غير صالح")

    client_ip = "127.0.0.1"
    user_agent = ""

    if request:
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

        identifier = f"{anime_id}:{episode}:{source}"
        valid, reason = security_shield.verify_token(st, identifier, client_ip, user_agent)
        if not valid:
            await security_shield.apply_tarpit(delay=1.0)
            raise HTTPException(403, f"انتهت صلاحية جلسة المشاهدة أو الرمز غير صالح ({reason})")

    selected = await playback_sources.get(anime_id, episode, source)
    if not selected or selected.get("type") != "hls":
        raise HTTPException(404, "مصدر الفيديو غير متاح")

    url = await db_cache.get(f"hls-asset:{source}:{asset}") if asset else selected["url"]
    if not isinstance(url, str) or not trusted_media(url):
        raise HTTPException(404, "جزء الفيديو غير متاح")

    client = get_video_client()
    headers = {"Accept-Encoding": "identity"}
    if byte_range:
        if not re.fullmatch(r"bytes=[0-9]+-[0-9]*", byte_range):
            raise HTTPException(416, "نطاق الفيديو غير صالح")
        headers["Range"] = byte_range

    upstream = None
    try:
        upstream = await client.send(client.build_request("GET", url, headers=headers), stream=True)
        if upstream.status_code not in (200, 206) or not trusted_media(str(upstream.url)):
            await upstream.aclose()
            raise HTTPException(502, "تعذّر جلب جزء الفيديو من المصدر")

        mime = upstream.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        if urlsplit(url).path.endswith(".m3u8") or "mpegurl" in mime.lower():
            body = await upstream.aread()
            await upstream.aclose()
            if len(body) > 2 * 1024 * 1024 or not body.lstrip(b"\xef\xbb\xbf \r\n").startswith(b"#EXTM3U"):
                raise HTTPException(502, "قائمة الفيديو غير صالحة")
            text, assets = rewrite_playlist(
                body.decode("utf-8"),
                url,
                anime_id,
                episode,
                source,
                client_ip=client_ip,
                user_agent=user_agent,
            )
            await db_cache.set_many(assets, playback_sources.TTL)
            return Response(
                text,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
            )
    except (httpx.HTTPError, UnicodeError) as error:
        if upstream is not None:
            await upstream.aclose()
        raise HTTPException(502, "تعذّر الاتصال بمصدر الفيديو") from error

    async def chunks():
        try:
            async for chunk in upstream.aiter_bytes(64 * 1024):
                yield chunk
        finally:
            await upstream.aclose()

    result_headers = {
        key: upstream.headers[key]
        for key in ("Content-Length", "Content-Range", "Accept-Ranges")
        if key in upstream.headers
    }
    result_headers.update({
        "Cache-Control": "no-store",
        "Content-Encoding": "identity",
        "X-Content-Type-Options": "nosniff",
    })
    return StreamingResponse(
        chunks(),
        status_code=upstream.status_code,
        media_type=mime,
        headers=result_headers,
    )
