import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import httpx

from app.services.airing_schedule import AiringScheduleService, CAIRO
from app.services.anime_service import AnimeService


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def test_week_starts_on_saturday_even_across_month_boundary(self):
        self.assertEqual(AiringScheduleService.week_start(date(2026, 11, 4)), date(2026, 10, 31))
        self.assertEqual(AiringScheduleService.week_start(date(2026, 10, 31)), date(2026, 10, 31))

    async def test_pagination_midnight_timezone_and_duplicates(self):
        start = datetime(2026, 10, 31, tzinfo=CAIRO)
        def event(id, moment, adult=False):
            return {"id": id, "airingAt": int(moment.timestamp()), "episode": 4,
                    "media": {"id": id, "title": {"english": "Anime"}, "isAdult": adult,
                              "coverImage": {"large": "https://images.example/poster.jpg"}, "genres": []}}
        first = event(1, start)
        second = event(2, start + timedelta(days=1, hours=1))
        def handler(request):
            import json
            variables = json.loads(request.content)["variables"]
            self.assertEqual(variables["from"], int(start.timestamp()) - 1)
            self.assertEqual(variables["to"], int((start + timedelta(days=7)).timestamp()))
            page = variables["page"]
            events = [first, event(3, start, True)] if page == 1 else [first, second, event(4, start + timedelta(days=7))]
            return httpx.Response(200, json={"data": {"Page": {"pageInfo": {"hasNextPage": page == 1}, "airingSchedules": events}}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        service = AiringScheduleService()
        with patch("app.services.airing_schedule.httpx.AsyncClient", return_value=client):
            result = await service.get_week(date(2026, 11, 4))
        self.assertEqual(result["timezone"], "Africa/Cairo")
        self.assertEqual(result["days"][0]["name"], "السبت")
        self.assertEqual(result["days"][0]["items"][0]["local_time"], "00:00")
        self.assertEqual(result["days"][1]["items"][0]["local_time"], "01:00")
        self.assertEqual(sum(len(day["items"]) for day in result["days"]), 2)

    async def test_outage_preserves_cached_schedule_and_marks_it_stale(self):
        service = AiringScheduleService()
        service._cache["2026-10-31"] = (0, {"week_start": "2026-10-31", "days": [], "stale": False})
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503)))
        with patch("app.services.airing_schedule.httpx.AsyncClient", return_value=client):
            result = await service.get_week(date(2026, 11, 4))
        self.assertTrue(result["stale"])

    async def test_hero_uses_latest_published_works_with_verified_sources(self):
        entries = [{"mal": 10, "episode": 3, "source": "https://animetna.com/episode/new/"},
                   {"mal": 11, "episode": 2, "source": "https://animetna.com/episode/broken/"}]
        def handler(request):
            return httpx.Response(200, json={"data": {"Page": {"media": [
                {"id": 1, "idMal": 10, "title": {"english": "Latest available"}, "format": "TV", "episodes": 12, "startDate": {"year": 2026, "month": 10, "day": 1}},
                {"id": 2, "idMal": 11, "title": {"english": "Unavailable"}, "format": "TV", "episodes": 12, "startDate": {"year": 2026, "month": 11, "day": 1}},
            ]}}})
        async def resolve(client, source, mal, episode):
            return [{"type": "hls", "url": "https://media.example/video"}] if mal == 10 else []
        async def catalog(client):
            return entries
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.services.anime_service.httpx.AsyncClient", return_value=client), patch("app.services.anime_service.latest_catalog", side_effect=catalog), patch("app.services.anime_service.resolve_episode_page", side_effect=resolve):
            result = await AnimeService().get_homepage()
        self.assertEqual([item["id"] for item in result["featured"]], [1])
        self.assertEqual(result["featured"][0]["available_episode"], 3)


if __name__ == "__main__":
    unittest.main()
