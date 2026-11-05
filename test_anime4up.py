import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.anime4up import _anime_cache, get_anime4up_episodes, resolve_sources


class AlternativeProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _anime_cache.clear()

    async def test_hunter_alternative_provider_follows_verified_episode_and_resolves_players(self):
        def handler(request):
            if request.url.path.startswith("/anime/"):
                return httpx.Response(200, text='<a href="https://myanimelist.net/anime/11061">MAL</a>' +
                    '[{"url":"/episode/hunter-x-hunter-2011-ep-1","number":1,"title":"Episode 1"}]')
            self.assertEqual(request.url.path, "/episode/hunter-x-hunter-2011-ep-1")
            return httpx.Response(200, text='<button data-ep-url="https://share4max.com/iframe/example"></button>')
        source = {"name": "1080p", "type": "direct", "url": "https://a.mxcontent.net/episode.mp4"}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("app.services.anime4up.resolve_player", new_callable=AsyncMock, return_value=[source]):
                result = await resolve_sources(client, 11061, "Hunter x Hunter (2011)", None, 1)
        self.assertEqual(result, [source])

    async def test_partial_mal_id_cannot_match_a_different_title(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            text='<a href="https://myanimelist.net/anime/1234">MAL</a>[{"url":"/episode/wrong-ep-1","number":1,"title":"1"}]'))) as client:
            self.assertEqual(await get_anime4up_episodes(client, 123, "Example", None), {})


if __name__ == "__main__":
    unittest.main()
