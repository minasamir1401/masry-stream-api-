"""Relay only playlist-discovered HLS assets through the site's own origin."""
import hashlib
import re
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from app.services.animetna import HLS_MEDIA_SUFFIXES
from app.services.db_cache import db_cache
from app.services.playback_sources import playback_sources
from app.services.video_http import get_video_client


def trusted_media(url):
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and (parsed.hostname or "").endswith(HLS_MEDIA_SUFFIXES) and parsed.port in (None, 443)
    except ValueError:
        return False


def rewrite_playlist(text, upstream_url, anime_id, episode, source):
    assets = {}
    def rewrite(uri):
        url = urljoin(upstream_url, uri)
        if not trusted_media(url):
            raise HTTPException(502, "رابط غير صالح داخل قائمة الفيديو")
        token = hashlib.sha256(url.encode()).hexdigest()[:24]
        assets[f"hls-asset:{source}:{token}"] = url
        return f"/api/anime/{anime_id}/hls/{episode}?source={source}&asset={token}"
    lines = []
    for line in text.lstrip("\ufeff").splitlines():
        if line and not line.startswith("#"):
            line = rewrite(line.strip())
        elif line.startswith("#"):
            line = re.sub(r'\bURI="([^"]+)"', lambda match: f'URI="{rewrite(match[1])}"', line)
        lines.append(line)
    return "\n".join(lines) + "\n", assets


async def relay(anime_id, episode, source, asset=None, byte_range=None):
    if not re.fullmatch(r"[a-f0-9]{24}", source) or (asset and not re.fullmatch(r"[a-f0-9]{24}", asset)):
        raise HTTPException(422, "رابط تشغيل غير صالح")
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
            text, assets = rewrite_playlist(body.decode("utf-8"), url, anime_id, episode, source)
            await db_cache.set_many(assets, playback_sources.TTL)
            return Response(text, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-store"})
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
    result_headers = {key: upstream.headers[key] for key in ("Content-Length", "Content-Range", "Accept-Ranges") if key in upstream.headers}
    result_headers.update({"Cache-Control": "no-store", "Content-Encoding": "identity"})
    return StreamingResponse(chunks(), status_code=upstream.status_code, media_type=mime, headers=result_headers)
