"""Wide hero art from public Kitsu and AniList data, with exact anime identity."""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from app.config import settings

logger = logging.getLogger(__name__)
CACHE_SECONDS = 86400
STALE_SECONDS = 7 * CACHE_SECONDS
KITSU_API = "https://kitsu.app/api/edge/mappings"


def valid_image(url, source, anime_id=None):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if source == "kitsu":
        return parsed.hostname in {"media.kitsu.app", "media.kitsu.io"} and parsed.path.startswith("/anime/") and ("/cover_images/" in parsed.path or "/cover_image/" in parsed.path)
    if source == "anilist":
        host = parsed.hostname or ""
        prefix = f"/file/anilistcdn/media/anime/banner/{anime_id}-" if anime_id else "/file/anilistcdn/media/anime/banner/"
        return host.endswith(".anilist.co") and parsed.path.startswith(prefix)
    return False


def kitsu_images(payload, mal_id):
    """Follow an exact MAL mapping to its included anime; never title-match a season."""
    if not isinstance(payload, dict):
        return []
    related_ids = set()
    for mapping in payload.get("data") or []:
        attrs = mapping.get("attributes") or {}
        item = (mapping.get("relationships") or {}).get("item", {}).get("data") or {}
        if mapping.get("type") == "mappings" and attrs.get("externalSite") == "myanimelist/anime" and str(attrs.get("externalId")) == str(mal_id) and item.get("type") == "anime":
            related_ids.add(str(item.get("id")))
    images = []
    for anime in payload.get("included") or []:
        if anime.get("type") != "anime" or str(anime.get("id")) not in related_ids:
            continue
        cover = (anime.get("attributes") or {}).get("coverImage") or {}
        if not isinstance(cover, dict):
            continue
        # Original is the native asset; 'large' is Kitsu's wide 3360px rendition.
        for size in ("original", "large"):
            url = cover.get(size)
            if valid_image(url, "kitsu") and not any(image["image_url"] == url for image in images):
                images.append({"image_url": url, "source": "kitsu", "provider_id": str(anime["id"]), "size": size})
    return images


class HeroImages:
    def __init__(self, cache_dir=None, transport=None, request_interval=1.1):
        self.cache_dir = Path(cache_dir or Path(settings.CACHE_DIR) / "hero-images-v2")
        self.transport = transport
        self.request_interval = request_interval
        self._memory = {}
        self._pending = {}
        self._rate_lock = asyncio.Lock()
        self._next_request_at = 0.0

    @staticmethod
    def _key(item):
        return f"{item['id']}-{item.get('id_mal') or 0}"

    def _read(self, item):
        key = self._key(item)
        record = self._memory.get(key)
        if record is None:
            try:
                record = json.loads((self.cache_dir / f"{key}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        if not isinstance(record, dict) or record.get("anime_id") != item["id"] or record.get("id_mal") != item.get("id_mal") or not valid_image(record.get("image_url"), record.get("source"), item["id"]):
            return None
        cached_at = record.get("cached_at")
        if not isinstance(cached_at, (int, float)) or not 0 <= time.time()-cached_at < STALE_SECONDS:
            return None
        self._memory[key] = record
        return record

    def _write(self, item, record):
        key = self._key(item)
        self._memory[key] = record
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_dir / f"{key}.{os.getpid()}.tmp"
            temporary.write_text(json.dumps(record), encoding="utf-8")
            temporary.replace(self.cache_dir / f"{key}.json")
        except OSError:
            logger.warning("Hero disk cache unavailable; using memory cache")

    async def get(self, item):
        if not isinstance(item.get("id"), int) or isinstance(item["id"], bool) or item["id"] <= 0:
            return None
        cached = self._read(item)
        ttl = CACHE_SECONDS if cached and cached.get("source") == "kitsu" else 3600
        if cached and time.time()-cached["cached_at"] < ttl:
            return {**cached, "stale": False}
        key = self._key(item)
        task = self._pending.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(item, cached))
            self._pending[key] = task
            task.add_done_callback(lambda done: self._pending.pop(key, None))
        return await asyncio.shield(task)

    async def _fetch(self, item, cached):
        candidates = []
        mal_id = item.get("id_mal")
        if isinstance(mal_id, int) and not isinstance(mal_id, bool) and mal_id > 0:
            try:
                async with self._rate_lock:
                    delay = self._next_request_at-time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    self._next_request_at = time.monotonic()+self.request_interval
                async with httpx.AsyncClient(timeout=httpx.Timeout(6, connect=3), transport=self.transport, follow_redirects=False) as client:
                    response = await client.get(KITSU_API, params={"filter[externalSite]": "myanimelist/anime", "filter[externalId]": str(mal_id), "include": "item"}, headers={"Accept": "application/vnd.api+json", "User-Agent": "AnimeSlayer/1.0"})
                    response.raise_for_status()
                    candidates = kitsu_images(response.json(), mal_id)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError) as error:
                logger.warning("Kitsu cover unavailable for MAL %s: %s", mal_id, type(error).__name__)
        banner = item.get("banner_url")
        if valid_image(banner, "anilist", item["id"]):
            candidates.append({"image_url": banner, "source": "anilist", "provider_id": str(item["id"]), "size": "banner"})
        if candidates:
            record = {"anime_id": item["id"], "id_mal": item.get("id_mal"), **candidates[0], "alternatives": candidates[1:], "cached_at": time.time()}
            self._write(item, record)
            return {**record, "stale": False}
        return {**cached, "stale": True} if cached else None


hero_images = HeroImages()
