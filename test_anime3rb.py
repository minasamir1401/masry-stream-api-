import html
import json
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from app.services.anime3rb import resolve_sources, episode_index, _titles

class PublicSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _titles.clear()

    async def test_only_free_sources_are_used_without_modifying_signed_parameters(self):
        free="https://video.vid3rb.com/video/abc?speed=162&token=original&expires=2000000000"
        premium="https://video.vid3rb.com/video/paid?premium=true"
        snapshot=html.escape(json.dumps({"data":{"video_number":"2","video_url":"https://video.vid3rb.com/player/abc-123?token=public"}}),quote=True)
        def handler(request):
            if request.url.path.startswith("/titles/"):
                return httpx.Response(200,text='<a href="https://myanimelist.net/anime/123">MAL</a><a href="https://anime3rb.com/episode/example/2">2</a>')
            if request.url.path.startswith("/episode/"):
                return httpx.Response(200,text='<div wire:snapshot="'+snapshot+'"></div>')
            sources=[{"src":premium,"premium":True,"label":"Premium"},{"src":free,"premium":False,"label":"480p"}]
            return httpx.Response(200,text="var video_sources = []; var video_sources = "+json.dumps(sources)+";")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("app.services.anime3rb.playable_mp4",new_callable=AsyncMock,return_value=True) as probe:
                result=await resolve_sources(client,123,"Example","Example",2)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]["url"],free)
        probe.assert_awaited_once()
        self.assertEqual(probe.call_args.args[1],free)

    async def test_wrong_season_is_rejected_by_mal_identity(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text='<a href="https://myanimelist.net/anime/1234">Different season</a>'))) as client:
            self.assertEqual(await resolve_sources(client,123,"Example","Example",2),[])

    async def test_numbered_season_alias_is_verified_by_mal_id(self):
        def handler(request):
            if request.url.path != "/titles/example-2nd-season":
                return httpx.Response(404)
            return httpx.Response(200,text='<a href="https://myanimelist.net/anime/123">MAL</a><a href="https://anime3rb.com/episode/example-2nd-season/2">2</a>')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await episode_index(client,123,"Example Season 2","Example 2")
        self.assertEqual(sorted(result),[2])

if __name__=="__main__":unittest.main()
