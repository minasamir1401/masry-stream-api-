import base64
import codecs
import json
import unittest

import httpx

from app.services.voe import player_config, resolve


def public_config(data):
    encoded = base64.b64encode(json.dumps(data).encode()).decode()[::-1]
    encoded = "".join(chr(ord(char) + 3) for char in encoded)
    encoded = base64.b64encode(encoded.encode()).decode()
    encoded = codecs.encode("!!".join(encoded[i:i+4] for i in range(0, len(encoded), 4)), "rot_13")
    return '<script type="application/json">' + json.dumps([encoded]) + '</script>'


class VoeSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_playback_playlist_is_used_without_loading_ads_or_download_only_url(self):
        requested = []
        playlist = "https://cdn.cloudwindow-route.com/master.m3u8?token=original"
        def handler(request):
            requested.append(str(request.url))
            if request.url.host == "voe.sx":
                return httpx.Response(200, text="window.location.href = 'https://shannoncoldalready.com/e/abc123';")
            if request.url.host == "shannoncoldalready.com":
                return httpx.Response(200, text='<script src="https://ads.invalid/popup.js"></script>' + public_config({
                    "source": playlist, "direct_access_allowed": False, "direct_access_url": "https://download.invalid/paid.mp4"}))
            if request.url.path == "/master.m3u8":
                self.assertEqual(request.url.params["token"], "original")
                return httpx.Response(200, text="#EXTM3U\n#EXTINF:1400,\nsegment.ts\n#EXT-X-ENDLIST")
            return httpx.Response(206, content=b"\x47" * 64)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await resolve(client, "https://voe.sx/e/abc123", "VOE 720p")
        self.assertEqual(result["type"], "hls")
        self.assertEqual(result["url"], playlist)
        self.assertTrue(all("ads.invalid" not in url and "download.invalid" not in url for url in requested))

    async def test_untrusted_redirect_cannot_be_requested(self):
        requested = []
        def handler(request):
            requested.append(str(request.url))
            return httpx.Response(200, text="window.location.href = 'https://127.0.0.1/e/abc123';")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertIsNone(await resolve(client, "https://voe.sx/e/abc123", "VOE"))
        self.assertEqual(len(requested), 1)

    def test_bad_player_data_is_not_treated_as_a_playable_source(self):
        self.assertIsNone(player_config('<script type="application/json">["invalid"]</script>'))
        self.assertIsNone(player_config(public_config({"direct_access_url": "https://example.com/a.mp4"})))


if __name__ == "__main__":
    unittest.main()
