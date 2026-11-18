import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import CacheEntry, CatalogEntry
from app.services import anime_service as anime_service_module
from app.services.anime_service import AnimeService
from app.services.animetna import _animetna_relevant_markup, latest_catalog
from app.services.db_cache import DBCache
from app.services.title_localization import (
    arabic_to_latin,
    names_for,
    provider_search,
    suggest_local,
)


class HomepageLatestFeedTests(unittest.IsolatedAsyncioTestCase):
    def test_animetna_markup_filter_drops_archive_dump_and_keeps_current_sections(self):
        sample = (
            '<div class="slider"><a href="https://animetna.com/anime/black-clover-season-2/">BC2</a></div>'
            '<h2>آخر الحلقات المضافة</h2>'
            '<a href="https://animetna.com/anime/kyousougiga/">Kyousougiga 2013</a>'
            '<h2>أحدث أنميات موسم خريف 2026</h2>'
            '<a href="https://animetna.com/anime/hotel-inhumans-season-2/">Hotel Inhumans 2</a>'
            '<h2>أحدث الانميات المضافة</h2>'
            '<a href="https://animetna.com/anime/kill-la-kill/">Kill la Kill 2013</a>'
        )
        filtered = _animetna_relevant_markup(sample)
        self.assertIn("black-clover-season-2", filtered)
        self.assertIn("hotel-inhumans-season-2", filtered)
        self.assertNotIn("kyousougiga", filtered)
        self.assertNotIn("kill-la-kill", filtered)

    async def test_latest_catalog_merges_providers_with_highest_episode_and_anilist_fallback(self):
        a3 = [{"mal": 61967, "episode": 7, "source": "https://anime3rb.com/episode/bc/7", "order": 0, "provider": "anime3rb"}]
        a4 = [{"mal": 61967, "episode": 6, "source": "https://4b.example/ep/6", "order": 0, "provider": "anime4up_live"},
              {"mal": 21, "episode": 1180, "source": "https://4b.example/ep/1180", "order": 1, "provider": "anime4up_live"}]
        at = [{"mal": 61967, "episode": 5, "source": "https://animetna.com/episode/bc-5/", "order": 0, "provider": "animetna"}]
        al = [{"mal": 59088, "episode": 7, "source": "", "order": 0, "provider": "anilist"}]

        with patch("app.services.anime3rb.latest_episodes", AsyncMock(return_value=a3)), \
             patch("app.services.anime4up_live.latest_episodes", AsyncMock(return_value=a4)), \
             patch("app.services.animetna._animetna_current_entries", AsyncMock(return_value=at)), \
             patch("app.services.animetna._anilist_recent_aired", AsyncMock(return_value=al)):
            async with httpx.AsyncClient() as client:
                entries = await latest_catalog(client, limit=10)

        self.assertEqual([e["mal"] for e in entries], [61967, 21, 59088])
        self.assertEqual(entries[0]["episode"], 7)
        self.assertEqual(entries[0]["provider"], "anime3rb")


class SearchLocalizationTests(unittest.TestCase):
    def test_persian_synonyms_do_not_override_arabic_names(self):
        _, ar, _, source = names_for({
            "id": 20958,
            "title": "Attack on Titan Season 2",
            "synonyms": ["حمله به تایتان فصل 2"],
        })
        self.assertNotIn("تایتان", ar)
        self.assertNotIn("فصل", ar)
        self.assertIn("هجوم العمالقة", ar)
        self.assertEqual(source, "curated+phonetic")

        _, ar2, _, _ = names_for({
            "id": 97668,
            "title": "One-Punch Man Season 2",
            "synonyms": ["مرد تک مشتی"],
        })
        self.assertNotIn("مرد", ar2)
        self.assertIn("ون بنش مان", ar2)

    def test_space_collapsed_and_unseen_arabic_queries_resolve(self):
        self.assertEqual(provider_search("داندادان"), "Dandadan")
        self.assertEqual(provider_search("فرييرين"), "Frieren: Beyond Journey's End")
        self.assertEqual(provider_search("ساكاموتو").casefold(), "sakamoto days")
        self.assertEqual(provider_search("بلاككلوفر"), "Black Clover")
        self.assertEqual(provider_search("كاجوراباتشي"), "Kagurabachi")
        self.assertEqual(arabic_to_latin("شانغريلا"), "shangrila")

    def test_suggest_local_matches_without_spaces(self):
        from app.services import title_localization
        old = dict(title_localization._SUGGESTIONS)
        try:
            title_localization._SUGGESTIONS.clear()
            title_localization.localize_payload({
                "id": 171018, "title": "Dandadan", "media_type": "SERIES",
                "poster_url": "p.jpg", "genres": [], "popularity": 9000,
            })
            hits = suggest_local("داندادان", limit=5)
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["id"], 171018)
        finally:
            title_localization._SUGGESTIONS.clear()
            title_localization._SUGGESTIONS.update(old)


class CatalogFilterAndFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.service = AnimeService()
        anime_service_module._shared_client = None
        DBCache._l1.clear()

    async def asyncTearDown(self):
        DBCache._l1.clear()
        await self.engine.dispose()

    async def test_year_without_season_uses_calendar_bounds_and_newest_excludes_unreleased(self):
        captured = []

        def handler(request):
            variables = json.loads(request.content)["variables"]
            captured.append(variables)
            return httpx.Response(200, json={"data": {"Page": {"media": [], "pageInfo": {"total": 0, "currentPage": 1, "perPage": 24, "lastPage": 1}}}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        with patch("app.services.anime_service._get_client", AsyncMock(return_value=client)), \
             patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=None)), \
             patch("app.services.anime_service.db_cache.set", AsyncMock()), \
             patch("app.services.anime_service.db_cache.set_many", AsyncMock()):
            await self.service.browse(year=2025, format="MOVIE")
            await self.service.browse(sort="START_DATE_DESC")

        self.assertEqual(captured[0]["startDate_greater"], 20249999)
        self.assertEqual(captured[0]["startDate_lesser"], 20260000)
        self.assertNotIn("seasonYear", captured[0])
        self.assertEqual(captured[1]["status_in"], ["RELEASING", "FINISHED"])

    async def test_single_franchise_arabic_search_expands_with_year_filter(self):
        calls = []

        def handler(request):
            variables = json.loads(request.content)["variables"]
            calls.append(variables)
            if "ids" in variables:
                # 2017 Season 1 does not match year=2026
                media = []
            else:
                media = [{
                    "id": 195604, "idMal": 61967, "title": {"english": "Black Clover Season 2", "romaji": "Black Clover 2nd Season"},
                    "startDate": {"year": 2026}, "format": "TV", "status": "RELEASING", "episodes": 12, "genres": ["Action"],
                }]
            return httpx.Response(200, json={"data": {"Page": {"media": media, "pageInfo": {"total": len(media), "currentPage": 1, "perPage": 24, "lastPage": 1}}}})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        with patch("app.services.anime_service._get_client", AsyncMock(return_value=client)), \
             patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=None)), \
             patch("app.services.anime_service.db_cache.set", AsyncMock()), \
             patch("app.services.anime_service.db_cache.set_many", AsyncMock()):
            res = await self.service.browse(search="بلاك كلوفر", year=2026)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["ids"], [97940])
        self.assertEqual(calls[1]["search"], "Black Clover")
        self.assertEqual(calls[1]["startDate_greater"], 20259999)
        self.assertEqual([item["id"] for item in res["items"]], [195604])

    async def test_anilist_429_retries_then_falls_back_to_stale_cache_and_catalog_entries(self):
        async with self.sessions() as db:
            db.add(CatalogEntry(
                anime_id=182255,
                canonical_path="/series/182255",
                payload=json.dumps({
                    "id": 182255, "title": "Frieren Season 2", "title_english": "Frieren Season 2",
                    "title_ar": "فرييرين الموسم 2", "title_aliases": ["فرييرين الموسم 2", "Frieren Season 2"],
                    "media_type": "SERIES", "release_year": 2026, "status": "RELEASING",
                    "genres": [{"id": 1, "name": "Adventure", "slug": "adventure"}], "popularity": 50000,
                }),
            ))
            await db.commit()

        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"Retry-After": "0"})))
        self.addAsyncCleanup(client.aclose)
        with patch("app.services.anime_service._get_client", AsyncMock(return_value=client)), \
             patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=None)), \
             patch("app.services.anime_service.db_cache.get_stale", AsyncMock(return_value=None)), \
             patch("app.database.AsyncSessionLocal", self.sessions):
            res = await self.service.browse(year=2026, genre="Adventure")

        self.assertEqual([item["id"] for item in res["items"]], [182255])


if __name__ == "__main__":
    unittest.main()
