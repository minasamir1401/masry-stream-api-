import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from app.services.hero_images import HeroImages, kitsu_images, valid_image, CACHE_SECONDS

KITSU='https://media.kitsu.app/anime/42765/cover_images/original.jpg'
LARGE='https://media.kitsu.app/anime/42765/cover_images/large.jpg'
BANNER='https://s4.anilist.co/file/anilistcdn/media/anime/banner/113415-example.jpg'
ITEM={'id':113415,'id_mal':40748,'title':'Jujutsu Kaisen','banner_url':BANNER}


def payload(mal='40748',related='42765',cover=True):
    return {'data':[{'id':'mapping','type':'mappings','attributes':{'externalSite':'myanimelist/anime','externalId':mal},'relationships':{'item':{'data':{'type':'anime','id':related}}}}], 'included':[{'id':'42765','type':'anime','attributes':{'canonicalTitle':'Jujutsu Kaisen','coverImage':{'original':KITSU,'large':LARGE} if cover else None}}]}


class HeroImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def service(self,handler):
        return HeroImages(cache_dir=self.temp.name,transport=httpx.MockTransport(handler),request_interval=0)

    def test_original_cover_before_large_rendition(self):
        images=kitsu_images(payload(),40748)
        self.assertEqual([image['image_url'] for image in images],[KITSU,LARGE])
        self.assertEqual(images[0]['provider_id'],'42765')

    def test_wrong_mal_and_unlinked_season_are_rejected(self):
        self.assertEqual(kitsu_images(payload(mal='51009'),40748),[])
        self.assertEqual(kitsu_images(payload(related='45857'),40748),[])

    def test_poster_is_not_used_as_a_wide_cover(self):
        data=payload(cover=False)
        data['included'][0]['attributes']['posterImage']={'original':KITSU}
        self.assertEqual(kitsu_images(data,40748),[])

    def test_images_are_from_expected_hosts_and_same_anilist_id(self):
        self.assertTrue(valid_image(BANNER,'anilist',113415))
        self.assertFalse(valid_image(BANNER,'anilist',195604))
        self.assertFalse(valid_image('https://media.kitsu.app.evil.example/anime/1/cover_images/image.jpg','kitsu'))
        self.assertFalse(valid_image('https://[','kitsu'))

    async def test_exact_public_mapping_no_auth_and_same_work_fallback(self):
        def handler(request):
            self.assertNotIn('authorization',request.headers)
            self.assertEqual(request.url.params['filter[externalId]'],'40748')
            self.assertEqual(request.url.params['filter[externalSite]'],'myanimelist/anime')
            return httpx.Response(200,json=payload())
        result=await self.service(handler).get(ITEM)
        self.assertEqual(result['source'],'kitsu')
        self.assertEqual(result['image_url'],KITSU)
        self.assertEqual(result['alternatives'][-1]['image_url'],BANNER)
        self.assertEqual(result['anime_id'],113415)

    async def test_missing_kitsu_cover_uses_matching_anilist_banner(self):
        result=await self.service(lambda request:httpx.Response(200,json=payload(cover=False))).get(ITEM)
        self.assertEqual(result['source'],'anilist')
        self.assertEqual(result['image_url'],BANNER)

    async def test_provider_outage_uses_available_banner(self):
        result=await self.service(lambda request:httpx.Response(503)).get(ITEM)
        self.assertEqual(result['source'],'anilist')

    async def test_cache_survives_restart_and_mapping_change_invalidates_it(self):
        await self.service(lambda request:httpx.Response(200,json=payload())).get(ITEM)
        result=await self.service(lambda request:self.fail('Cache must avoid network')).get(ITEM)
        self.assertEqual(result['image_url'],KITSU)
        changed={**ITEM,'id_mal':999}
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'data':[]})
        self.assertEqual((await self.service(handler).get(changed))['source'],'anilist')
        self.assertEqual(len(calls),1)

    async def test_parallel_requests_share_one_provider_call(self):
        calls=[]
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(.01)
            return httpx.Response(200,json=payload())
        service=self.service(handler)
        results=await asyncio.gather(*(service.get(ITEM) for _ in range(8)))
        self.assertEqual(len(calls),1)
        self.assertTrue(all(result['image_url']==KITSU for result in results))

    async def test_expired_verified_cover_is_kept_during_outage(self):
        old={'anime_id':113415,'id_mal':40748,'image_url':KITSU,'source':'kitsu','cached_at':time.time()-CACHE_SECONDS-20}
        Path(self.temp.name,'113415-40748.json').write_text(json.dumps(old))
        result=await self.service(lambda request:httpx.Response(503)).get({**ITEM,'banner_url':None})
        self.assertTrue(result['stale'])
        self.assertEqual(result['image_url'],KITSU)

    async def test_endpoint_preserves_internal_identity(self):
        from app.main import app
        with patch('app.routers.anime.anime_service.get_by_id',new_callable=AsyncMock,return_value=ITEM), patch('app.routers.anime.hero_images.get',new_callable=AsyncMock,return_value={'image_url':KITSU,'source':'kitsu','stale':False,'alternatives':[]}) as provider:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response=await client.get('/api/anime/113415/hero-image')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['id_mal'],40748)
        self.assertEqual(response.json()['source'],'kitsu')
        provider.assert_awaited_once_with(ITEM)

if __name__=='__main__': unittest.main()
