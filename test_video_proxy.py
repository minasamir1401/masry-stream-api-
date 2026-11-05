import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from app.routers.anime import get_anime_video


class VideoProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_range_and_signed_url_are_preserved_and_connections_closed(self):
        url = "https://a.mxcontent.net/v2/episode.mp4?token=original"
        def handler(request):
            self.assertEqual(str(request.url), url)
            self.assertEqual(request.headers["range"], "bytes=2000000-4097151")
            return httpx.Response(206, content=b"video", headers={"Content-Range": "bytes 2000000-2000004/5000000"})
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(upstream.aclose)
        sources = {"servers": [{"type": "direct", "url": url}]}
        request = Request({"type": "http", "headers": [(b"range", b"bytes=2000000-")]})
        with patch("app.routers.anime.anime_service.get_stream_sources", new_callable=AsyncMock, return_value=sources):
            with patch("app.routers.anime.get_video_client", return_value=upstream):
                response = await get_anime_video(11061, 1, request, 0)
                data = b"".join([chunk async for chunk in response.body_iterator])
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-range"], "bytes 2000000-2000004/5000000")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(data, b"video")
        self.assertFalse(upstream.is_closed)

    async def test_unverified_urls_and_invalid_ranges_are_rejected(self):
        cases = [("https://127.0.0.1/private", b"bytes=0-", 404),
                 ("https://a.mxcontent.net/video.mp4", b"bytes=100-10", 416),
                 ("https://a.mxcontent.net/video.mp4", b"bytes=0-10,20-30", 416)]
        for url, byte_range, status in cases:
            with self.subTest(url=url, byte_range=byte_range):
                request = Request({"type": "http", "headers": [(b"range", byte_range)]})
                with patch("app.routers.anime.anime_service.get_stream_sources", new_callable=AsyncMock,
                           return_value={"servers": [{"type": "direct", "url": url}]}):
                    with self.assertRaises(HTTPException) as error:
                        await get_anime_video(11061, 1, request, 0)
                self.assertEqual(error.exception.status_code, status)

    async def test_upstream_failure_closes_connection_and_returns_retryable_error(self):
        upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(403)))
        self.addAsyncCleanup(upstream.aclose)
        sources = {"servers": [{"type": "direct", "url": "https://a.mxcontent.net/episode.mp4"}]}
        request = Request({"type": "http", "headers": []})
        with patch("app.routers.anime.anime_service.get_stream_sources", new_callable=AsyncMock, return_value=sources):
            with patch("app.routers.anime.get_video_client", return_value=upstream):
                with self.assertRaises(HTTPException) as error:
                    await get_anime_video(11061, 1, request, 0)
        self.assertEqual(error.exception.status_code, 502)
        self.assertFalse(upstream.is_closed)


if __name__ == "__main__":
    unittest.main()
