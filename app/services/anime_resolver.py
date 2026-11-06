import asyncio
import json
import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional
import httpx
from app.services.animetna import (resolve_servers, validate_embed, revalidate_server, resolve_player,
    DIRECT_PLAYER_HOSTS, SHARE4MAX_HOSTS, VIDARA_HOSTS, UQLOAD_HOSTS, VOE_HOSTS, MIXDROP_HOSTS,
    STREAMTAPE_HOSTS, VINOVO_HOSTS, FIRESTREAM_HOSTS, DOOD_HOSTS, MEDIAFIRE_HOSTS,
    HLS_MEDIA_SUFFIXES, is_anime4up_host)
from app.services.anime3rb import resolve_sources as resolve_anime3rb
from app.services.anime4up import resolve_sources as resolve_anime4up
from app.services.anime4up_live import resolve_sources as resolve_anime4up_live
from app.services import source_trace
from app.services.source_http import get_source_client

logger = logging.getLogger(__name__)

SLAYER_HEADERS = {
    "client-id": "android-app2",
    "client-secret": "7befba6263cc14c90d2f1d6da2c5cf9b251bfbbd",
    "User-Agent": "okhttp/5.3.2",
    "Accept": "application/json, text/plain, */*",
}

SLAYER_BASE = "https://anslayer.com"


class AnimeStreamResolver:
    def __init__(self) -> None:
        self._stream_cache = {}
        self._inflight = {}

    @staticmethod
    def _clean_title(text: Optional[str]) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"[:\-–—\(\)\[\]]", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _build_search_queries(english: Optional[str], romaji: Optional[str]) -> List[str]:
        raw_titles = [t for t in (english, romaji) if t]
        queries: List[str] = []

        for title in raw_titles:
            queries.append(title)
            norm = re.sub(r"[・:–—\-\|\/]", " ", title)
            norm = re.sub(r"\s+", " ", norm).strip()
            if norm and norm.lower() != title.lower():
                queries.append(norm)
            base = re.split(r"[:–—\-\|\/]", title)[0].strip()
            if base and len(base) >= 3 and base.lower() != title.lower():
                queries.append(base)
            clean = re.sub(r"\s*[\(\[\{].*?[\)\]\}]", "", title)
            clean = re.sub(r"\b(pilot\s*film|movie|special|ova|ona)\b", "", clean, flags=re.I).strip()
            clean = re.sub(r"\s+", " ", clean).strip()
            if clean and len(clean) >= 3 and clean.lower() not in (title.lower(), base.lower()):
                queries.append(clean)

        seen = set()
        deduped: List[str] = []
        for q in queries:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(q)
        return deduped

    @staticmethod
    def _rank_candidates(
        candidates: List[Dict[str, Any]],
        query: str,
        english: Optional[str],
        romaji: Optional[str],
    ) -> List[Dict[str, Any]]:
        full_title = f"{english or ''} {romaji or ''}".lower()
        wants_s2 = bool(re.search(r"season\s*2|2nd\s*season|part\s*2", full_title))
        wants_movie = "movie" in full_title or "film" in full_title

        scored = []
        for item in candidates:
            name = (item.get("anime_name") or "").lower()
            has_s2 = bool(re.search(r"season\s*2|2nd\s*season|part\s*2", name))
            has_movie = "movie" in name or "film" in name or "tengoku" in name

            score = 0
            if not wants_s2 and has_s2:
                score -= 60
            if not wants_movie and has_movie:
                score -= 30
            if wants_s2 and has_s2:
                score += 50

            clean_q = re.sub(r"[^\w\s]", "", query).strip().lower()
            clean_name = re.sub(r"[^\w\s]", "", name).strip().lower()
            if clean_q == clean_name:
                score += 100
            elif clean_name.startswith(clean_q):
                score += 40

            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in scored]

    async def _search_slayer(self, client: httpx.AsyncClient, query: str) -> List[Dict[str, Any]]:
        param = json.dumps({
            "list_type": "filter",
            "just_info": "Yes",
            "order": "desc",
            "anime_name": query,
        })
        url = f"{SLAYER_BASE}/anime/public/animes/get-published-animes?json={urllib.parse.quote(param)}"
        try:
            resp = await client.get(url, headers=SLAYER_HEADERS, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("response", {}).get("data", [])
        except Exception as e:
            logger.debug("Failed slayer search for '%s': %s", query, e)
        return []

    async def _get_slayer_episodes(self, client: httpx.AsyncClient, slayer_id: int) -> List[Dict[str, Any]]:
        url = f"{SLAYER_BASE}/anime/public/anime/get-anime-details?anime_id={slayer_id}"
        try:
            resp = await client.get(url, headers=SLAYER_HEADERS, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json().get("response", {})
                eps = data.get("episodes")
                if isinstance(eps, dict):
                    return eps.get("data", [])
                elif isinstance(eps, list):
                    return eps
        except Exception as e:
            logger.debug("Failed slayer details for id %s: %s", slayer_id, e)
        return []

    async def _resolve_fw_streams(self, client: httpx.AsyncClient, fw_param: str) -> List[str]:
        enc = urllib.parse.quote(fw_param)
        url = f"{SLAYER_BASE}/la/public/api/f?n={enc}"
        try:
            resp = await client.get(url, headers=SLAYER_HEADERS, timeout=4.0)
            if resp.status_code == 200:
                body = resp.text.strip()
                if body and body.startswith("["):
                    arr = json.loads(body)
                    return [str(x).replace(r"\/", "/") for x in arr if x]
        except Exception as e:
            logger.debug("Failed resolving fw streams: %s", e)
        return []

    @staticmethod
    def _format_stream_servers(raw_urls: List[str]) -> List[Dict[str, str]]:
        servers: List[Dict[str, str]] = []
        seen_urls = set()

        for raw in raw_urls:
            raw = raw.strip()
            if not raw or raw in seen_urls:
                continue
            parsed = urllib.parse.urlsplit(raw)
            host = parsed.hostname or ""

            if parsed.scheme != "https":
                continue

            if host in MEDIAFIRE_HOSTS:
                servers.append({"name": "Mediafire FHD", "type": "embed", "url": raw})
                seen_urls.add(raw)
                continue

            if host in STREAMTAPE_HOSTS:
                embed_url = raw.replace("/v/", "/e/")
                servers.append({"name": "Streamtape", "type": "embed", "url": embed_url})
                seen_urls.add(raw)
                continue

            if host in VINOVO_HOSTS:
                embed_url = raw.replace("/v/", "/e/")
                servers.append({"name": "Vinovo", "type": "embed", "url": embed_url})
                seen_urls.add(raw)
                continue

            if host in FIRESTREAM_HOSTS:
                embed_url = raw.replace("/v/", "/e/")
                servers.append({"name": "Firestream", "type": "embed", "url": embed_url})
                seen_urls.add(raw)
                continue

            if host in DOOD_HOSTS:
                embed_url = raw.replace("/v/", "/e/")
                servers.append({"name": "Doodstream", "type": "embed", "url": embed_url})
                seen_urls.add(raw)
                continue

            if "mega.nz" in host:
                embed_url = raw.replace("/file/", "/embed/")
                servers.append({"name": "Mega", "type": "embed", "url": embed_url})
                seen_urls.add(raw)
                continue

            if host in DIRECT_PLAYER_HOSTS | SHARE4MAX_HOSTS | VIDARA_HOSTS | UQLOAD_HOSTS | VOE_HOSTS | MIXDROP_HOSTS or is_anime4up_host(host):
                servers.append({"name": host.removeprefix("www."), "type": "embed", "url": raw})
                seen_urls.add(raw)
                continue
            if host.endswith(HLS_MEDIA_SUFFIXES):
                servers.append({"name": "بث مباشر", "type": "hls", "url": raw})
                seen_urls.add(raw)
                continue

            if "ok.ru/video/" in raw or "ok.ru/videoembed/" in raw:
                vid = raw.split("video/")[-1].split("videoembed/")[-1].split("?")[0].strip("/")
                embed = f"https://ok.ru/videoembed/{vid}"
                servers.append({
                    "name": "OK.ru",
                    "type": "embed",
                    "url": embed,
                })
                seen_urls.add(raw)

        return servers

    async def _extract_links_from_episodes(
        self,
        client: httpx.AsyncClient,
        episodes: List[Dict[str, Any]],
        target_ep: int,
    ) -> List[str]:
        target_obj = None
        for ep in episodes:
            if str(ep.get("episode_number")) == str(target_ep):
                target_obj = ep
                break

        if not target_obj:
            return []

        urls = target_obj.get("episode_urls") or []
        resolved = []
        for u in urls:
            link = (u.get("episode_url") or "").replace(r"\/", "/")
            fw_param: Optional[str] = None

            if "vq.php?f=" in link:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                f = qs.get("f", [""])[0]
                e = qs.get("e", [""])[0].split("|")[0]
                if f and e:
                    fw_param = f"{f}\\{e}"
            elif "?n=" in link:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
                n = qs.get("n", [""])[0]
                if n:
                    fw_param = n

            if fw_param:
                streams = await self._resolve_fw_streams(client, fw_param)
                resolved.extend(streams)
            elif link.startswith("https://"):
                resolved.append(link)
        return list(dict.fromkeys(resolved))

    async def resolve_streams_for_anime(
        self,
        anilist_id: int,
        id_mal: Optional[int] = None,
        title_english: Optional[str] = None,
        title_romaji: Optional[str] = None,
        episode: int = 1,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        key = f"{anilist_id}:{episode}"
        task = self._inflight.get(key)
        if task is None or task.done():
            task = asyncio.create_task(self._resolve_streams_for_anime(
                anilist_id, id_mal, title_english, title_romaji, episode, refresh
            ))
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._inflight.pop(key, None)
                                   if self._inflight.get(key) is done else None)
        # Navigating away must not cancel a shared source check for other viewers.
        return await asyncio.shield(task)

    async def _resolve_streams_for_anime(
        self,
        anilist_id: int,
        id_mal: Optional[int] = None,
        title_english: Optional[str] = None,
        title_romaji: Optional[str] = None,
        episode: int = 1,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        cache_key = f"{anilist_id}:{episode}"
        cached = self._stream_cache.get(cache_key)
        if cached and any((urllib.parse.urlsplit(server.get("url", "")).hostname or "").endswith((".uqload.vc", ".uqload.com", ".uqload.is")) for server in cached[1].get("servers", [])):
            cached = None
            self._stream_cache.pop(cache_key, None)
        if not refresh and cached and cached[0] > time.monotonic():
            return cached[1]

        servers: List[Dict[str, str]] = []
        issues = source_trace.begin()
        provider_checks = []

        async def run_provider(provider, client):
            started = time.monotonic()
            check = {"provider": provider.__module__.rsplit(".", 1)[-1], "status": "checking"}
            provider_checks.append(check)
            try:
                result = await provider(client, id_mal, title_english, title_romaji, episode)
                playable = [server for server in result if server.get("type") in ("direct", "hls", "embed")]
                check.update(status="available" if playable else "no_playable_source", servers=len(playable), rejected=len(result) - len(playable))
                return playable
            except asyncio.CancelledError:
                check["status"] = "timeout"
                raise
            except Exception as error:
                check.update(status="error", error=type(error).__name__)
                return []
            finally:
                check["elapsed_ms"] = round((time.monotonic() - started) * 1000)

        try:
            async with asyncio.timeout(30):
                client = get_source_client()
                pending = {asyncio.create_task(run_provider(provider, client))
                           for provider in (resolve_servers, resolve_anime3rb, resolve_anime4up, resolve_anime4up_live)}
                deadline = time.monotonic() + 24
                try:
                    while pending and time.monotonic() < deadline:
                        done, pending = await asyncio.wait(pending, timeout=max(0, deadline-time.monotonic()), return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            if not task.cancelled() and task.exception() is None:
                                servers.extend(server for server in task.result()
                                               if server.get("type") in ("direct", "hls", "embed"))
                        if servers:
                            deadline = min(deadline, time.monotonic() + 2.2)
                finally:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
        except (httpx.HTTPError, TimeoutError, ValueError):
            logger.info("Primary anime source was unavailable for %s", anilist_id)

        try:
            async with asyncio.timeout(15):
                client = get_source_client()
                queries = [] if servers else self._build_search_queries(title_english, title_romaji)
                raw_titles = [t for t in (title_english, title_romaji) if t]
                expected_names = set()
                for title in raw_titles:
                    expected_names.add(re.sub(r"\W+", "", title.casefold()))
                    norm = re.sub(r"[・:–—\-\|\/]", " ", title)
                    expected_names.add(re.sub(r"\W+", "", norm.casefold()))
                    base = re.split(r"[:–—\-\|\/]", title)[0].strip()
                    if base and len(base) >= 3:
                        expected_names.add(re.sub(r"\W+", "", base.casefold()))

                for q in queries:
                    candidates = await self._search_slayer(client, q)
                    if not candidates:
                        continue

                    ranked = self._rank_candidates(candidates, q, title_english, title_romaji)
                    for match in ranked[:3]:
                        match_clean = re.sub(r"\W+", "", match.get("anime_name", "").casefold())
                        if match_clean not in expected_names:
                            continue
                        slayer_id = match.get("anime_id")
                        if not slayer_id:
                            continue

                        episodes = await self._get_slayer_episodes(client, slayer_id)
                        raw_links = await self._extract_links_from_episodes(client, episodes, episode)
                        formatted = self._format_stream_servers(raw_links)
                        checked = await asyncio.gather(*(resolve_player(client, server, id_mal, episode) for server in formatted), return_exceptions=True)
                        available = [server for group in checked if isinstance(group, list)
                                     for server in group if server.get("type") in ("direct", "hls", "embed")]
                        if available:
                            servers.extend(available)
                            break

                    if servers:
                        break
        except Exception as e:
            logger.warning("Anime stream resolution encountered an issue: %s", e)

        if not servers and cached and cached[1].get("servers"):
            # A temporary search outage must not erase previously working sources.
            async with asyncio.timeout(12):
                client = get_source_client()
                tasks = [asyncio.create_task(revalidate_server(client, server))
                         for server in cached[1].get("servers", [])
                         if server.get("type") in ("direct", "hls", "embed")]
                if tasks:
                    done, pending = await asyncio.wait(tasks, timeout=10)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    servers = [task.result() for task in done
                               if not task.cancelled() and task.exception() is None and task.result()]

        seen_urls = set()
        deduped_servers: List[Dict[str, str]] = []
        def quality_rank(server):
            match = re.search(r"(2160|1080|720|480|360)p?", server["name"])
            return -int(match[1]) if match else 0

        def cdn_startup_rank(server):
            host = urllib.parse.urlsplit(server.get("url", "")).hostname or ""
            if host.endswith((".97bf1.com", ".friwduh.shop", ".s1q2105.com")):
                return 0
            if host.endswith(".cloudwindow-route.com"):
                return 1
            return 2

        type_priority = {"hls": 0, "direct": 1, "embed": 2}
        for s in sorted(servers, key=lambda source: (type_priority.get(source.get("type", "embed"), 3), quality_rank(source), cdn_startup_rank(source))):
            parsed = urllib.parse.urlsplit(s["url"])
            # Two catalogs may list the same Mixdrop file with different signatures.
            identity = (parsed.hostname, parsed.path) if (parsed.hostname or "").endswith(".mxcontent.net") else s["url"]
            if identity not in seen_urls:
                seen_urls.add(identity)
                deduped_servers.append(s)

        result = {
            "anime_id": anilist_id,
            "id_mal": id_mal,
            "episode": episode,
            "embed_url": deduped_servers[0]["url"] if deduped_servers else None,
            "has_arabic_subs": bool(deduped_servers),
            "servers": deduped_servers,
            "availability": "available" if deduped_servers else "source_unavailable",
            "diagnostics": {"providers": provider_checks, "issues": source_trace.summary(issues)},
        }

        ttl = 900 if any(server["type"] == "hls" for server in deduped_servers) else 1800
        self._stream_cache[cache_key] = (time.monotonic() + (ttl if deduped_servers else 5), result)
        return result


anime_stream_resolver = AnimeStreamResolver()
