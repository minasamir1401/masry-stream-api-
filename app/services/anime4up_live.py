"""Read the current publisher instead of relying only on its archived mirror."""
import html
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.animetna import (SourcePage, collect_available, is_anime4up_host,
    resolve_player, DIRECT_PLAYER_HOSTS, SHARE4MAX_HOSTS, VOE_HOSTS, MIXDROP_HOSTS, VIDARA_HOSTS)
from app.services.db_cache import db_cache

BASE = "https://anime4up.rest"
_origin = None
_indexes = {}


def publisher_host(host):
    return is_anime4up_host(host) or host in {"anime4up.rest", "www.anime4up.rest", "w1.anime4up.rest", "ww.anime4up.rest"}


def episode_links(markup, base):
    links = {}
    for raw, label in SourcePage(markup).links:
        url = urlsplit(urljoin(base, raw))
        if not publisher_host(url.hostname) or not url.path.startswith("/episode/"):
            continue
        canonical = url._replace(query="", fragment="").geturl()
        links[canonical] = links.get(canonical, "") + " " + label
    episodes = {}
    for url, label in links.items():
        number = re.search(r"(?:الحلقة|حلقة|الفلم|فيلم|Episode)\s*([0-9]+)(?![0-9])", label, re.I)
        if number:
            episodes[int(number[1])] = url
        elif len(links) == 1:
            episodes[1] = url
    return episodes


async def origin(client):
    global _origin
    if _origin and _origin[0] > time.monotonic():
        return _origin[1]
    response = await client.get(BASE + "/", timeout=7, follow_redirects=True)
    response.raise_for_status()
    if response.url.scheme != "https" or not publisher_host(response.url.host):
        raise ValueError("Unexpected publisher domain")
    value = f"https://{response.url.host}"
    _origin = (time.monotonic() + 3600, value)
    return value


async def episode_index(client, id_mal, english, romaji):
    cache_key = id_mal or english or romaji
    if not cache_key:
        return {}
    cached = _indexes.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    saved = await db_cache.get(f"source-index:anime4up-live:{cache_key}")
    if saved:
        value = {int(number): url for number, url in saved.items()}
        _indexes[cache_key] = (time.monotonic() + 3600, value)
        return value
    try:
        base = await origin(client)
        raw_titles = [t for t in (romaji, english) if t]
        titles = list(dict.fromkeys(raw_titles + [re.split(r"[:–—\-\|\/]", t)[0].strip() for t in raw_titles if t]))
        expected = {re.sub(r"\W+", "", title.casefold()) for title in titles if title}
        candidates = {}
        for title in titles:
            if not title:
                continue
            response = await client.get(base + "/", params={"s": title}, timeout=7)
            response.raise_for_status()
            for raw, label in SourcePage(response.text).links:
                url = urlsplit(urljoin(base, raw))
                if publisher_host(url.hostname) and url.path.startswith("/anime/"):
                    candidates[url._replace(query="", fragment="").geturl()] = label
            if candidates:
                break
        ranked = sorted(candidates, key=lambda url: re.sub(r"\W+", "", candidates[url].casefold()) not in expected)
        for url in ranked[:5]:
            response = await client.get(url, timeout=7)
            response.raise_for_status()
            is_mal_match = bool(id_mal and re.search(rf"https?://(?:www\.)?myanimelist\.net/anime/{id_mal}(?![0-9])", response.text))
            is_title_match = re.sub(r"\W+", "", candidates[url].casefold()) in expected
            if not is_mal_match and not is_title_match:
                continue
            episodes = episode_links(response.text, str(response.url))
            if episodes:
                _indexes[cache_key] = (time.monotonic() + 3600, episodes)
                await db_cache.set(f"source-index:anime4up-live:{cache_key}", episodes, 3600)
                return episodes
    except (httpx.HTTPError, ValueError):
        pass
    return cached[1] if cached else {}


async def resolve_sources(client, id_mal, english, romaji, episode):
    episodes = await episode_index(client, id_mal, english, romaji)
    url = episodes.get(episode)
    if not url or not publisher_host(urlsplit(url).hostname):
        return []
    try:
        response = await client.get(url, timeout=7)
        response.raise_for_status()
        options = []
        seen = set()
        for raw in re.findall(r'data-watch=["\x27]([^"\x27]+)', response.text):
            link = html.unescape(raw)
            host = urlsplit(link).hostname
            if link in seen or not link.startswith("https://"):
                continue
            seen.add(link)
            if host in DIRECT_PLAYER_HOSTS | SHARE4MAX_HOSTS | VOE_HOSTS | MIXDROP_HOSTS | VIDARA_HOSTS or is_anime4up_host(host):
                options.append({"name": (host or "").removeprefix("www."), "type": "embed", "url": link})
        results = await collect_available((resolve_player(client, option, id_mal, episode) for option in options))
        return [server for group in results if isinstance(group, list) for server in group if server.get("type") in ("direct", "hls", "embed")]
    except (httpx.HTTPError, ValueError, TypeError):
        return []
