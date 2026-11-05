import asyncio
import html
import json
import logging
import re
import time
import unicodedata
from urllib.parse import urlsplit

import httpx
from app.services.animetna import (
    SHARE4MAX_HOSTS,
    MP4UPLOAD_HOSTS,
    VOE_HOSTS,
    MIXDROP_HOSTS,
    validate_embed,
    resolve_player,
    collect_available,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://anime4up.uno"
_anime_cache = {}
_episode_cache = {}


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


async def get_anime4up_episodes(client: httpx.AsyncClient, id_mal: int | None, english: str | None, romaji: str | None) -> dict[int, str]:
    if not id_mal:
        return {}

    cache_key = id_mal or english or romaji
    cached = _anime_cache.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    titles = [t for t in (english, romaji) if t]
    slugs = []
    for t in titles:
        slugs.append(slug(t))
        no_year = re.sub(r"\s*\(\d{4}\)", "", t).strip()
        if no_year != t:
            slugs.append(slug(no_year))

    for s in dict.fromkeys(slugs):
        url = f"{BASE_URL}/anime/{s}/"
        try:
            resp = await client.get(url, timeout=7, follow_redirects=True)
            if resp.status_code == 200:
                if not re.search(rf"https?://(?:www\.)?myanimelist\.net/anime/{id_mal}(?![0-9])", resp.text):
                    continue
                match = re.search(r'\[\{"url":"/episode/[^"]+",.*?"title":[^}]+\}\]', resp.text)
                if match:
                    episodes_raw = json.loads(match.group(0))
                    ep_map = {}
                    for item in episodes_raw:
                        num = item.get("number")
                        ep_url = item.get("url")
                        if num and ep_url:
                            try:
                                ep_map[int(num)] = f"{BASE_URL}{ep_url}"
                            except ValueError:
                                pass
                    if ep_map:
                        _anime_cache[cache_key] = (time.monotonic() + 3600, ep_map)
                        return ep_map
        except httpx.HTTPError:
            continue

    return {}


async def resolve_sources(client: httpx.AsyncClient, id_mal: int | None, english: str | None, romaji: str | None, episode: int) -> list[dict]:
    ep_map = await get_anime4up_episodes(client, id_mal, english, romaji)
    ep_url = ep_map.get(episode)
    if not ep_url:
        return []

    try:
        resp = await client.get(ep_url, timeout=7, follow_redirects=True)
        resp.raise_for_status()

        raw_servers = re.findall(r'data-[a-z0-9_-]*(?:src|url|server)="([^"]*)"', resp.text, re.I)
        options = []
        seen = set()

        for s_url in raw_servers:
            s_url = html.unescape(s_url).strip()
            if s_url.startswith("//"):
                s_url = "https:" + s_url
            if not s_url.startswith("https://") or s_url in seen:
                continue
            seen.add(s_url)

            host = urlsplit(s_url).hostname or ""
            if host in SHARE4MAX_HOSTS:
                options.append({"name": "سيرفر رئيسي FHD", "type": "embed", "url": s_url})
            elif host in MP4UPLOAD_HOSTS:
                options.append({"name": "Mp4upload", "type": "embed", "url": s_url})
            elif host in VOE_HOSTS | MIXDROP_HOSTS:
                options.append({"name": "VOE" if host in VOE_HOSTS else "Mixdrop", "type": "embed", "url": s_url})
            elif "mega.nz" in host:
                options.append({"name": "سيرفر ميجا", "type": "embed", "url": s_url})
            elif "ok.ru" in host:
                options.append({"name": "OK.ru", "type": "embed", "url": s_url})
            elif any(d in host for d in ("4shared.com", "uptostream.com", "dsvplay.com", "vadbam.com")):
                name = host.split(".")[0].capitalize()
                options.append({"name": name, "type": "embed", "url": s_url})

        checked = await collect_available((resolve_player(client, opt, id_mal, episode) for opt in options))
        results = [s for group in checked if isinstance(group, list) for s in group if s]
        return results

    except httpx.HTTPError as exc:
        logger.debug("Failed resolving Anime4up sources for episode %d: %s", episode, exc)
        return []

