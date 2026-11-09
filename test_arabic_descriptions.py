import asyncio
import copy
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, Mock
from app.services.arabic_descriptions import ArabicDescriptions, is_arabic, factual_summary, clean_synopsis
from app.config import settings

ARABIC="ينطلق ساحر شاب مع أصدقائه لحماية قريتهم من عدو قديم، ويكتشفون خلال الرحلة معنى الشجاعة والصداقة."
ENGLISH="A young wizard travels with his friends to protect their village from an ancient enemy."
CURRENT="As tyranny engulfs the Clover Kingdom, Asta and the Magic Knights launch a desperate raid on the Spade Kingdom to rescue their squad captains from the Dark Triad. But their mission leads them into a brutal battle against forces beyond anything they’ve ever faced. Now, Asta must rise above impossible odds to protect his friends and honor the promise he made to Yuno.\n<br><br>\n(Source: Crunchyroll)"

class ArabicDescriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service=ArabicDescriptions()
        self.temp=tempfile.TemporaryDirectory();directory=Path(self.temp.name)
        (directory/'model').mkdir();(directory/'model/model.bin').touch()
        self.config=patch.object(settings,'ARABIC_TRANSLATION_MODEL_DIR',str(directory));self.config.start()
        self.cache={}
        async def get(key): return self.cache.get(key)
        async def put(key,value,ttl): self.cache[key]=value
        self.get=patch('app.services.db_cache.db_cache.get',AsyncMock(side_effect=get));self.put=patch('app.services.db_cache.db_cache.set',AsyncMock(side_effect=put))
        self.get.start();self.put.start()
    async def asyncTearDown(self):
        self.get.stop();self.put.stop();self.config.stop();self.service._executor.shutdown(wait=True);self.temp.cleanup()

    async def test_current_reported_synopsis_uses_reviewed_arabic_text(self):
        item={'id':195604,'title':'Black Clover Season 2','description':CURRENT,'media_type':'SERIES'}
        await self.service.enrich(item)
        self.assertEqual(item['description_ar_kind'],'reviewed')
        self.assertIn('مملكة سبيد',item['description'])
        self.assertNotIn('As tyranny',item['description'])
        self.assertTrue(is_arabic(item['description']))

    async def test_unknown_synopsis_is_translated_once_cached_and_invalidated_by_source_hash(self):
        original={'id':999993,'title':'Wizard','media_type':'SERIES','description':ENGLISH}
        with patch.object(self.service,'_translate',Mock(return_value=ARABIC)) as translator:
            for _ in range(2):
                item=copy.deepcopy(original);await self.service.enrich(item)
                self.assertEqual(item['description_ar_kind'],'machine')
            self.assertEqual(translator.call_count,1)
            await self.service.enrich({**original,'description':ENGLISH+' Their adventure begins.'})
            self.assertEqual(translator.call_count,2)

    async def test_concurrent_requests_share_translation_job(self):
        with patch.object(self.service,'_translate',Mock(return_value=ARABIC)) as translator:
            items=[{'id':999994,'title':'Wizard','media_type':'SERIES','description':ENGLISH} for _ in range(3)]
            await asyncio.gather(*(self.service.enrich(item) for item in items))
            self.assertEqual(translator.call_count,1)
            self.assertTrue(all(item['description']==ARABIC for item in items))

    async def test_translation_failure_keeps_foreign_text_out_of_public_description(self):
        original={'id':999995,'title':'Wizard','media_type':'SERIES','description':ENGLISH}
        with patch.object(self.service,'_translate',Mock(side_effect=RuntimeError('model unavailable'))) as translator:
            item = await self.service.enrich(copy.deepcopy(original))
            await self.service.enrich(copy.deepcopy(original))
            translator.assert_called_once()
            await self.service.enrich({**original, 'description': ENGLISH + ' The story has changed.'})
            self.assertEqual(translator.call_count, 2)
        self.assertEqual(item['description_ar_kind'],'facts')
        self.assertTrue(is_arabic(item['description']))
        self.assertNotIn(ENGLISH,item['description'])
        self.assertEqual(item['description_original'],ENGLISH)

    def test_copied_english_sentence_retries_shorter_clauses(self):
        def encode(value, out_type):
            return [part.split() for part in value] if isinstance(value, list) else value.split()
        source = "A young wizard travels with friends, and they protect the village together."
        self.service._tokenizer = SimpleNamespace(encode=encode, decode=lambda tokens: " ".join(tokens))
        row = lambda value: SimpleNamespace(hypotheses=[value.split()])
        self.service._translator = SimpleNamespace(translate_batch=Mock(side_effect=[
            [row(source)], [row("ينطلق ساحر شاب مع أصدقائه،"), row("ويحمون القرية معًا.")],
        ]))
        result = self.service._translate(source)
        self.assertEqual(self.service._translator.translate_batch.call_count, 2)
        self.assertEqual(result, "ينطلق ساحر شاب مع أصدقائه، ويحمون القرية معًا.")

    async def test_local_identifier_and_changed_source_do_not_reuse_reviewed_anime_plot(self):
        with patch.object(settings,'ARABIC_TRANSLATION_ENABLED',False):
            for value in ({'title_namespace':'local'},{'description':'A completely different season and story is starting today.'}):
                item={'id':195604,'title':'Unrelated work','media_type':'SERIES','description':CURRENT,**value}
                await self.service.enrich(item)
                self.assertEqual(item['description_ar_kind'],'facts')

    async def test_japanese_text_is_not_sent_to_english_translation_model(self):
        with patch.object(self.service,'_translate',Mock()) as translator:
            item={'id':999996,'title':'Wizard','media_type':'SERIES','description':'これは新しい冒険の物語です。'}
            await self.service.enrich(item)
        translator.assert_not_called()
        self.assertTrue(is_arabic(item['description']))

    async def test_provided_arabic_story_is_preserved_and_source_markup_is_cleaned(self):
        item={'id':999997,'title':'Wizard','media_type':'SERIES','description':ARABIC}
        await self.service.enrich(item)
        self.assertEqual(item['description_ar_kind'],'provided')
        self.assertEqual(item['description'],ARABIC)
        self.assertEqual(clean_synopsis('A story.<br><br>(Source: Publisher)'), 'A story.')
        self.assertFalse(is_arabic('An English plot with one Arabic word: مرحبا'))

if __name__=='__main__': unittest.main()
