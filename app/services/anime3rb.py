"""Read public title/episode pages and their explicitly free player sources."""
import asyncio
import html
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit

import httpx

from app.services.animetna import SourcePage, playable_mp4
from app.services.db_cache import db_cache

BASE = "https://anime3rb.com"
_titles = {}
_mal_by_slug = {}
_MAL_CACHE_TTL = 30 * 86400


def slug(title):
    title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", title).strip("-")


async def episode_index(client, id_mal, english, romaji):
    cache_key = id_mal or english or romaji
    if not cache_key:
        return {}
    cached = _titles.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    names = []
    for title in (romaji, english):
        if not title:
            continue
        names.append(slug(title))
        base = re.split(r"[:–—\-\|\/]", title)[0].strip()
        if base and base != title:
            names.append(slug(base))
        clean = re.sub(r"\b(pilot\s*film|movie|special|season\s*\d+|part\s*\d+)\b", "", title, flags=re.I).strip()
        if clean and clean != title:
            names.append(slug(clean))
        numbered = re.fullmatch(r"(.+?)\s+([2-9])", title)
        if numbered:
            number = int(numbered[2])
            suffix = {2: "nd", 3: "rd"}.get(number, "th")
            names.append(slug(f"{numbered[1]} {number}{suffix} Season"))
            names.append(slug(f"{numbered[1]} Season {number}"))
    for name in dict.fromkeys([n for n in names if n]):
        try:
            response = await client.get(f"{BASE}/titles/{name}", timeout=8)
            response.raise_for_status()
            mal_ids_on_page = re.findall(r"myanimelist\.net/anime/(\d+)", response.text)
            if id_mal and mal_ids_on_page:
                if str(id_mal) not in mal_ids_on_page:
                    continue
            elif not (name in response.url.path):
                continue
            episodes = {}
            for url, _ in SourcePage(response.text).links:
                parsed = urlsplit(url)
                match = re.fullmatch(r"/episode/[^/]+/([0-9]+)", parsed.path)
                if parsed.scheme == "https" and parsed.hostname == "anime3rb.com" and match:
                    episodes[int(match[1])] = url
            _titles[cache_key] = (time.monotonic() + 1800, episodes)
            return episodes
        except httpx.HTTPError:
            continue
    return cached[1] if cached else {}


async def resolve_sources(client, id_mal, english, romaji, episode):
    try:
        episodes = await episode_index(client, id_mal, english, romaji)
        url = episodes.get(episode)
        if not url:
            return []
        response = await client.get(url, timeout=8)
        response.raise_for_status()
        player_urls = []
        for raw in re.findall(r'wire:snapshot="([^"]+)"', response.text):
            state = json.loads(html.unescape(raw))
            data = state.get("data", {}) if isinstance(state, dict) else {}
            if not isinstance(data, dict):
                continue
            player = data.get("video_url")
            if not isinstance(player, str):
                continue
            if data.get("video_number") is not None and str(data["video_number"]) != str(episode):
                continue
            parsed = urlsplit(player)
            if parsed.scheme == "https" and parsed.hostname == "video.vid3rb.com" and re.fullmatch(r"/player/[a-f0-9-]+", parsed.path):
                player_urls.append(player)
        sources = []
        for player in dict.fromkeys(player_urls):
            response = await client.get(player, timeout=8)
            response.raise_for_status()
            for array in re.findall(r"var video_sources\s*=\s*(\[.*?\]);", response.text, re.S):
                for source in json.loads(array):
                    media = urlsplit(source.get("src", ""))
                    # Premium sources and rate/signature parameters are never altered.
                    if source.get("premium") is False and media.scheme == "https" and media.hostname == "video.vid3rb.com" and media.path.startswith("/video/"):
                        sources.append(source)
        async def check(source):
            try:
                if await playable_mp4(client, source["src"]):
                    return {"name": f"Anime3rb — {source.get('label', 'MP4')}", "type": "direct", "url": source["src"]}
            except httpx.HTTPError:
                pass
            return None
        checked = await asyncio.gather(*(check(source) for source in sources))
        return [source for source in checked if source]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return []


async def latest_episodes(client: httpx.AsyncClient, limit: int = 24) -> list[dict]:
    """Read recently published series episodes from anime3rb's homepage."""
    try:
        response = await client.get(f"{BASE}/", timeout=10, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    discovered = []
    seen_slugs = set()
    for raw, _ in SourcePage(response.text).links:
        parsed = urlsplit(raw)
        if parsed.scheme and parsed.hostname not in (None, "anime3rb.com"):
            continue
        match = re.fullmatch(r"/episode/([^/]+)/([0-9]+)", parsed.path)
        if not match:
            continue
        title_slug, ep_num = match[1], int(match[2])
        if title_slug in seen_slugs:
            continue
        seen_slugs.add(title_slug)
        discovered.append((title_slug, ep_num, f"{BASE}{parsed.path}"))
        if len(discovered) >= limit:
            break

    semaphore = asyncio.Semaphore(8)

    async def resolve_mal(title_slug: str) -> int | None:
        if title_slug in _mal_by_slug:
            return _mal_by_slug[title_slug]
        cached = await db_cache.get(f"mal-map:anime3rb:{title_slug}")
        if isinstance(cached, int) and cached > 0:
            _mal_by_slug[title_slug] = cached
            return cached
        async with semaphore:
            try:
                page = await client.get(f"{BASE}/titles/{title_slug}", timeout=5, follow_redirects=True)
                page.raise_for_status()
                mal = re.search(r"myanimelist\.net/anime/(\d+)", page.text)
                if mal:
                    mal_id = int(mal[1])
                    _mal_by_slug[title_slug] = mal_id
                    await db_cache.set(f"mal-map:anime3rb:{title_slug}", mal_id, _MAL_CACHE_TTL)
                    return mal_id
            except (httpx.HTTPError, ValueError):
                return None
        return None

    mal_ids = await asyncio.gather(*(resolve_mal(slug_name) for slug_name, _, _ in discovered))
    entries = []
    for order, ((_, ep_num, ep_url), mal_id) in enumerate(zip(discovered, mal_ids)):
        if mal_id:
            entries.append({
                "mal": mal_id,
                "episode": ep_num,
                "source": ep_url,
                "order": order,
                "provider": "anime3rb",
            })
    return entries
