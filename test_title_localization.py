import json
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.title_localization import (
    names_for, localize_payload, normalize_search, provider_search, provider_filters, clean_title,
)


class TitleLocalizationTests(unittest.TestCase):
    def test_public_names_and_arabic_aliases_identify_same_work(self):
        en, ar, aliases, source = names_for({"id": 16498, "title": "Attack on Titan", "original_title": "進撃の巨人"})
        self.assertEqual((en, ar, source), ("Attack on Titan", "هجوم العمالقة", "curated"))
        for query in (en, ar, "هُجُوم العَمَالِقَة", "هجوم العمالقه", "اتاك اون تايتن"):
            self.assertEqual(provider_search(query), en)
        self.assertIn("اتاك اون تايتن", aliases)

    def test_exact_anilist_join_does_not_confuse_local_database_ids(self):
        data = json.loads((Path(__file__).parent / "app/title_data/anime_titles.json").read_text(encoding="utf-8"))
        anime_id, label = next(iter(data.items()))
        public = names_for({"id": int(anime_id), "title": "Unmapped Example"})
        self.assertEqual(public[1], clean_title(label["ar"]))
        self.assertEqual(public[3], "wikidata")
        local = names_for({"id": int(anime_id), "title": "Unmapped Example", "title_namespace": "local"})
        self.assertEqual(local[3], "phonetic")

    def test_schedule_uses_anime_identifier_not_airing_event_identifier(self):
        event = {"id": 1535, "anime_id": 16498, "title": "Attack on Titan", "poster_url": "poster"}
        localize_payload({"days": [{"items": [event]}]})
        self.assertEqual(event["title_ar"], "هجوم العمالقة")
        self.assertNotEqual(event["title_ar"], "مذكرة الموت")

    def test_unknown_names_have_explicit_phonetic_source_and_keep_provider_matching_name(self):
        item = {"id": 99999999, "title": "Future Adventure", "title_romaji": "Mirai no Bouken", "original_title": "未来冒険", "media_type": "SERIES"}
        localize_payload(item)
        self.assertEqual(item["title_ar_source"], "phonetic")
        self.assertTrue(item["title_ar"])
        self.assertEqual(item["title_romaji"], "Mirai no Bouken")
        self.assertNotIn("未来冒険", item["original_title"])
        localize_payload(item)
        self.assertEqual(item["title_ar_source"], "phonetic")

    def test_mixed_scripts_and_bidi_controls_do_not_enter_public_name(self):
        self.assertEqual(clean_title("Attack 進撃 \u202eTitan"), "Attack Titan")
        self.assertEqual(clean_title("Hunter x Hunter (2011)"), "Hunter x Hunter (2011)")
        self.assertEqual(normalize_search("هُجُوم العَمَالِقَة"), normalize_search("هجوم العمالقه"))
        self.assertEqual(names_for({"id": 1, "title": "هجوم العمالقة", "title_namespace": "local"})[0], "Attack on Titan")

    def test_arabic_partial_search_includes_matching_seasons_with_exact_ids(self):
        names_for({"id": 16498, "title": "Attack on Titan"})
        names_for({"id": 9999999, "title": "Attack on Titan Season 2"})
        self.assertEqual(provider_filters("هجوم العمالقه"), {"ids": [16498]})
        matches = provider_filters("هجوم")["ids"]
        self.assertIn(16498, matches)
        self.assertIn(9999999, matches)


class TitleSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_results_are_bilingual_for_both_catalog_query_languages(self):
        import httpx
        from app.services.anime_service import AnimeService
        service = AnimeService()
        raw = {"id": 1535, "title": {"english": "Death Note", "romaji": "DEATH NOTE", "native": "デスノート"}, "format": "TV", "episodes": 37, "status": "FINISHED"}
        response = httpx.Response(200, request=httpx.Request("POST", "https://graphql.anilist.co"), json={"data": {"Page": {"media": [raw], "pageInfo": {"total": 1}}}})
        client = AsyncMock()
        client.post.return_value = response
        with patch("app.services.anime_service._get_client", AsyncMock(return_value=client)), patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=None)), patch("app.services.anime_service.db_cache.set", AsyncMock()), patch("app.services.anime_service.db_cache.set_many", AsyncMock()), patch("app.services.anime_override_service.anime_override_service.get_hidden_anime_ids_sync", return_value=set()):
            for query in ("Death Note", "مذكرة الموت", "مذكرة"):
                result = await service.browse(search=query)
                self.assertEqual(result["items"][0]["id"], 1535)
                self.assertEqual(result["items"][0]["title_ar"], "مذكرة الموت")
                self.assertEqual(result["items"][0]["title_english"], "Death Note")

    async def test_catalog_and_legacy_search_resolve_both_languages_to_same_work(self):
        from app.services.anime_service import AnimeService
        service = AnimeService()
        names_for({"id": 16498, "title": "Attack on Titan"})
        with patch.object(service, "_query_list", AsyncMock(return_value={"items": []})) as query:
            for text in ("هجوم العمالقة", "Attack on Titan", "اتاك اون تايتن"):
                await service.browse(search=text)
                expected = {"search": "Attack on Titan"} if text == "Attack on Titan" else {"ids": [16498]}
                variables = query.call_args.args[0]
                self.assertTrue(all(variables.get(key) == value for key, value in expected.items()))
                await service.search(text)
                variables = query.call_args.args[0]
                self.assertTrue(all(variables.get(key) == value for key, value in expected.items()))

    async def test_legacy_cached_list_is_localized_without_network_or_cache_reset(self):
        from app.services.anime_service import AnimeService
        service = AnimeService()
        variables = {"page": 1, "perPage": 24, "sort": ["POPULARITY_DESC"]}
        payload = {"items": [{"id": 1535, "title": "Death Note", "original_title": "デスノート", "media_type": "SERIES"}]}
        service._list_cache[json.dumps(variables, sort_keys=True)] = (time.monotonic() + 60, payload)
        with patch("app.services.anime_service._get_client", AsyncMock()) as network, patch("app.services.anime_override_service.anime_override_service.get_hidden_anime_ids_sync", return_value=set()):
            result = await service._query_list(variables)
        network.assert_not_awaited()
        self.assertEqual(result["items"][0]["title_ar"], "مذكرة الموت")
        self.assertEqual(result["items"][0]["original_title"], "مذكرة الموت")


if __name__ == "__main__":
    unittest.main()
