import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.services.animetna import collect_available, direct_video, is_anime4up_host, mixdrop_video, playable_hls, resolve_servers, vidara_video
from app.services.anime_resolver import AnimeStreamResolver


class PlaybackRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unresponsive_mirror_does_not_discard_a_completed_source(self):
        cancelled = asyncio.Event()
        async def stuck():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        async def working():
            return {"name": "720p", "url": "https://a.mp4upload.com/video.mp4"}
        results = await collect_available([stuck(), working()], timeout=0.02)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "720p")
        self.assertTrue(cancelled.is_set())

    async def asyncSetUp(self):
        from app.services.animetna import _episode_indexes
        _episode_indexes.clear()
        for provider in ("resolve_anime3rb", "resolve_anime4up", "resolve_anime4up_live"):
            mock = patch(f"app.services.anime_resolver.{provider}", new_callable=AsyncMock, return_value=[])
            mock.start()
            self.addCleanup(mock.stop)

    async def test_rotated_vidara_cdn_preserves_all_three_qualities(self):
        def handler(request):
            if request.url.path == "/api/stream":
                return httpx.Response(200, json={"streaming_url": "https://s14-s1001604.97bf1.com/master.m3u8?token=original"})
            if request.url.path == "/master.m3u8":
                self.assertEqual(request.url.params["token"], "original")
                return httpx.Response(200, text="#EXTM3U\n#EXTINF:1200,\nsegment.ts\n#EXT-X-ENDLIST")
            return httpx.Response(206, content=b"\x47" * 64)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await asyncio.gather(*(vidara_video(client, "https://vidaraa.cc/e/abc123", f"Vidara {quality}p") for quality in (1080, 720, 480)))
        self.assertEqual([source["name"] for source in result], ["Vidara 1080p", "Vidara 720p", "Vidara 480p"])

    async def test_mixdrop_extracts_signed_video_without_executing_ads(self):
        requested = []
        media = "https://a-delivery30.mxcontent.net/v2/episode.mp4?token=original"
        def handler(request):
            requested.append(str(request.url))
            if request.url.host == "mixdrop.ag":
                return httpx.Response(200, text="<script src='https://ads.invalid/pop.js'></script>" +
                    "eval(function(p,a,c,k,e,d){}('0.1=\"https://a-delivery30.mxcontent.net/v2/episode.mp4?token=original\";',10,2,'MDCore|wurl'.split('|'),0,{}))")
            self.assertEqual(str(request.url), media)
            return httpx.Response(206, content=b"\x00\x00\x00\x20ftypisom")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = await mixdrop_video(client, "https://mixdrop.ag/e/abc123", "Mixdrop 720p")
        self.assertEqual(source, {"name": "Mixdrop 720p", "type": "direct", "url": media, "source_url": "https://mixdrop.ag/e/abc123"})
        self.assertEqual(len(requested), 2)

    async def test_embed_result_does_not_end_search_before_ad_free_alternative(self):
        resolver = AnimeStreamResolver()
        direct = {"name": "720p", "type": "direct", "url": "https://a.mxcontent.net/episode.mp4"}
        async def slow_provider(*args):
            await asyncio.sleep(0.02)
            return [direct]
        with patch("app.services.anime_resolver.resolve_servers", new_callable=AsyncMock, return_value=[{"name": "ads", "type": "embed", "url": "https://share4max.com/iframe/abc"}]):
            with patch("app.services.anime_resolver.resolve_anime4up", side_effect=slow_provider):
                result = await resolver.resolve_streams_for_anime(123, 123, "Example", "Example")
        self.assertEqual(result["servers"], [direct])

    async def test_slower_provider_adds_another_quality_after_first_playable_result(self):
        resolver = AnimeStreamResolver()
        first = {"name": "1080p", "type": "hls", "url": "https://cdn1.friwduh.shop/full.m3u8"}
        second = {"name": "480p", "type": "direct", "url": "https://a.mxcontent.net/low.mp4"}
        async def slow_provider(*args):
            await asyncio.sleep(1.7)
            return [second]
        with patch("app.services.anime_resolver.resolve_servers", new_callable=AsyncMock, return_value=[first]):
            with patch("app.services.anime_resolver.resolve_anime4up", side_effect=slow_provider):
                result = await resolver.resolve_streams_for_anime(123)
        self.assertEqual(result["servers"], [first, second])

    async def test_direct_episode_links_are_not_discarded_by_alternative_provider(self):
        resolver = AnimeStreamResolver()
        episodes = [{"episode_number": "7", "episode_urls": [
            {"episode_url": "https://sendvid.com/embed/abc123"},
            {"episode_url": "https://mp4upload.com/embed-abc123.html"},
        ]}]
        async with httpx.AsyncClient() as client:
            links = await resolver._extract_links_from_episodes(client, episodes, 7)
        self.assertEqual(len(links), 2)
        self.assertEqual(len(resolver._format_stream_servers(links)), 2)

    async def test_thirty_second_subscription_preview_is_not_a_full_episode(self):
        def handler(request):
            return httpx.Response(200,text="#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:10,\n1.ts\n#EXTINF:10,\n2.ts\n#EXTINF:10,\n3.ts\n#EXT-X-ENDLIST")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertFalse(await playable_hls(client,"https://cdn1.friwduh.shop/master.m3u8"))

    async def test_provider_requiring_external_playback_subscription_is_skipped(self):
        from app.services.animetna import resolve_player
        def handler(request):
            self.fail("Restricted provider must not be requested for direct playback")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(await resolve_player(client,{"type":"embed","url":"https://uqload.com/embed-abc.html"},123,1),[])

    async def test_playlist_must_contain_accessible_video_not_just_return_200(self):
        def handler(request):
            if request.url.path == "/master.m3u8":
                return httpx.Response(200, text="#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvideo.m3u8")
            if request.url.path == "/video.m3u8":
                return httpx.Response(200, text="#EXTM3U\n#EXTINF:10,\nsegment.ts")
            return httpx.Response(403, text="Unavailable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            self.assertFalse(await playable_hls(client, "https://cdn1.friwduh.shop/master.m3u8"))

    async def test_vidara_public_mirror_resolves_and_checks_media_segment(self):
        def handler(request):
            if request.url.path == "/api/stream":
                self.assertEqual(json.loads(request.content)["filecode"], "abc123")
                return httpx.Response(200, json={"streaming_url": "https://media.s1q2105.com/master.m3u8"})
            if request.url.path == "/master.m3u8":
                return httpx.Response(200, text="#EXTM3U\n#EXTINF:10,\nsegment.ts")
            self.assertEqual(request.headers["range"], "bytes=0-63")
            return httpx.Response(206, content=b"\x47" * 64)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await vidara_video(client, "https://vidaraa.cc/e/abc123", "Vidara")
            self.assertEqual(result["type"], "hls")

    async def test_html_error_page_is_not_a_working_playlist(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<!doctype html>File deleted")
        )) as client:
            self.assertFalse(await playable_hls(client, "https://cdn1.friwduh.shop/master.m3u8"))
            self.assertFalse(await playable_hls(client, "http://127.0.0.1/private"))

    def test_rotating_player_hosts_are_recognized_without_allowing_arbitrary_hosts(self):
        self.assertTrue(is_anime4up_host("4h.7saym84.shop"))
        self.assertTrue(is_anime4up_host("4a.czjty2z.shop"))
        self.assertFalse(is_anime4up_host("4h.7saym84.shop.evil.example"))
        self.assertFalse(is_anime4up_host("localhost"))

    async def test_one_broken_server_does_not_discard_another_working_server(self):
        def handler(request):
            if request.url.path.endswith("/search"):
                return httpx.Response(200, json={"items": [{"en": "Example", "url": "https://animetna.com/anime/example/"}]})
            if request.url.path.startswith("/anime/"):
                return httpx.Response(200, text='<a href="https://myanimelist.net/anime/123">MAL</a><a href="https://animetna.com/episode/example-episode-1/">شاهد الحلقة الأولى</a><a href="https://animetna.com/episode/example-episode-1/">الحلقة 1</a>')
            return httpx.Response(200, text='<button data-src="https://mp4upload.com/embed-broken.html"></button><button data-src="https://4h.7saym84.shop/Anime4up-S1/mal/123/1/sub/"></button>')

        async def player(client, server, mal, episode):
            if "mp4upload" in server["url"]:
                raise httpx.ReadTimeout("One provider is unavailable")
            return [{"name": "working", "type": "hls", "url": "https://cdn1.friwduh.shop/video"}]

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("app.services.animetna.resolve_player", side_effect=player):
                sources = await resolve_servers(client, 123, "Example", "Example", 1)
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["name"], "working")

    async def test_missing_episode_never_plays_a_different_episode_or_season(self):
        async def run(mal):
            def handler(request):
                if request.url.path.endswith("/search"):
                    return httpx.Response(200, json={"items": [{"en": "Example", "url": "https://animetna.com/anime/example/"}]})
                return httpx.Response(200, text=f'<a href="https://myanimelist.net/anime/{mal}">MAL</a><a href="https://animetna.com/episode/example-episode-4/">الحلقة 4</a>')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch("app.services.animetna.resolve_player", new_callable=AsyncMock) as player:
                    self.assertEqual(await resolve_servers(client, 123, "Example", "Example", 1), [])
                    player.assert_not_called()
        await run(123)
        await run(1234)

    async def test_manual_refresh_rechecks_previously_unavailable_sources(self):
        resolver = AnimeStreamResolver()
        source = {"name": "working", "type": "hls", "url": "https://cdn1.friwduh.shop/video"}
        with patch("app.services.anime_resolver.resolve_servers", new_callable=AsyncMock, side_effect=[[], [source]]) as resolve:
            with patch.object(resolver, "_build_search_queries", return_value=[]):
                first = await resolver.resolve_streams_for_anime(123)
                cached = await resolver.resolve_streams_for_anime(123)
                refreshed = await resolver.resolve_streams_for_anime(123, refresh=True)
        self.assertEqual(first["servers"], [])
        self.assertEqual(cached["servers"], [])
        self.assertEqual(refreshed["servers"], [source])
        self.assertEqual(resolve.await_count, 2)

    async def test_sendvid_is_resolved_without_loading_ad_scripts(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            if request.url.host == "sendvid.com":
                return httpx.Response(200, text='<script src="https://ads.invalid/pop.js"></script><source src="https://videos2.sendvid.com/video.mp4?token=public">')
            return httpx.Response(206, content=b"\x00\x00\x00\x20ftypisom")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = await direct_video(client, "https://sendvid.com/embed/abc123", "Sendvid")
        self.assertEqual(source["type"], "direct")
        self.assertEqual(len(requests), 2)
        self.assertTrue(all("ads.invalid" not in url for url in requests))

    async def test_mp4upload_uses_its_published_player_origin_for_video(self):
        def handler(request):
            if request.url.host == "mp4upload.com":
                return httpx.Response(200, text='player.src({src: "https://a1.mp4upload.com:183/video.mp4?token=original"});')
            self.assertEqual(request.headers["referer"], "https://www.mp4upload.com/")
            self.assertEqual(request.url.params["token"], "original")
            return httpx.Response(206, content=b"\x00\x00\x00\x20ftypisom")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = await direct_video(client, "https://mp4upload.com/embed-movie123.html", "MP4Upload")
        self.assertEqual(source["type"], "direct")

    async def test_simultaneous_viewers_share_one_source_check(self):
        resolver = AnimeStreamResolver()
        source = {"name": "working", "type": "hls", "url": "https://cdn1.friwduh.shop/video"}
        async def delayed(*args):
            await asyncio.sleep(0.01)
            return [source]
        with patch("app.services.anime_resolver.resolve_servers", side_effect=delayed) as resolve:
            results = await asyncio.gather(*(resolver.resolve_streams_for_anime(123) for _ in range(8)))
        self.assertEqual(resolve.await_count, 1)
        self.assertTrue(all(result["servers"] == [source] for result in results))

    async def test_leaving_a_page_does_not_cancel_another_viewers_source_check(self):
        resolver = AnimeStreamResolver()
        started, finish = asyncio.Event(), asyncio.Event()
        source = {"name": "working", "type": "hls", "url": "https://cdn1.friwduh.shop/video"}
        async def delayed(*args):
            started.set()
            await finish.wait()
            return [source]
        with patch("app.services.anime_resolver.resolve_servers", side_effect=delayed) as resolve:
            first = asyncio.create_task(resolver.resolve_streams_for_anime(123))
            await started.wait()
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(resolver.resolve_streams_for_anime(123))
            finish.set()
            result = await second
        self.assertEqual(result["servers"], [source])
        self.assertEqual(resolve.await_count, 1)

    async def test_temporary_search_outage_preserves_revalidated_previous_source(self):
        resolver = AnimeStreamResolver()
        source = {"name": "working", "type": "hls", "url": "https://cdn1.friwduh.shop/video"}
        resolver._stream_cache["123:1"] = (0, {"servers": [source]})
        with patch("app.services.anime_resolver.resolve_servers", new_callable=AsyncMock, return_value=[]):
            with patch.object(resolver, "_build_search_queries", return_value=[]):
                with patch("app.services.anime_resolver.revalidate_server", new_callable=AsyncMock, return_value=source):
                    result = await resolver.resolve_streams_for_anime(123)
        self.assertEqual(result["servers"], [source])


if __name__ == "__main__":
    unittest.main()
