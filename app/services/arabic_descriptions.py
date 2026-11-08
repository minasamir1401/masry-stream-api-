"""Arabic-first synopses, reviewed overrides and cached local EN→AR translation.

No network calls or model downloads occur during translation. Missing models
produce labelled Arabic work facts, never the original foreign synopsis.
"""
import asyncio
import hashlib
import html
import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import settings
from app.services.title_localization import names_for

logger=logging.getLogger(__name__)
REVIEWED=json.loads((Path(__file__).resolve().parents[1]/"title_data/synopses_ar.json").read_text(encoding="utf-8"))
GENRES={"Action":"أكشن","Adventure":"مغامرات","Fantasy":"خيال","Drama":"دراما","Comedy":"كوميديا","Romance":"رومانسي","Mystery":"غموض","Sports":"رياضة","Sci-Fi":"خيال علمي","Supernatural":"خارق للطبيعة","Thriller":"تشويق","Horror":"رعب"}


def clean_synopsis(value):
    text=html.unescape(str(value or ""))
    text=re.sub(r"<br\s*/?>|</(?:p|div)>","\n",text,flags=re.I)
    text=re.sub(r"<[^>]*>","",text)
    text=re.sub(r"(?:\(|^|\n)\s*Source\s*:.*$","",text,flags=re.I|re.S)
    return re.sub(r"\s+"," ",text).strip()[:6000]


def is_arabic(value):
    letters=[char for char in str(value or "") if char.isalpha()]
    return len(letters)>=12 and sum("\u0600"<=char<="\u06ff" for char in letters)/len(letters)>=.65


def is_english_source(value):
    letters=[char for char in value if char.isalpha()]
    return len(letters)>=12 and sum("LATIN" in unicodedata.name(char,"") for char in letters)/len(letters)>=.85


def factual_summary(item):
    _,ar,_,_=names_for(item)
    genres=[GENRES.get(value.get("name") if isinstance(value,dict) else value) for value in item.get("genres") or []]
    genres="، ".join(value for value in genres if value)
    return f"{ar} {'فيلم أنمي' if item.get('media_type')=='MOVIE' else 'مسلسل أنمي'}"+(f" من إنتاج {item['release_year']}" if item.get("release_year") else "")+(f"، من تصنيفات {genres}" if genres else "")+". لا يتوفر ملخص قصصي بالعربية لهذا العمل حاليًا. يمكنك تصفّح معلوماته والحلقات التي عُرضت."


class ArabicDescriptions:
    def __init__(self):
        self._jobs={}
        self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="arabic-synopsis")
        self._translator=None
        self._tokenizer=None

    def _translate(self,text):
        import ctranslate2
        import sentencepiece
        directory=Path(settings.ARABIC_TRANSLATION_MODEL_DIR)
        if self._translator is None:
            self._tokenizer=sentencepiece.SentencePieceProcessor(model_file=str(directory/"sentencepiece.model"))
            self._translator=ctranslate2.Translator(str(directory/"model"),device="cpu",compute_type="int8",inter_threads=1,intra_threads=1)
        # Sentence boundaries and bounded input avoid silently truncating long plots.
        chunks=[]
        for sentence in re.split(r"(?<=[.!?])\s+",text):
            tokens=self._tokenizer.encode(sentence,out_type=str)
            chunks.extend(tokens[offset:offset+350] for offset in range(0,len(tokens),350))
        translated=self._translator.translate_batch(chunks,beam_size=4,max_decoding_length=700)
        result=" ".join(self._tokenizer.decode(row.hypotheses[0]) for row in translated)
        if not is_arabic(result): raise ValueError("Translation did not produce an Arabic synopsis")
        return clean_synopsis(result)

    async def _generate(self,key,text,anime_id):
        from app.services.db_cache import db_cache
        try:
            translated=await asyncio.get_running_loop().run_in_executor(self._executor,self._translate,text)
            result={"text":translated,"kind":"machine","source":f"https://anilist.co/anime/{anime_id}"}
            await db_cache.set(key,result,365*86400)
            return result
        except Exception as error:
            logger.info("Arabic synopsis unavailable for %s: %s",anime_id,type(error).__name__)
            return None
        finally:
            self._jobs.pop(key,None)

    async def describe(self,item):
        anime_id=item.get("id")
        original=clean_synopsis(item.get("description_original") or item.get("description"))
        digest=hashlib.sha256(original.encode()).hexdigest()[:24]
        if item.get("description_ar") and item.get("description_ar_hash")==digest and item.get("description_ar_kind")!="facts":
            return
        result=None
        if is_arabic(original): result={"text":original,"kind":"provided","source":None}
        elif item.get("title_namespace")!="local" and str(anime_id) in REVIEWED and REVIEWED[str(anime_id)].get("source_hash",digest)==digest: result=REVIEWED[str(anime_id)]
        elif is_english_source(original) and settings.ARABIC_TRANSLATION_ENABLED:
            from app.services.db_cache import db_cache
            key=f"description:ar:v1:{anime_id}:{digest}"
            result=await db_cache.get(key)
            if not result and (Path(settings.ARABIC_TRANSLATION_MODEL_DIR)/"model/model.bin").is_file():
                task=self._jobs.get(key)
                if task is None and len(self._jobs)<64:
                    task=asyncio.create_task(self._generate(key,original,anime_id));self._jobs[key]=task
                if task:
                    try: result=await asyncio.wait_for(asyncio.shield(task),timeout=3)
                    except asyncio.TimeoutError: pass
        if not result or not is_arabic(result.get("text")):
            result={"text":factual_summary(item),"kind":"facts","source":None}
        item.update(description_original=original,description_ar=result["text"],description=result["text"],description_ar_kind=result["kind"],description_ar_hash=digest,description_source_url=result.get("source"))

    async def enrich(self,payload):
        from pydantic import BaseModel
        if isinstance(payload,BaseModel):
            values=await self.enrich(payload.model_dump(mode="json"))
            return type(payload).model_validate(values)
        works=[]
        def collect(value):
            if isinstance(value,list):
                for item in value: collect(item)
            elif isinstance(value,dict):
                if isinstance(value.get("title"),str) and value.get("media_type") in ("MOVIE","SERIES") and not value.get("anime_id"):
                    works.append(value)
                else:
                    for item in value.values():
                        if isinstance(item,(dict,list)): collect(item)
        collect(payload)
        if works: await asyncio.gather(*(self.describe(item) for item in works))
        return payload


arabic_descriptions=ArabicDescriptions()
