import unittest

import httpx

from app.services.animetna import lulustream_video


class LuluStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_hls_is_validated_without_executing_player_or_ads(self):
        requested = []
        def handler(request):
            requested.append(str(request.url))
            if request.url.host == "lulustream.com":
                return httpx.Response(302, headers={"Location": "https://luluvdo.com/e/file123"})
            if request.url.host == "luluvdo.com":
                return httpx.Response(200, text='<script src="https://ads.invalid/pop.js"></script>' +
                    "eval(function(p,a,c,k,e,d){}('0:[{1:\"https://cdn.tnmr.org/master.m3u8?token=original\"}]',10,2,'sources|file'.split('|'),0,{}))")
            if request.url.path == "/master.m3u8":
                self.assertEqual(request.url.params["token"], "original")
                return httpx.Response(200, text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1280x720\nindex.m3u8")
            if request.url.path == "/index.m3u8":
                return httpx.Response(200, text="#EXTM3U\n#EXTINF:1400,\nsegment.ts\n#EXT-X-ENDLIST")
            return httpx.Response(206, content=b"\x47" * 64)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = await lulustream_video(client, "https://lulustream.com/e/file123", "LuluStream 1080p")
        self.assertEqual(source["type"], "hls")
        self.assertEqual(source["name"], "LuluStream 720p")
        self.assertTrue(all("ads.invalid" not in url for url in requested))

    async def test_non_player_url_is_rejected(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.fail("Unexpected request"))) as client:
            self.assertIsNone(await lulustream_video(client, "https://127.0.0.1/e/file123", "LuluStream"))


if __name__ == "__main__":
    unittest.main()
