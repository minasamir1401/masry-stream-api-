import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.routers.anime import get_anime_stream
from app.services.anime_service import AnimeService


class PlaybackMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_outage_is_not_cached_or_reported_as_missing_video(self):
        service = AnimeService()
        with patch.object(service, "get_by_id", new_callable=AsyncMock, return_value=None):
            with patch("app.services.anime_service.anime_stream_resolver.resolve_streams_for_anime", new_callable=AsyncMock) as resolve:
                result = await service.get_stream_sources(131573)
        self.assertEqual(result["availability"], "metadata_unavailable")
        resolve.assert_not_awaited()

    async def test_metadata_outage_returns_retryable_http_status(self):
        with patch("app.routers.anime.anime_service.get_stream_sources", new_callable=AsyncMock,
                   return_value={"availability": "metadata_unavailable", "servers": []}):
            with self.assertRaises(HTTPException) as error:
                await get_anime_stream(131573)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(error.exception.headers["Retry-After"], "15")

    async def test_catalog_discovery_persists_details_needed_by_playback(self):
        service = AnimeService()
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            "data": {"Page": {"pageInfo": {"total": 1, "currentPage": 1, "lastPage": 1, "perPage": 50},
                "media": [{"id": 131573, "idMal": 48561, "title": {"english": "JUJUTSU KAISEN 0", "romaji": "Jujutsu Kaisen 0"}, "format": "MOVIE"}]}}
        })))
        self.addAsyncCleanup(client.aclose)
        with patch("app.services.anime_service.db_cache.get", new_callable=AsyncMock, return_value=None), \
             patch("app.services.anime_service.db_cache.set", new_callable=AsyncMock), \
             patch("app.services.anime_service.db_cache.set_many", new_callable=AsyncMock) as persist, \
             patch("app.services.anime_service._get_client", new_callable=AsyncMock, return_value=client):
            result = await service._query_list({"page": 1, "perPage": 50})
        saved = persist.call_args.args[0]["anime:131573"]
        self.assertEqual(saved["id_mal"], 48561)
        self.assertEqual(saved["title_romaji"], "Jujutsu Kaisen 0")
        self.assertFalse(result["items"][0]["is_available"])


if __name__ == "__main__":
    unittest.main()
