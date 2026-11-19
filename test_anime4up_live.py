import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services import anime4up_live as live


class CurrentPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        live._origin = None
        live._indexes.clear()
        live._anime_meta.clear()
        for method in ("get", "set"):
            mocked = patch(f"app.services.anime4up_live.db_cache.{method}", new_callable=AsyncMock, return_value=None)
            mocked.start()
            self.addCleanup(mocked.stop)

    async def test_current_search_follows_mal_identity_and_opaque_episode_links(self):
        def handler(request):
            if request.url.host == "anime4up.rest":
                return httpx.Response(307, headers={"Location": "https://4h.example.shop/"})
            if request.url.params.get("s"):
                return httpx.Response(200, text='<a href="/anime/death-note/">DEATH NOTE</a>')
            if request.url.path == "/":
                return httpx.Response(200, text="Publisher")
            if request.url.path == "/anime/death-note/":
                return httpx.Response(200, text='<a href="https://myanimelist.net/anime/1535">MAL</a>'
                    '<a href="/episode/opaque-three/">الحلقة 3</a><a href="/episode/opaque-four/">الحلقة 4</a>')
            self.assertEqual(request.url.path, "/episode/opaque-four/")
            return httpx.Response(200, text='<button data-watch="https://voe.sx/e/newfile"></button>')
        source = {"name": "VOE", "type": "hls", "url": "https://a.cloudwindow-route.com/master.m3u8"}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("app.services.anime4up_live.resolve_player", new_callable=AsyncMock, return_value=[source]) as resolve:
                result = await live.resolve_sources(client, 1535, "Death Note", "DEATH NOTE", 4)
        self.assertEqual(result, [source])
        self.assertEqual(resolve.call_args.args[-1], 4)

    async def test_wrong_mal_identity_never_fetches_the_episode(self):
        live._origin = (float("inf"), "https://4h.example.shop")
        def handler(request):
            if request.url.params.get("s"):
                return httpx.Response(200, text='<a href="/anime/wrong/">Death Note</a>')
            self.assertEqual(request.url.path, "/anime/wrong/")
            return httpx.Response(200, text='<a href="https://myanimelist.net/anime/15350">MAL</a><a href="/episode/wrong/">الحلقة 4</a>')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(await live.resolve_sources(client, 1535, "Death Note", None, 4), [])

    def test_movie_episode_is_parsed_without_guessing_from_a_random_url_slug(self):
        self.assertEqual(live.episode_links('<a href="/episode/opaque/">الفلم 1</a>', "https://4h.example.shop"),
                         {1: "https://4h.example.shop/episode/opaque/"})

    async def test_pagination_jump_finds_episode_1_on_last_page_and_resolves_megamax_ad_free_only(self):
        live._origin = (float("inf"), "https://4h.example.shop")
        p1_links = "".join(f'<a href="/episode/hxh-{n}/">الحلقة {n}</a>' for n in range(148, 100, -1))
        def handler(request):
            if request.url.host == "4h.example.shop" and request.url.params.get("s"):
                return httpx.Response(200, text='<a href="/anime/hunter-x-hunter-2011/">Hunter x Hunter (2011)</a>')
            if request.url.path == "/anime/hunter-x-hunter-2011/":
                return httpx.Response(200, text='<a href="https://myanimelist.net/anime/11061">MAL</a>' + p1_links + '<a href="/anime/hunter-x-hunter-2011/page/4/">4</a>')
            if request.url.path == "/anime/hunter-x-hunter-2011/page/4/":
                return httpx.Response(200, text='<a href="/episode/hxh-1/">الحلقة 1</a>')
            if request.url.path == "/episode/hxh-1/":
                return httpx.Response(200, text=(
                    '<button data-watch="https://megamax.me/iframe/abc123"></button>'
                    '<button data-watch="https://ok.ru/videoembed/999"></button>'
                    '<button data-watch="https://playmogo.com/e/ads123"></button>'
                ))
            self.fail(f"Unexpected request: {request.url}")
        hls_source = {"name": "VOE — 1080p", "type": "hls", "url": "https://cdn.cloudwindow-route.com/master.m3u8"}
        async def fake_resolve_player(client, server, mal, episode):
            if "megamax.me" in server["url"]:
                return [hls_source]
            return []
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("app.services.anime4up_live.resolve_player", side_effect=fake_resolve_player):
                result = await live.resolve_sources(client, 11061, "Hunter x Hunter (2011)", "HUNTER×HUNTER (2011)", 1)
        self.assertEqual(result, [hls_source])


if __name__ == "__main__":
    unittest.main()
