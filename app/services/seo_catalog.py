"""Persistent inventory of known works, independent of expiring provider caches."""
import json
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.database import AsyncSessionLocal
from app.models import CacheEntry, CatalogEntry, AnimeOverride, CustomEpisode, Media
from app.services.catalog_links import default_path, register_path


def released_count(item):
    if item.get("status") == "NOT_YET_RELEASED":
        return 0
    if item.get("next_airing_episode"):
        return max(0, int(item["next_airing_episode"]) - 1)
    if item.get("status") == "FINISHED":
        return max(0, int(item.get("total_episodes") or (1 if item.get("media_type") == "MOVIE" else 0)))
    # An announced total is not evidence that those episodes have aired.
    return 0


async def remember_catalog(session, values):
    works, targets = {}, []
    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            if value.get("id") and value.get("media_type") in ("MOVIE", "SERIES") and value.get("title_namespace") != "local":
                targets.append(value)
                works[value["id"]] = value
            else:
                for item in value.values():
                    if isinstance(item, (list, dict)):
                        collect(item)
    # Only media caches contribute; playback URLs/tokens are never archived here.
    for key, value in values.items():
        if key.startswith(("anime:", "list:", "homepage:")):
            collect(value)
    transient = {"episodes", "stream_url", "file_name", "is_available", "direct_streamable", "available_episode", "catalog_stale"}
    rows = [{"anime_id": anime_id, "canonical_path": default_path(anime_id, value.get("media_type")),
             "payload": json.dumps({key: item for key, item in value.items() if key not in transient}, sort_keys=True, ensure_ascii=False, default=str)}
            for anime_id, value in works.items()]
    if rows:
        insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
        statement = insert(CatalogEntry).values(rows)
        await session.execute(statement.on_conflict_do_update(index_elements=["anime_id"], set_={"payload": statement.excluded.payload, "canonical_path": func.coalesce(CatalogEntry.canonical_path, statement.excluded.canonical_path)}))
        paths = dict((await session.execute(select(CatalogEntry.anime_id, CatalogEntry.canonical_path).where(CatalogEntry.anime_id.in_(works)))).all())
        for anime_id, path in paths.items():
            register_path(anime_id, path)
        for value in targets:
            value["canonical_path"] = paths[value["id"]]


async def backfill_catalog():
    async with AsyncSessionLocal() as session:
        # Preserve previously published paths before fresh cache data is imported.
        existing = (await session.execute(select(CatalogEntry))).scalars().all()
        for entry in existing:
            if not entry.canonical_path:
                entry.canonical_path = default_path(entry.anime_id, json.loads(entry.payload).get("media_type"))
            register_path(entry.anime_id, entry.canonical_path)
        await session.flush()
        rows = (await session.execute(select(CacheEntry).where(CacheEntry.key.like("anime:%") | CacheEntry.key.like("homepage:%") | CacheEntry.key.like("list:%")))).scalars().all()
        values = {}
        for row in rows:
            try:
                values[row.key] = json.loads(row.value)
            except (ValueError, TypeError):
                continue
        for offset in range(0, len(values), 100):
            await remember_catalog(session, dict(list(values.items())[offset:offset + 100]))
        await session.commit()


async def saved_media(anime_id):
    """Retain known work pages after cache expiry/provider failure, without streams."""
    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(CatalogEntry, anime_id)
            if row is None:
                return None
            item = json.loads(row.payload)
            if not item.get("title"):
                return None
            item = {"telegram_channel": "AnimeClover", "telegram_message_id": anime_id,
                    "file_size": 0, "mime_type": "video/mp4", "genres": [], "view_count": 0, **item,
                    "id": anime_id, "canonical_path": row.canonical_path or default_path(anime_id, item.get("media_type")),
                    "is_available": False, "direct_streamable": False, "catalog_stale": True, "episodes": []}
            register_path(anime_id, item["canonical_path"])
            return item
    except (SQLAlchemyError, ValueError, TypeError):
        return None


async def sitemap_paths(kind):
    async with AsyncSessionLocal() as session:
        hidden = set((await session.execute(select(AnimeOverride.anime_id).where(AnimeOverride.is_hidden.is_(True)))).scalars())
        entries = (await session.execute(select(CatalogEntry).order_by(CatalogEntry.anime_id))).scalars().all()
        local = (await session.execute(select(Media).where(Media.media_type.in_(["MOVIE", "SERIES"]), Media.is_available.is_(True)))).scalars().all()
        custom = (await session.execute(select(CustomEpisode).where(CustomEpisode.is_hidden.is_(False)))).scalars().all()
    paths = set()
    for entry in entries:
        if entry.anime_id in hidden:
            continue
        item = json.loads(entry.payload)
        path = entry.canonical_path or default_path(entry.anime_id, item.get("media_type"))
        category = "movies" if path.startswith("/movie/") else "series"
        if kind == category:
            paths.add(path)
        if kind == "episodes":
            paths.update(f"/watch/{entry.anime_id}?ep={number}" for number in range(1, released_count(item) + 1))
    for item in local:
        if item.id in hidden:
            continue
        category = "movies" if item.media_type == "MOVIE" else "series"
        if kind == category:
            paths.add(f"/{'movie' if category == 'movies' else 'series'}/{item.id}")
        if kind == "episodes" and item.file_name:
            paths.add(f"/watch/{item.id}?ep=1")
    if kind == "episodes":
        paths.update(f"/watch/{episode.anime_id}?ep={episode.episode_number}" for episode in custom if episode.anime_id not in hidden)
    return sorted(paths)
