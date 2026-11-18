from datetime import datetime
import html
import json
import logging
import re
import asyncio
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from app.services.seo_catalog import released_count
from app.services.title_localization import (
    compact_search,
    english_query_for_ids,
    has_arabic,
    localize_payload,
    normalize_search,
    provider_filters,
    public_titles,
)

from app.services.anime_resolver import anime_stream_resolver
from app.services.animetna import latest_catalog, resolve_episode_page, get_episode_index
from app.services.anime3rb import (
    episode_index as anime3rb_episode_index,
    resolve_sources as anime3rb_resolve_sources,
)
from app.services.anime4up import get_anime4up_episodes
from app.services.anime4up_live import (
    episode_index as live_episode_index,
    publisher_host as is_live_anime4up_host,
    resolve_sources as live_resolve_sources,
)
from app.services.db_cache import db_cache, TTL_ANIME_DETAIL, TTL_LIST, TTL_HOMEPAGE, TTL_STREAM

logger = logging.getLogger(__name__)

_shared_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()
_loop_semaphores: Dict[int, asyncio.Semaphore] = {}


def _anilist_semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    sem = _loop_semaphores.get(loop_id)
    if sem is None:
        if len(_loop_semaphores) > 8:
            _loop_semaphores.clear()
        sem = asyncio.Semaphore(3)
        _loop_semaphores[loop_id] = sem
    return sem


async def _get_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        async with _client_lock:
            if _shared_client is None or _shared_client.is_closed:
                _shared_client = httpx.AsyncClient(
                    timeout=10.0,
                    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
                    headers={"User-Agent": "AnimeClover/1.0", "Accept-Encoding": "gzip"},
                )
    return _shared_client

ANILIST_API_URL = "https://graphql.anilist.co"

ANIME_LIST_QUERY = """
query ($page: Int, $perPage: Int, $search: String, $ids: [Int], $sort: [MediaSort], $format: MediaFormat, $genre: String, $season: MediaSeason, $seasonYear: Int, $status: MediaStatus, $status_in: [MediaStatus], $startDate_greater: FuzzyDateInt, $startDate_lesser: FuzzyDateInt) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      currentPage
      lastPage
      hasNextPage
      perPage
    }
    media(type: ANIME, search: $search, id_in: $ids, sort: $sort, format: $format, genre: $genre, season: $season, seasonYear: $seasonYear, status: $status, status_in: $status_in, startDate_greater: $startDate_greater, startDate_lesser: $startDate_lesser, isAdult: false) {
      id
      idMal
      title {
        romaji
        english
        native
      }
      synonyms
      description(asHtml: false)
      startDate {
        year
      }
      episodes
      duration
      status
      format
      nextAiringEpisode { episode airingAt }
      genres
      averageScore
      bannerImage
      coverImage {
        extraLarge
        large
        medium
      }
      trailer {
        id
        site
      }
    }
  }
}
"""

ANIME_DETAIL_QUERY = """
query ($id: Int) {
  Media(id: $id, type: ANIME) {
    id
    idMal
    title {
      romaji
      english
      native
    }
    synonyms
    description(asHtml: false)
    startDate {
      year
    }
    episodes
    duration
    status
    format
    nextAiringEpisode { episode airingAt }
    genres
    averageScore
    bannerImage
    coverImage {
      extraLarge
      large
      medium
    }
    trailer {
      id
      site
    }
  }
}
"""


class AnimeService:
    _MAX_CACHE_SIZE = 500
    _MAX_LIST_CACHE = 100

    def __init__(self) -> None:
        self._anime_cache: Dict[int, Dict[str, Any]] = {}
        self._anime_cache_expires = {}
        self._home_cache = None
        self._home_lock = asyncio.Lock()
        self._list_cache = {}
        self._list_inflight: Dict[tuple, asyncio.Task] = {}

    @staticmethod
    def _format_media(item: Dict[str, Any]) -> Dict[str, Any]:
        titles = item.get("title") or {}
        display_title = titles.get("english") or titles.get("romaji") or titles.get("native") or "عنوان غير محدد"
        cover = item.get("coverImage") or {}
        poster = cover.get("extraLarge") or cover.get("large") or cover.get("medium")
        banner = item.get("bannerImage") or poster
        description = html.unescape(item.get("description") or "لا يوجد وصف متاح لهذا العمل حالياً.")
        description = re.sub(r"<br\s*/?>|</(?:p|div)>", "\n", description, flags=re.IGNORECASE)
        description = re.sub(r"<[^>]+>", "", description).strip()

        genres_list = [
            {"id": idx + 1, "name": g, "slug": g.lower().replace(" ", "-")}
            for idx, g in enumerate(item.get("genres") or [])
        ]

        next_airing = item.get("nextAiringEpisode") or {}
        episodes_count = item.get("episodes") or max(0, (next_airing.get("episode") or 1) - 1)
        if not episodes_count and item.get("format") == "MOVIE":
            episodes_count = 1
        episodes_data = []
        for ep_num in range(1, episodes_count + 1):
            episodes_data.append({
                "id": ep_num,
                "telegram_message_id": ep_num,
                "telegram_channel": "AnimeClover",
                "direct_streamable": False,
                "title": f"الحلقة {ep_num}",
                "episode_number": ep_num,
                "season_number": 1,
                "duration": (item.get("duration") or 24) * 60,
                "file_size": 0,
                "stream_url": f"/api/anime/{item['id']}/stream/{ep_num}",
            })

        result = {
            "id": item["id"],
            "id_mal": item.get("idMal"),
            "telegram_channel": "AnimeClover",
            "telegram_message_id": item["id"],
            "title": display_title,
            "title_namespace": "anilist",
            "synonyms": item.get("synonyms") or [],
            "title_english": titles.get("english"),
            "title_romaji": titles.get("romaji"),
            "original_title": titles.get("native") or titles.get("romaji"),
            "description": description,
            "release_year": (item.get("startDate") or {}).get("year"),
            "duration": (item.get("duration") or 24) * 60,
            "file_size": 0,
            "mime_type": "video/mp4",
            "poster_url": poster,
            "banner_url": banner,
            "media_type": "SERIES" if item.get("format") in ("TV", "TV_SHORT", "ONA") or (item.get("episodes") or 1) > 1 else "MOVIE",
            "total_episodes": item.get("episodes"),
            "next_airing_episode": next_airing.get("episode"),
            "next_airing_at": next_airing.get("airingAt"),
            "status": item.get("status"),
            "average_score": item.get("averageScore"),
            "popularity": item.get("popularity") or 0,
            "is_available": False,
            "direct_streamable": False,
            "view_count": (item.get("averageScore") or 75) * 10,
            "created_at": datetime.now().isoformat(),
            "genres": genres_list,
            "episodes": episodes_data,
            "stream_url": f"/api/anime/{item['id']}/stream/1",
        }
        return localize_payload(result)

    async def _post_anilist(self, client: httpx.AsyncClient, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        sem = _anilist_semaphore()
        last_error: Optional[Exception] = None
        for attempt in range(3):
            async with sem:
                try:
                    resp = await client.post(
                        ANILIST_API_URL,
                        json={"query": query, "variables": variables},
                        headers={"Content-Type": "application/json"},
                    )
                except httpx.HTTPError as exc:
                    last_error = exc
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue

            if resp.status_code == 429:
                retry_after = min(2.0, float(resp.headers.get("Retry-After") or "1.0"))
                last_error = httpx.HTTPStatusError("AniList rate limited", request=resp.request, response=resp)
                await asyncio.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                last_error = httpx.HTTPStatusError("AniList upstream error", request=resp.request, response=resp)
                await asyncio.sleep(0.4 * (attempt + 1))
                continue

            resp.raise_for_status()
            payload = resp.json()
            if payload.get("errors"):
                raise ValueError(f"AniList returned GraphQL error: {payload['errors']}")
            return payload

        if last_error is not None:
            raise last_error
        raise RuntimeError("AniList request failed")

    async def _local_catalog_fallback(self, variables: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Filter persisted CatalogEntry rows when upstream AniList is unavailable."""
        from sqlalchemy import select
        from app.database import AsyncSessionLocal
        from app.models import CatalogEntry

        try:
            async with AsyncSessionLocal() as session:
                rows = (await session.execute(select(CatalogEntry.payload))).scalars().all()
        except Exception:
            return None
        if not rows:
            return None

        ids_filter = set(variables.get("ids") or [])
        search_raw = str(variables.get("search") or "").strip()
        search_norm = normalize_search(search_raw)
        search_comp = compact_search(search_raw)
        genre_filter = (variables.get("genre") or "").casefold()
        season_year = variables.get("seasonYear")
        start_gt = variables.get("startDate_greater")
        start_lt = variables.get("startDate_lesser")
        status_filter = variables.get("status")
        status_in = set(variables.get("status_in") or [])
        format_filter = variables.get("format")

        matched: List[Dict[str, Any]] = []
        for raw_payload in rows:
            try:
                item = json.loads(raw_payload)
            except (ValueError, TypeError):
                continue
            aid = item.get("id")
            if not aid:
                continue
            if ids_filter and aid not in ids_filter:
                continue
            if search_norm:
                candidates = [
                    item.get("title"),
                    item.get("title_english"),
                    item.get("title_ar"),
                    *(item.get("title_aliases") or []),
                ]
                if not any(
                    search_norm in normalize_search(c)
                    or (len(search_comp) >= 3 and search_comp in compact_search(c))
                    for c in candidates
                    if c
                ):
                    continue
            if genre_filter:
                genres = [str(g.get("name") or "").casefold() for g in (item.get("genres") or []) if isinstance(g, dict)]
                if genre_filter not in genres:
                    continue
            year_val = item.get("release_year")
            if season_year and year_val != season_year:
                continue
            if start_gt is not None or start_lt is not None:
                if not year_val:
                    continue
                y_min = (start_gt // 10000) + 1 if start_gt is not None else 0
                y_max = (start_lt - 1) // 10000 if start_lt is not None else 9999
                if not (y_min <= year_val <= y_max):
                    continue
            if status_filter and item.get("status") != status_filter:
                continue
            if status_in and item.get("status") not in status_in:
                continue
            if format_filter:
                mt = item.get("media_type")
                if format_filter == "MOVIE" and mt != "MOVIE":
                    continue
                if format_filter in ("TV", "TV_SHORT", "ONA") and mt != "SERIES":
                    continue
            matched.append({**item, "episodes": []})

        sort_list = variables.get("sort") or ["POPULARITY_DESC"]
        primary_sort = sort_list[0] if isinstance(sort_list, list) and sort_list else "POPULARITY_DESC"
        if primary_sort == "SCORE_DESC":
            matched.sort(key=lambda x: (x.get("average_score") or 0, x.get("popularity") or 0), reverse=True)
        elif primary_sort == "START_DATE_DESC":
            matched.sort(key=lambda x: (x.get("release_year") or 0, x.get("popularity") or 0), reverse=True)
        elif primary_sort == "TITLE_ROMAJI":
            matched.sort(key=lambda x: str(x.get("title_english") or x.get("title") or "").casefold())
        elif primary_sort == "EPISODES_DESC":
            matched.sort(key=lambda x: (x.get("total_episodes") or 0, x.get("popularity") or 0), reverse=True)
        else:
            matched.sort(key=lambda x: (x.get("popularity") or 0, x.get("average_score") or 0), reverse=True)

        page = max(1, int(variables.get("page") or 1))
        per_page = max(1, int(variables.get("perPage") or 24))
        total = len(matched)
        start = (page - 1) * per_page
        page_items = matched[start:start + per_page]
        total_pages = max(1, (total + per_page - 1) // per_page)
        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": per_page,
            "total_pages": total_pages,
            "has_next_page": page < total_pages,
        }

    async def _fetch_list_uncached(self, variables: Dict[str, Any], db_key: str, mem_key: str, raw_search: Optional[str] = None) -> Dict[str, Any]:
        client = await _get_client()
        try:
            payload = await self._post_anilist(client, ANIME_LIST_QUERY, variables)
            data = payload.get("data", {}).get("Page", {})
            media_list = list(data.get("media", []))
            page_info = data.get("pageInfo", {})

            # Expand single-franchise curated ID hits with AniList text search so
            # sequels/seasons/movies and year+search filters work seamlessly.
            ids = variables.get("ids")
            if ids and 1 <= len(ids) <= 3 and "search" not in variables and variables.get("page", 1) == 1:
                franchise_en = english_query_for_ids(ids)
                if franchise_en:
                    expanded_vars = {k: v for k, v in variables.items() if k != "ids"}
                    expanded_vars["search"] = franchise_en
                    if expanded_vars.get("sort") == ["POPULARITY_DESC"]:
                        expanded_vars["sort"] = ["SEARCH_MATCH", "POPULARITY_DESC"]
                    try:
                        extra_payload = await self._post_anilist(client, ANIME_LIST_QUERY, expanded_vars)
                        extra_page = extra_payload.get("data", {}).get("Page", {})
                        extra_media = extra_page.get("media", [])
                        seen_ids = {m["id"] for m in media_list}
                        for m in extra_media:
                            if m["id"] not in seen_ids:
                                seen_ids.add(m["id"])
                                media_list.append(m)
                        per_page = int(variables.get("perPage") or 24)
                        media_list = media_list[:per_page]
                        extra_info = extra_page.get("pageInfo") or {}
                        if len(media_list) > len(data.get("media", [])):
                            page_info = {
                                **page_info,
                                "total": max(int(page_info.get("total") or 0), int(extra_info.get("total") or len(media_list))),
                                "lastPage": max(int(page_info.get("lastPage") or 1), int(extra_info.get("lastPage") or 1)),
                                "hasNextPage": bool(page_info.get("hasNextPage") or extra_info.get("hasNextPage")),
                            }
                    except Exception:
                        pass

            if not media_list and raw_search and has_arabic(raw_search):
                local = await self._local_catalog_fallback({**variables, "ids": None, "search": raw_search})
                if local and local.get("items"):
                    return local

            items = [self._format_media(m) for m in media_list]
            await db_cache.set_many({f"anime:{item['id']}": item for item in items}, TTL_ANIME_DETAIL)
            for it in items:
                if len(self._anime_cache) >= self._MAX_CACHE_SIZE:
                    del self._anime_cache[next(iter(self._anime_cache))]
                self._anime_cache[it["id"]] = it
            full_result = {
                "items": [{**item, "episodes": []} for item in items],
                "total": page_info.get("total", len(items)),
                "page": page_info.get("currentPage", variables.get("page", 1)),
                "page_size": page_info.get("perPage", variables.get("perPage", 24)),
                "total_pages": page_info.get("lastPage", 1),
                "has_next_page": bool(page_info.get("hasNextPage")),
            }
            if len(self._list_cache) >= self._MAX_LIST_CACHE:
                del self._list_cache[next(iter(self._list_cache))]
            self._list_cache[mem_key] = (time.monotonic() + TTL_LIST, full_result)
            await db_cache.set(db_key, full_result, TTL_LIST)
            return full_result
        except Exception:
            stale = await db_cache.get_stale(db_key)
            if stale is not None:
                self._list_cache[mem_key] = (time.monotonic() + 120, stale)
                return stale
            local = await self._local_catalog_fallback(variables)
            if local is not None:
                return local
            raise

    @public_titles
    async def _query_list(self, variables: Dict[str, Any], allow_hidden: bool = False, raw_search: Optional[str] = None) -> Dict[str, Any]:
        from app.services.anime_override_service import anime_override_service
        raw_key = json.dumps(variables, sort_keys=True)
        mem_key = raw_key
        db_key = f"list:{raw_key}"
        hidden_ids = anime_override_service.get_hidden_anime_ids_sync()

        def apply_hidden(payload: Dict[str, Any]) -> Dict[str, Any]:
            if not allow_hidden and hidden_ids and "items" in payload:
                filtered = [it for it in payload["items"] if it.get("id") not in hidden_ids]
                return {**payload, "items": filtered, "total": len(filtered)}
            return payload

        # L1 memory cache
        cached = self._list_cache.get(mem_key)
        if cached and cached[0] > time.monotonic():
            return apply_hidden(cached[1])

        # L2 DB cache — survives restarts and is shared across workers
        db_hit = await db_cache.get(db_key)
        if db_hit is not None:
            if len(self._list_cache) >= self._MAX_LIST_CACHE:
                del self._list_cache[next(iter(self._list_cache))]
            self._list_cache[mem_key] = (time.monotonic() + TTL_LIST, db_hit)
            return apply_hidden(db_hit)

        inflight_key = (id(asyncio.get_running_loop()), mem_key)
        task = self._list_inflight.get(inflight_key)
        if task is None:
            task = asyncio.create_task(self._fetch_list_uncached(variables, db_key, mem_key, raw_search=raw_search))
            self._list_inflight[inflight_key] = task
        try:
            full_result = await task
        finally:
            if self._list_inflight.get(inflight_key) is task:
                self._list_inflight.pop(inflight_key, None)

        return apply_hidden(full_result)

    async def browse(self, page=1, per_page=24, search=None, genre=None, year=None, season=None, status=None, format=None, sort="POPULARITY_DESC", allow_hidden=False):
        sort_value = ["SEARCH_MATCH", "POPULARITY_DESC"] if (search and sort == "POPULARITY_DESC") else [sort]
        variables: Dict[str, Any] = {"page": page, "perPage": per_page, "sort": sort_value}
        if search:
            variables.update(provider_filters(search))
        for key, value in {"genre": genre, "season": season, "status": status, "format": format}.items():
            if value:
                variables[key] = value
        if year:
            if season:
                variables["seasonYear"] = year
            else:
                # Calendar date range captures movies/OVAs/specials lacking seasonYear on AniList.
                variables["startDate_greater"] = year * 10000 - 1
                variables["startDate_lesser"] = (year + 1) * 10000
        if sort == "START_DATE_DESC" and not status:
            now = datetime.now()
            if year is None or year <= now.year:
                today_bound = int(now.strftime("%Y%m%d")) + 1
                variables["status_in"] = ["RELEASING", "FINISHED"]
                if "startDate_lesser" in variables:
                    variables["startDate_lesser"] = min(variables["startDate_lesser"], today_bound)
                else:
                    variables["startDate_lesser"] = today_bound
        if search and has_arabic(search):
            return await self._query_list(variables, allow_hidden=allow_hidden, raw_search=search)
        return await self._query_list(variables, allow_hidden=allow_hidden)

    @public_titles
    async def get_homepage(self):
        from app.services.anime_override_service import anime_override_service
        hidden_ids = anime_override_service.get_hidden_anime_ids_sync()

        # L1 memory check
        if self._home_cache and self._home_cache[0] > time.monotonic():
            data = self._home_cache[1]
            if hidden_ids:
                return {
                    "featured": [i for i in data.get("featured", []) if i.get("id") not in hidden_ids],
                    "spotlight": [i for i in data.get("spotlight", []) if i.get("id") not in hidden_ids],
                    "popular": [i for i in data.get("popular", []) if i.get("id") not in hidden_ids],
                    "recommended": [i for i in data.get("recommended", []) if i.get("id") not in hidden_ids],
                }
            return data

        async with self._home_lock:
            if self._home_cache and self._home_cache[0] > time.monotonic():
                data = self._home_cache[1]
                if hidden_ids:
                    return {
                        "featured": [i for i in data.get("featured", []) if i.get("id") not in hidden_ids],
                        "spotlight": [i for i in data.get("spotlight", []) if i.get("id") not in hidden_ids],
                        "popular": [i for i in data.get("popular", []) if i.get("id") not in hidden_ids],
                        "recommended": [i for i in data.get("recommended", []) if i.get("id") not in hidden_ids],
                    }
                return data

            # L2 DB cache check inside the lock to avoid stampede
            db_hit = await db_cache.get("homepage:v3")
            if db_hit is not None:
                self._home_cache = (time.monotonic() + TTL_HOMEPAGE, db_hit)
                if hidden_ids:
                    return {
                        "featured": [i for i in db_hit.get("featured", []) if i.get("id") not in hidden_ids],
                        "spotlight": [i for i in db_hit.get("spotlight", []) if i.get("id") not in hidden_ids],
                        "popular": [i for i in db_hit.get("popular", []) if i.get("id") not in hidden_ids],
                        "recommended": [i for i in db_hit.get("recommended", []) if i.get("id") not in hidden_ids],
                    }
                return db_hit

            query = """query ($ids: [Int]) { Page(perPage: 50) { media(idMal_in: $ids, type: ANIME, isAdult: false) {
                id idMal title { english romaji native } synonyms description(asHtml: false)
                startDate { year month day } episodes duration status format genres averageScore popularity
                nextAiringEpisode { episode airingAt }
                bannerImage coverImage { extraLarge large medium }
            } } }"""
            try:
                async with httpx.AsyncClient(timeout=15, limits=httpx.Limits(max_connections=30, max_keepalive_connections=10)) as client:
                    entries = await latest_catalog(client)
                    if not entries:
                        raise ValueError("No published catalog entries available")
                    response = await client.post(ANILIST_API_URL, json={"query": query, "variables": {"ids": [entry["mal"] for entry in entries]}})
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("errors"):
                        raise ValueError("Homepage metadata unavailable")
                    raw_items = payload["data"]["Page"]["media"]
                    items = {item["id"]: self._format_media(item) for item in raw_items}
                    by_mal = {item["idMal"]: item for item in raw_items}
                    recent = sorted(
                        [entry for entry in entries if entry["mal"] in by_mal],
                        key=lambda entry: entry.get("order", 0),
                    )
                    async def available(entry):
                        try:
                            raw = by_mal[entry["mal"]]
                            formatted = items[raw["id"]]
                            src = entry.get("source") or ""
                            host = urlsplit(src).hostname or ""
                            if host == "anime3rb.com":
                                sources = await asyncio.wait_for(
                                    anime3rb_resolve_sources(client, entry["mal"], formatted.get("title_english"), formatted.get("title_romaji"), entry["episode"]),
                                    timeout=6,
                                )
                            elif is_live_anime4up_host(host):
                                sources = await asyncio.wait_for(
                                    live_resolve_sources(client, entry["mal"], formatted.get("title_english"), formatted.get("title_romaji"), entry["episode"]),
                                    timeout=6,
                                )
                            elif src:
                                sources = await asyncio.wait_for(
                                    resolve_episode_page(client, src, entry["mal"], entry["episode"]),
                                    timeout=6,
                                )
                            else:
                                return None
                            if not any(source.get("type") in ("direct", "hls") for source in sources):
                                return None
                            return {**formatted, "available_episode": entry["episode"], "is_available": True, "direct_streamable": True}
                        except (httpx.HTTPError, TimeoutError, asyncio.TimeoutError, ValueError):
                            return None
                    checked = await asyncio.gather(*(available(entry) for entry in recent[:6]), return_exceptions=True)
                    available_items = [item for item in checked if isinstance(item, dict)]
                self._anime_cache.update(items)
                published = []
                seen = set()
                verified = {item["id"]: item for item in available_items}
                for entry in recent:
                    item = items[by_mal[entry["mal"]]["id"]]
                    if item["id"] in seen:
                        continue
                    seen.add(item["id"])
                    published.append({**item, **verified.get(item["id"], {}), "is_available": item["id"] in verified, "available_episode": entry["episode"], "episodes": []})
                ordered = published
                result = {"featured": [item for item in published if item["media_type"] == "SERIES"][:7], "spotlight": published[:28],
                          "popular": sorted(ordered, key=lambda item: item["popularity"], reverse=True)[:5],
                          "recommended": sorted(ordered, key=lambda item: item.get("average_score") or 0, reverse=True)[:6]}
                self._home_cache = (time.monotonic() + TTL_HOMEPAGE, result)
                await db_cache.set("homepage:v3", result, TTL_HOMEPAGE)
                if hidden_ids:
                    return {
                        "featured": [i for i in result.get("featured", []) if i.get("id") not in hidden_ids],
                        "spotlight": [i for i in result.get("spotlight", []) if i.get("id") not in hidden_ids],
                        "popular": [i for i in result.get("popular", []) if i.get("id") not in hidden_ids],
                        "recommended": [i for i in result.get("recommended", []) if i.get("id") not in hidden_ids],
                    }
                return result
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                if self._home_cache:
                    return self._home_cache[1]
                stale = await db_cache.get_stale("homepage:v3")
                if stale is not None:
                    return stale
                raise

    async def get_trending(self, page: int = 1, per_page: int = 24, allow_hidden: bool = False) -> Dict[str, Any]:
        return await self._query_list({
            "page": page,
            "perPage": per_page,
            "sort": ["TRENDING_DESC", "POPULARITY_DESC"],
        }, allow_hidden=allow_hidden)

    async def get_popular_series(self, page: int = 1, per_page: int = 24, allow_hidden: bool = False) -> Dict[str, Any]:
        return await self._query_list({
            "page": page,
            "perPage": per_page,
            "format": "TV",
            "sort": ["POPULARITY_DESC"],
        }, allow_hidden=allow_hidden)

    async def get_anime_movies(self, page: int = 1, per_page: int = 24, allow_hidden: bool = False) -> Dict[str, Any]:
        return await self._query_list({
            "page": page,
            "perPage": per_page,
            "format": "MOVIE",
            "sort": ["POPULARITY_DESC"],
        }, allow_hidden=allow_hidden)

    async def get_top_rated(self, page: int = 1, per_page: int = 24, allow_hidden: bool = False) -> Dict[str, Any]:
        return await self._query_list({
            "page": page,
            "perPage": per_page,
            "sort": ["SCORE_DESC"],
        }, allow_hidden=allow_hidden)

    async def search(self, query: str, page: int = 1, per_page: int = 24, allow_hidden: bool = False) -> Dict[str, Any]:
        filters = provider_filters(query)
        return await self._query_list({
            "page": page,
            "perPage": per_page,
            **filters,
            "sort": ["SEARCH_MATCH", "POPULARITY_DESC"] if "search" in filters else ["POPULARITY_DESC"],
        }, allow_hidden=allow_hidden)

    @public_titles
    async def get_by_id(self, anime_id: int, allow_hidden: bool = False) -> Optional[Dict[str, Any]]:
        from app.services.seo_catalog import saved_media
        from app.services.anime_override_service import anime_override_service
        hidden_ids = anime_override_service.get_hidden_anime_ids_sync()
        if not allow_hidden and anime_id in hidden_ids:
            return None

        # L1 memory cache
        if anime_id in self._anime_cache:
            expiry = self._anime_cache_expires.setdefault(anime_id, time.monotonic() + TTL_ANIME_DETAIL)
            if expiry > time.monotonic():
                return self._anime_cache[anime_id]
            self._anime_cache.pop(anime_id, None)
            self._anime_cache_expires.pop(anime_id, None)

        # L2 DB cache
        db_key = f"anime:{anime_id}"
        db_hit = await db_cache.get(db_key)
        if db_hit is not None:
            if len(self._anime_cache) >= self._MAX_CACHE_SIZE:
                del self._anime_cache[next(iter(self._anime_cache))]
            self._anime_cache[anime_id] = db_hit
            return db_hit

        variables = {"id": anime_id}
        try:
            client = await _get_client()
            resp = await client.post(
                ANILIST_API_URL,
                json={"query": ANIME_DETAIL_QUERY, "variables": variables},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code == 404:
                return await saved_media(anime_id)
            if resp.status_code == 429:
                logger.warning("AniList rate limited (429), skipping fresh fetch for id %s", anime_id)
                raise HTTPException(503, "مصدر البيانات غير متاح مؤقتًا", headers={"Retry-After": "30"})
            resp.raise_for_status()
            response_data = resp.json()
            if response_data.get("errors"):
                raise HTTPException(503, "تعذّر تحميل بيانات العمل مؤقتًا", headers={"Retry-After": "30"})
            media = response_data.get("data", {}).get("Media")
            if not media:
                return await saved_media(anime_id)
            formatted = self._format_media(media)
            if len(self._anime_cache) >= self._MAX_CACHE_SIZE:
                del self._anime_cache[next(iter(self._anime_cache))]
            self._anime_cache[anime_id] = formatted
            await db_cache.set(db_key, formatted, TTL_ANIME_DETAIL)
            return formatted
        except HTTPException:
            saved = await saved_media(anime_id)
            if saved:
                return saved
            raise
        except Exception as e:
            saved = await saved_media(anime_id)
            if saved:
                return saved
            logger.warning("Metadata unavailable for id %s: %s", anime_id, type(e).__name__)
            raise HTTPException(503, "تعذّر الاتصال بمصدر البيانات مؤقتًا", headers={"Retry-After": "30"}) from e

    async def get_stream_sources(self, anime_id: int, episode: int = 1, refresh: bool = False, allow_hidden: bool = False) -> Dict[str, Any]:
        item = await self.get_by_id(anime_id, allow_hidden=allow_hidden)
        if not item:
            from app.database import AsyncSessionLocal
            from app.services.anime_override_service import anime_override_service
            try:
                async with AsyncSessionLocal() as session:
                    custom_servers = await anime_override_service.get_custom_servers(
                        anime_id, episode, session, active_only=True
                    )
            except Exception:
                custom_servers = []

            if custom_servers:
                custom_list = [
                    {
                        "name": s["name"],
                        "type": s["server_type"],
                        "url": s["url"],
                        "subtitle_url": s.get("subtitle_url"),
                        "is_custom": True,
                    }
                    for s in custom_servers
                ]
                return {
                    "anime_id": anime_id,
                    "episode": episode,
                    "servers": custom_list,
                    "embed_url": custom_list[0]["url"],
                    "availability": "available",
                    "diagnostics": {"providers": [], "issues": []},
                }

            return {"anime_id": anime_id, "episode": episode, "servers": [], "embed_url": None,
                    "availability": "metadata_unavailable", "diagnostics": {
                        "providers": [], "issues": [{"reason": "not_found", "host": "local", "count": 1}]}}

        # Fetch custom servers from DB
        from app.database import AsyncSessionLocal
        from app.services.anime_override_service import anime_override_service
        try:
            async with AsyncSessionLocal() as session:
                custom_servers = await anime_override_service.get_custom_servers(
                    anime_id, episode, session, active_only=True
                )
        except Exception:
            custom_servers = []

        english = item.get("title_english") or item.get("title")
        romaji = item.get("title_romaji")
        id_mal = item.get("id_mal")

        db_key = f"stream:v4:{anime_id}:{episode}"
        result = None
        if not refresh and not custom_servers:
            result = await db_cache.get(db_key)

        if result is None:
            result = await anime_stream_resolver.resolve_streams_for_anime(
                anilist_id=anime_id,
                id_mal=id_mal,
                title_english=english,
                title_romaji=romaji,
                episode=episode,
                refresh=refresh,
            )

        if custom_servers:
            custom_list = [
                {
                    "name": s["name"],
                    "type": s["server_type"],
                    "url": s["url"],
                    "subtitle_url": s.get("subtitle_url"),
                    "is_custom": True,
                }
                for s in custom_servers
            ]
            seen_urls = {s["url"] for s in custom_list}
            merged = custom_list + [s for s in result.get("servers", []) if s.get("url") not in seen_urls]
            result = {
                **result,
                "servers": merged,
                "availability": "available",
                "embed_url": merged[0]["url"] if merged else None,
            }
        elif result.get("servers"):
            await db_cache.set(db_key, result, TTL_STREAM)
        elif refresh:
            await db_cache.delete(db_key)

        return result

    async def episodes_for_anime(self, anime_id: int, allow_hidden: bool = False, verify_sources: bool = True):
        item = await self.get_by_id(anime_id, allow_hidden=allow_hidden)
        if not item:
            return None

        from app.database import AsyncSessionLocal
        from app.services.anime_override_service import anime_override_service
        try:
            async with AsyncSessionLocal() as session:
                custom_eps = await anime_override_service.get_custom_episodes(anime_id, session)
        except Exception:
            custom_eps = []
        custom_map = {e["episode_number"]: e for e in custom_eps if not e.get("is_hidden")}
        cache_key = f"episode-index:v2:{anime_id}"
        source = await db_cache.get(cache_key)
        if source is not None:
            source = {int(number): url for number, url in source.items()}
        elif verify_sources or (item.get("status") == "RELEASING" and not released_count(item)):
            try:
                async with asyncio.timeout(8), httpx.AsyncClient(timeout=4) as client:
                    args = (client, item.get("id_mal"), item.get("title_english") or item["title"], item.get("title_romaji"))
                    tasks = [asyncio.create_task(provider(*args)) for provider in (get_episode_index, anime3rb_episode_index, get_anime4up_episodes, live_episode_index)]
                    done, pending = await asyncio.wait(tasks, timeout=6)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    source = {number: url for task in done if not task.cancelled() and task.exception() is None and isinstance(task.result(), dict) for number, url in task.result().items()}
                    if source:
                        await db_cache.set(cache_key, source, 300)
            except (httpx.HTTPError, TimeoutError, ValueError):
                source = {}
        source = source or {}
        if item.get("status") == "RELEASING" and not released_count(item) and not source and not custom_map:
            raise HTTPException(503, "تعذّر التحقق من الحلقات المعروضة مؤقتًا", headers={"Retry-After": "30"})

        total = item.get("total_episodes") or 0
        next_episode = item.get("next_airing_episode")
        max_custom = max(custom_map.keys(), default=0)
        released = max(released_count(item), max(source, default=0), max_custom)
        if total > 0:
            count = max(total, max_custom)
        else:
            count = max(released, max(source, default=0), max_custom)
        if not count and item.get("media_type") == "MOVIE":
            count = 1
        episodes = []
        for number in range(1, count + 1):
            state = "listed" if (number in source or number in custom_map) else "unverified"
            if (item.get("status") == "NOT_YET_RELEASED" or number > released) and number not in custom_map:
                state = "upcoming"
            episodes.append({"number": number, "state": state})
        return {"anime_id": anime_id, "total": count, "reported_total": total or None,
                "released": released, "listed": len(source) + len(custom_map), "episodes": episodes}


anime_service = AnimeService()
