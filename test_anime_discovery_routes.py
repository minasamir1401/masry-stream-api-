import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.routers.anime import router


class DiscoveryRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def test_discovery_names_reach_services_instead_of_numeric_anime_route(self):
        with patch("app.routers.anime.anime_service.get_homepage", AsyncMock(return_value={"featured": [{"id": 113415}]})) as homepage, \
             patch("app.routers.anime.airing_schedule.get_week", AsyncMock(return_value={"days": []})) as schedule, \
             patch("app.routers.anime.anime_service.browse", AsyncMock(return_value={"items": [{"id": 113415}]})) as catalog:
            home = await self.client.get("/api/anime/homepage")
            week = await self.client.get("/api/anime/schedule?day=2026-11-05")
            series = await self.client.get("/api/anime/catalog?page=2&page_size=12&genre=Action&sort=SCORE_DESC")
        self.assertEqual([home.status_code, week.status_code, series.status_code], [200, 200, 200])
        self.assertEqual(home.json()["featured"][0]["id"], 113415)
        homepage.assert_awaited_once_with()
        schedule.assert_awaited_once_with(date(2026, 11, 5))
        catalog.assert_awaited_once_with(2, 12, None, "Action", None, None, None, None, "SCORE_DESC")

    async def test_invalid_catalog_filters_are_rejected_before_provider_lookup(self):
        with patch("app.routers.anime.anime_service.browse", AsyncMock()) as catalog:
            for query in ("page=0", "page_size=500", "sort=bad", "season=bad", "year=0"):
                with self.subTest(query=query):
                    response = await self.client.get(f"/api/anime/catalog?{query}")
                    self.assertEqual(response.status_code, 422)
        catalog.assert_not_awaited()

    async def test_provider_outage_is_reported_as_retryable_service_failure(self):
        with patch("app.routers.anime.anime_service.get_homepage", AsyncMock(side_effect=RuntimeError("offline"))):
            response = await self.client.get("/api/anime/homepage")
        self.assertEqual(response.status_code, 502)

    async def test_failed_discovery_responses_are_not_cached_as_missing_anime(self):
        from app.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/anime/catalog?page=0")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
