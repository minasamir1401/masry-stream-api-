import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.anime_service import AnimeService


class HomepageTests(unittest.IsolatedAsyncioTestCase):
    async def test_seven_distinct_series_follow_episode_feed_instead_of_premiere_year(self):
        # The provider's metadata response is deliberately reversed. An old
        # show's newly added episode must still precede a recent premiere.
        entries = [{"mal": 100 + i, "episode": i + 4, "source": f"episode-{i}", "order": i} for i in range(9)]
        entries.insert(2, {"mal": 100, "episode": 1, "source": "duplicate", "order": 20})
        media = [{"id": 1000 + i, "idMal": 100 + i, "title": {"english": f"Anime {i}"},
                  "startDate": {"year": 2010 + i}, "format": "MOVIE" if i == 1 else "TV", "episodes": 1 if i == 1 else 12}
                 for i in reversed(range(9))]
        def handler(request):
            return httpx.Response(200, json={"data": {"Page": {"media": media}}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        # No server passing verification must shrink the episode discovery feed.
        with patch("app.services.anime_service.latest_catalog", AsyncMock(return_value=entries)), \
             patch("app.services.anime_service.resolve_episode_page", AsyncMock(return_value=[])), \
             patch("app.services.anime_service.httpx.AsyncClient", return_value=client):
            service = AnimeService()
            result = await service.get_homepage()
        featured = result["featured"]
        self.assertEqual([item["id"] for item in featured], [1000, 1002, 1003, 1004, 1005, 1006, 1007])
        self.assertEqual([item["available_episode"] for item in featured], [4, 6, 7, 8, 9, 10, 11])
        self.assertTrue(all(not item["direct_streamable"] for item in featured))
        self.assertEqual(len(result["spotlight"]), 9)
        self.assertIs(await service.get_homepage(), result)

    async def test_verified_playback_flag_is_only_kept_for_the_matching_anime(self):
        entries = [{"mal": 1, "episode": 5, "source": "first", "order": 0},
                   {"mal": 2, "episode": 6, "source": "second", "order": 1}]
        media = [{"id": i, "idMal": i, "format": "TV", "episodes": 12} for i in (1, 2)]
        async def resolve(client, source, mal, episode):
            return [{"type": "hls"}] if mal == 2 else []
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {"Page": {"media": media}}})))
        with patch("app.services.anime_service.latest_catalog", AsyncMock(return_value=entries)), \
             patch("app.services.anime_service.resolve_episode_page", side_effect=resolve), \
             patch("app.services.anime_service.httpx.AsyncClient", return_value=client):
            result = await AnimeService().get_homepage()
        self.assertFalse(result["featured"][0]["direct_streamable"])
        self.assertTrue(result["featured"][1]["direct_streamable"])
        self.assertEqual(result["featured"][1]["available_episode"], 6)


if __name__ == "__main__":
    unittest.main()
