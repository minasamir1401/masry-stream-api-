import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base, _initialize_schema
from app.models import CatalogEntry, CacheEntry
from app.services import seo_catalog, catalog_links, title_localization
from app.services.anime_service import AnimeService
from app.services.db_cache import DBCache
from app.routers.anime import router


class PersistentLinkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.sessions()
        self.paths_before = dict(catalog_links._paths)

    async def asyncTearDown(self):
        catalog_links._paths.clear(); catalog_links._paths.update(self.paths_before)
        await self.db.close(); await self.engine.dispose()

    async def test_first_url_survives_name_art_classification_and_source_changes(self):
        original = {"id": 97940, "title": "Black Clover", "media_type": "SERIES", "status": "FINISHED", "total_episodes": 170, "poster_url": "original.jpg", "genres": []}
        await seo_catalog.remember_catalog(self.db, {"anime:97940": original})
        await self.db.commit()
        changed = {**original, "title": "Edited title", "media_type": "MOVIE", "poster_url": "updated.jpg", "stream_url": "temporary-secret-token"}
        await seo_catalog.remember_catalog(self.db, {"anime:97940": changed})
        await self.db.commit()
        self.assertEqual(changed["canonical_path"], "/series/97940")
        row = (await self.db.execute(select(CatalogEntry))).scalar_one()
        self.assertEqual(row.canonical_path, "/series/97940")
        self.assertNotIn("temporary-secret-token", row.payload)
        with patch.object(seo_catalog, "AsyncSessionLocal", self.sessions):
            self.assertIn("/series/97940", await seo_catalog.sitemap_paths("series"))
            self.assertNotIn("/movie/97940", await seo_catalog.sitemap_paths("movies"))
            self.assertIn("/watch/97940?ep=170", await seo_catalog.sitemap_paths("episodes"))

    async def test_cache_clear_and_restart_preserve_path_and_saved_page(self):
        work = {"id": 97940, "title": "Black Clover", "media_type": "SERIES", "poster_url": "poster.jpg"}
        await DBCache._db_set(self.db, "anime:97940", work, 1)
        await self.db.commit()
        await self.db.execute(text("DELETE FROM cache_entries")); await self.db.commit()
        catalog_links._paths.clear()
        with patch.object(seo_catalog, "AsyncSessionLocal", self.sessions):
            await seo_catalog.backfill_catalog()
            saved = await seo_catalog.saved_media(97940)
        self.assertEqual(catalog_links.known_path(97940), "/series/97940")
        self.assertEqual(saved["title"], "Black Clover")
        self.assertEqual(saved["canonical_path"], "/series/97940")
        self.assertFalse(saved["is_available"])
        self.assertNotIn("stream_url", saved)

    async def test_legacy_paths_are_pinned_before_importing_new_classification(self):
        self.db.add(CatalogEntry(anime_id=97940, payload=json.dumps({"id":97940,"title":"Black Clover","media_type":"SERIES"})))
        self.db.add(CacheEntry(key="anime:97940", value=json.dumps({"id":97940,"title":"Updated","media_type":"MOVIE"}), expires_at=1))
        await self.db.commit()
        with patch.object(seo_catalog, "AsyncSessionLocal", self.sessions):
            await seo_catalog.backfill_catalog()
        self.assertEqual(catalog_links.known_path(97940), "/series/97940")

    async def test_provider_outage_uses_saved_metadata_but_keeps_same_id_and_url(self):
        work = {"id":97940,"title":"Black Clover","media_type":"SERIES","genres":[]}
        await seo_catalog.remember_catalog(self.db, {"homepage:v2":{"featured":[work]}}); await self.db.commit()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
        self.addAsyncCleanup(client.aclose)
        with patch.object(seo_catalog, "AsyncSessionLocal", self.sessions), patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=None)), patch("app.services.anime_service._get_client", AsyncMock(return_value=client)):
            restored = await AnimeService().get_by_id(97940)
        self.assertEqual(restored["canonical_path"], "/series/97940")
        self.assertEqual(restored["title_ar"], "بلاك كلوفر")

    async def test_schema_upgrade_is_repeatable_and_retains_old_inventory(self):
        async with self.engine.begin() as connection:
            await connection.execute(text("DROP TABLE catalog_entries"))
            await connection.execute(text("CREATE TABLE catalog_entries (anime_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"))
            await connection.execute(text("INSERT INTO catalog_entries VALUES (97940, '{}')"))
            await connection.run_sync(_initialize_schema)
            await connection.run_sync(_initialize_schema)
            rows = (await connection.execute(text("SELECT anime_id, canonical_path FROM catalog_entries"))).all()
        self.assertEqual(rows, [(97940, None)])


class SuggestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old = dict(title_localization._SUGGESTIONS)
        title_localization._SUGGESTIONS.clear()
        self.work = {"id":97940,"title":"Black Clover","media_type":"SERIES","poster_url":"poster.jpg","genres":[],"popularity":100}
        title_localization.localize_payload(self.work)
        app = FastAPI(); app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        title_localization._SUGGESTIONS.clear(); title_localization._SUGGESTIONS.update(self.old)
        await self.client.aclose()

    async def test_typing_prefixes_arabic_english_and_common_typo_uses_local_catalog(self):
        with patch("app.routers.anime.anime_service.search", AsyncMock()) as network, patch("app.services.anime_override_service.anime_override_service.get_hidden_anime_ids_sync", return_value=set()):
            for query in ("b", "bl", "black cl", "ب", "بل", "بلاك", "بلاك لكوفر"):
                response = await self.client.get("/api/anime/suggestions", params={"q":query})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["items"][0]["canonical_path"], "/series/97940")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
        network.assert_not_awaited()

    async def test_hidden_works_are_excluded_and_provider_failure_is_retryable(self):
        self.assertEqual(title_localization.suggest_local("black", hidden_ids={97940}), [])
        with patch("app.routers.anime.anime_service.search", AsyncMock(side_effect=RuntimeError("offline"))):
            response = await self.client.get("/api/anime/suggestions?q=unknown")
        self.assertEqual(response.status_code, 503)
        for query in ("q=x&limit=500", "q=", "q="+"x"*201):
            self.assertEqual((await self.client.get("/api/anime/suggestions?"+query)).status_code, 422)


if __name__ == "__main__": unittest.main()
