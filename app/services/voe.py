"""Read the public VOE player configuration without loading advertising scripts."""
import base64
import codecs
import html
import json
import re
from urllib.parse import urlsplit

import httpx

from app.services.source_trace import record

HOSTS = {"voe.sx", "shannoncoldalready.com"}


def player_config(markup):
    for raw in re.findall(r'<script\b[^>]*type=["\x27]application/json["\x27][^>]*>(.*?)</script>', markup, re.S):
        try:
            payload = json.loads(html.unescape(raw))
            if not isinstance(payload, list) or not payload or not isinstance(payload[0], str):
                continue
            encoded = re.sub(r"@\$|\^\^|~@|%\?|\*~|!!|#&", "", codecs.decode(payload[0], "rot_13"))
            text = base64.b64decode(encoded, validate=True).decode()
            text = "".join(chr(ord(char) - 3) for char in text)[::-1]
            data = json.loads(base64.b64decode(text, validate=True).decode())
            if isinstance(data, dict) and isinstance(data.get("source"), str):
                return data
        except (ValueError, TypeError, UnicodeError):
            continue
    return None


async def resolve(client, url, name):
    from app.services.animetna import playable_hls
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in HOSTS or not re.fullmatch(r"/e/[a-z0-9]+", parsed.path):
        return None
    try:
        response = await client.get(url, timeout=7, follow_redirects=True)
        response.raise_for_status()
        if response.url.host not in HOSTS:
            return None
        # The host publishes a client-side redirect to its current player domain.
        redirect = re.search(r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", response.text)
        if redirect:
            target = urlsplit(redirect[1])
            if target.scheme != "https" or target.hostname not in HOSTS or target.path != parsed.path:
                return None
            response = await client.get(target.geturl(), timeout=7)
            response.raise_for_status()
        config = player_config(response.text)
        if not config:
            record("missing_player_config", url)
            return None
        if config.get("file_code") and config["file_code"] != parsed.path.rsplit("/", 1)[-1]:
            return None
        # Use the published playback playlist. Download-only URLs are not used.
        media = config["source"]
        info = {}
        if await playable_hls(client, media, info):
            if info.get("height"):
                quality = f"{info['height']}p"
                name = re.sub(r"(?:2160|1080|720|480|360)p(?: \(source\))?", quality, name) if re.search(r"[0-9]+p", name) else f"{name} — {quality}"
            return {"name": name, "type": "hls", "url": media, "source_url": url}
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    return None
