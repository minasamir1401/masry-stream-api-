"""Read the current publisher instead of relying only on its archived mirror."""
import asyncio
import html
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

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
            mal_ids_on_page = re.findall(r"myanimelist\.net/anime/(\d+)", response.text)
            if id_mal and mal_ids_on_page:
                if str(id_mal) not in mal_ids_on_page:
                    continue
            elif re.sub(r"\W+", "", candidates[url].casefold()) not in expected:
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


def _anime_slug_from_episode_path(path: str) -> str:
    decoded = unquote(path).strip("/")
    segment = decoded.split("/")[-1]
    segment = re.sub(r"^(?:انمي|فيلم|فلم)-", "", segment)
    segment = re.sub(r"-(?:الحلقة|حلقة|الأونا|اونا|الأوفا|اوفا|الفلم|فيلم|الخاصة|خاصة)-[0-9]+.*$", "", segment)
    return re.sub(r"[^a-z0-9-]+", "-", segment.lower()).strip("-")


async def latest_episodes(client: httpx.AsyncClient, limit: int = 24) -> list[dict]:
    """Read recently published episodes from the active anime4up publisher."""
    try:
        base = await origin(client)
        response = await client.get(f"{base}/", timeout=10, follow_redirects=True)
        response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        return []

    discovered = []
    seen_slugs = set()
    for raw, label in SourcePage(response.text).links:
        parsed = urlsplit(urljoin(base, raw))
        if not publisher_host(parsed.hostname) or not parsed.path.startswith("/episode/"):
            continue
        number = re.search(r"(?:الحلقة|حلقة|الأونا|الأوفا|الفلم|فيلم|Episode)\s*([0-9]+)(?![0-9])", label, re.I)
        if not number:
            number = re.search(r"-([0-9]+)-(?:مترجمة|مترجم|اون-لاين)/?$", unquote(parsed.path))
        if not number:
            continue
        slug = _anime_slug_from_episode_path(parsed.path)
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        canonical_ep = parsed._replace(query="", fragment="").geturl()
        discovered.append((slug, int(number[1]), canonical_ep))
        if len(discovered) >= limit:
            break

    semaphore = asyncio.Semaphore(8)

    async def resolve_mal(slug: str, ep_url: str) -> int | None:
        cache_key = f"mal-map:anime4up:{slug}"
        cached = await db_cache.get(cache_key)
        if isinstance(cached, int) and cached > 0:
            return cached
        async with semaphore:
            direct_url = f"{base}/anime/{slug}/"
            try:
                direct_resp = await client.get(direct_url, timeout=4, follow_redirects=True)
                if direct_resp.status_code == 200:
                    mal = re.search(r"myanimelist\.net/anime/(\d+)", direct_resp.text)
                    if mal:
                        mal_id = int(mal[1])
                        await db_cache.set(cache_key, mal_id, 30 * 86400)
                        return mal_id
            except httpx.HTTPError:
                pass
            candidate_paths = []
            try:
                ep_resp = await client.get(ep_url, timeout=4, follow_redirects=True)
                if ep_resp.status_code == 200:
                    mal = re.search(r"myanimelist\.net/anime/(\d+)", ep_resp.text)
                    if mal:
                        mal_id = int(mal[1])
                        await db_cache.set(cache_key, mal_id, 30 * 86400)
                        return mal_id
                    for raw_link, _ in SourcePage(ep_resp.text).links:
                        u = urlsplit(urljoin(base, raw_link))
                        if publisher_host(u.hostname) and u.path.startswith("/anime/"):
                            url_clean = u._replace(query="", fragment="").geturl()
                            if url_clean != direct_url:
                                candidate_paths.append(url_clean)
                            break
            except httpx.HTTPError:
                pass
            for anime_url in dict.fromkeys(candidate_paths):
                try:
                    anime_resp = await client.get(anime_url, timeout=4, follow_redirects=True)
                    if anime_resp.status_code == 200:
                        mal = re.search(r"myanimelist\.net/anime/(\d+)", anime_resp.text)
                        if mal:
                            mal_id = int(mal[1])
                            await db_cache.set(cache_key, mal_id, 30 * 86400)
                            return mal_id
                except httpx.HTTPError:
                    continue
        return None

    mal_ids = await asyncio.gather(*(resolve_mal(slug, ep_url) for slug, _, ep_url in discovered))
    entries = []
    for order, ((_, ep_num, ep_url), mal_id) in enumerate(zip(discovered, mal_ids)):
        if mal_id:
            entries.append({
                "mal": mal_id,
                "episode": ep_num,
                "source": ep_url,
                "order": order,
                "provider": "anime4up_live",
            })
    return entries
