import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from app.routers.anime import get_anime_video
from app.services.playback_sources import PlaybackSources


class PlaybackSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved = {}
        self.store = PlaybackSources()
        async def get(key):
            return self.saved.get(key)
        async def set_value(key, value, ttl):
            self.saved[key] = value
        self.get_patch = patch("app.services.playback_sources.db_cache.get", new_callable=AsyncMock, side_effect=get)
        self.set_patch = patch("app.services.playback_sources.db_cache.set", new_callable=AsyncMock, side_effect=set_value)
        self.get_patch.start()
        self.set_patch.start()
        self.addCleanup(self.get_patch.stop)
        self.addCleanup(self.set_patch.stop)

    async def test_catalog_reordering_cannot_change_file_mid_playback(self):
        original = {"name": "720p", "type": "direct", "url": "https://a.mxcontent.net/original.mp4?token=one"}
        token = await self.store.register(128893, 2, original)
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            206, content=b"original-file", headers={"Content-Range": "bytes 100-112/1000"})
            if str(request.url) == original["url"] else self.fail("Playback switched to another file")))
        self.addAsyncCleanup(upstream.aclose)
        request = Request({"type": "http", "headers": [(b"range", b"bytes=100-112")]})
        with patch("app.routers.anime.playback_sources", self.store):
            with patch("app.routers.anime.anime_service.get_stream_sources", new_callable=AsyncMock) as catalog:
                with patch("app.routers.anime.get_video_client", return_value=upstream):
                    response = await get_anime_video(128893, 2, request, 0, source=token)
                    self.assertEqual(b"".join([part async for part in response.body_iterator]), b"original-file")
                catalog.assert_not_called()

    async def test_token_is_bound_to_its_anime_and_episode(self):
        token = await self.store.register(128893, 2, {"name": "720p", "type": "direct", "url": "https://a.mxcontent.net/episode.mp4"})
        self.assertIsNone(await self.store.get(128893, 3, token))
        self.assertIsNone(await self.store.get(166613, 2, token))
        with patch("app.routers.anime.playback_sources", self.store):
            with self.assertRaises(HTTPException) as error:
                await get_anime_video(128893, 3, Request({"type": "http", "headers": []}), 0, source=token)
        self.assertEqual(error.exception.status_code, 404)

    async def test_expired_signed_url_is_renewed_once_without_losing_the_byte_range(self):
        selected = {"name": "720p", "type": "direct", "url": "https://a.mp4upload.com/old.mp4"}
        renewed = {**selected, "url": "https://a.mp4upload.com/renewed.mp4"}
        calls = []
        def handler(request):
            calls.append(request.url.path)
            self.assertEqual(request.headers["range"], "bytes=100-163")
            if request.url.path == "/old.mp4":
                return httpx.Response(403)
            return httpx.Response(206, content=b"x" * 64, headers={"Content-Range": "bytes 100-163/1000"})
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(upstream.aclose)
        request = Request({"type": "http", "headers": [(b"range", b"bytes=100-163")]})
        with patch("app.routers.anime.playback_sources.get", new_callable=AsyncMock, return_value=selected):
            with patch("app.routers.anime.playback_sources.renew", new_callable=AsyncMock, return_value=renewed) as renew:
                with patch("app.routers.anime.get_video_client", return_value=upstream):
                    response = await get_anime_video(128893, 2, request, 0, source="a" * 24)
                    self.assertEqual(len(b"".join([part async for part in response.body_iterator])), 64)
                renew.assert_awaited_once()
        self.assertEqual(calls, ["/old.mp4", "/renewed.mp4"])

    async def test_renewal_resolves_the_same_published_file(self):
        selected = {"name": "720p", "type": "direct", "url": "https://a.mp4upload.com/old.mp4", "source_url": "https://mp4upload.com/embed-samefile.html"}
        renewed = {**selected, "url": "https://a.mp4upload.com/new.mp4"}
        token = await self.store.register(128893, 2, selected)
        with patch("app.services.playback_sources.resolve_player", new_callable=AsyncMock, return_value=[renewed]) as resolve:
            result = await self.store.renew(None, 128893, 2, token, selected)
        self.assertEqual(result, renewed)
        self.assertEqual(resolve.call_args.args[1]["url"], selected["source_url"])
        self.assertEqual(await self.store.get(128893, 2, token), renewed)


if __name__ == "__main__":
    unittest.main()
