import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.services.hls_gateway import relay, rewrite_playlist


class HlsGatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_playlist_variants_keys_and_segments_keep_original_signatures(self):
        text = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin?token=key-original"\n#EXT-X-MAP:URI="init.mp4"\nsegment.ts?token=original\n'
        rewritten, assets = rewrite_playlist(text, "https://cdn.tnmr.org/folder/index.m3u8", 21459, 12, "a" * 24)
        self.assertEqual(set(assets.values()), {"https://cdn.tnmr.org/folder/key.bin?token=key-original",
                         "https://cdn.tnmr.org/folder/init.mp4", "https://cdn.tnmr.org/folder/segment.ts?token=original"})
        self.assertEqual(rewritten.count("/api/anime/21459/hls/12?source="), 3)

    def test_untrusted_uri_cannot_turn_the_gateway_into_an_open_proxy(self):
        with self.assertRaises(HTTPException):
            rewrite_playlist("#EXTM3U\nhttps://127.0.0.1/private\n", "https://cdn.tnmr.org/master.m3u8", 1, 1, "a" * 24)

    async def test_root_playlist_registers_only_discovered_assets(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            text="#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=1920x1080,BANDWIDTH=1000\nindex.m3u8?token=original\n")))
        self.addAsyncCleanup(client.aclose)
        with patch("app.services.hls_gateway.playback_sources.get", new_callable=AsyncMock,
                   return_value={"type": "hls", "url": "https://cdn.tnmr.org/master.m3u8"}), \
             patch("app.services.hls_gateway.get_video_client", return_value=client), \
             patch("app.services.hls_gateway.db_cache.set_many", new_callable=AsyncMock) as save:
            response = await relay(21459, 12, "a" * 24)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"/api/anime/21459/hls/12?source=", response.body)
        self.assertEqual(list(save.call_args.args[0].values()), ["https://cdn.tnmr.org/index.m3u8?token=original"])

    async def test_media_asset_uses_same_origin_range_and_releases_response(self):
        def handler(request):
            self.assertEqual(request.headers["Range"], "bytes=0-63")
            return httpx.Response(206, content=b"\x47" * 64, headers={"Content-Type": "video/mp2t", "Content-Range": "bytes 0-63/1000"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client.aclose)
        with patch("app.services.hls_gateway.playback_sources.get", new_callable=AsyncMock, return_value={"type": "hls"}), \
             patch("app.services.hls_gateway.db_cache.get", new_callable=AsyncMock, return_value="https://cdn.tnmr.org/segment.ts"), \
             patch("app.services.hls_gateway.get_video_client", return_value=client):
            response = await relay(21459, 12, "a" * 24, "b" * 24, "bytes=0-63")
            self.assertEqual(len(b"".join([part async for part in response.body_iterator])), 64)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertFalse(client.is_closed)


if __name__ == "__main__":
    unittest.main()
