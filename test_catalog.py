import json
import unittest
from unittest.mock import patch, AsyncMock
import httpx
from app.services.anime_service import AnimeService
from app.services.animetna import page_episode_index

class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_and_page_are_sent_to_provider(self):
        def handler(request):
            variables=json.loads(request.content)["variables"]
            self.assertEqual(variables, {"page":3,"perPage":24,"sort":["SCORE_DESC"],"genre":"Action","seasonYear":2026,"season":"FALL","status":"RELEASING","format":"TV"})
            return httpx.Response(200,json={"data":{"Page":{"media":[],"pageInfo":{"total":100,"currentPage":3,"perPage":24,"lastPage":5}}}})
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.anime_service.httpx.AsyncClient",return_value=client):
            result=await AnimeService().browse(3,24,None,"Action",2026,"FALL","RELEASING","TV","SCORE_DESC")
        self.assertEqual(result["page"],3)
        self.assertEqual(result["total_pages"],5)

    def test_ongoing_series_with_unknown_total_keeps_all_aired_episodes(self):
        item=AnimeService._format_media({"id":21,"format":"TV","status":"RELEASING","episodes":None,"nextAiringEpisode":{"episode":1156,"airingAt":1}})
        self.assertEqual(item["media_type"],"SERIES")
        self.assertEqual(len(item["episodes"]),1155)
        self.assertFalse(item["episodes"][0]["direct_streamable"])

    def test_index_keeps_oldest_and_newest_episode_and_deduplicates_aliases(self):
        markup='<a href="https://animetna.com/episode/example-episode-1/">شاهد الحلقة الأولى</a><a href="https://animetna.com/episode/example-episode-1/">الحلقة 1</a><a href="https://animetna.com/episode/example-episode-170/">الحلقة 170</a>'
        self.assertEqual(sorted(page_episode_index(markup)),[1,170])

    async def test_unaired_episodes_are_disabled_in_index(self):
        service=AnimeService()
        service._anime_cache[1]={"id":1,"id_mal":10,"title":"Example","total_episodes":12,"next_airing_episode":6,"status":"RELEASING","media_type":"SERIES"}
        async def source(*args):return {1:"url",5:"url"}
        with patch("app.services.anime_service.get_episode_index",side_effect=source), patch("app.services.anime_service.anime3rb_episode_index",new_callable=AsyncMock,return_value={}):
            result=await service.episodes_for_anime(1)
        self.assertEqual(result["total"],12)
        self.assertEqual(result["episodes"][4]["state"],"listed")
        self.assertEqual(result["episodes"][5]["state"],"upcoming")

if __name__=="__main__":unittest.main()
