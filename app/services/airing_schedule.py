import asyncio
import time
from datetime import date, datetime, time as day_time, timedelta
from zoneinfo import ZoneInfo

import httpx

CAIRO = ZoneInfo("Africa/Cairo")
DAY_NAMES = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")
QUERY = """
query ($from: Int!, $to: Int!, $page: Int!) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    airingSchedules(airingAt_greater: $from, airingAt_lesser: $to, sort: TIME) {
      id airingAt episode
      media {
        id title { english romaji native } isAdult format duration genres averageScore
        bannerImage coverImage { large } siteUrl
      }
    }
  }
}
"""


class AiringScheduleService:
    def __init__(self):
        self._cache = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def week_start(selected: date):
        return selected - timedelta(days=(selected.weekday() - 5) % 7)

    async def get_week(self, selected: date | None = None):
        today = datetime.now(CAIRO).date()
        start = self.week_start(selected or today)
        key = start.isoformat()
        cached = self._cache.get(key)
        if cached and cached[0] > time.monotonic():
            return {**cached[1], "today": today.isoformat()}
        async with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                return {**cached[1], "today": today.isoformat()}
            beginning = datetime.combine(start, day_time.min, CAIRO)
            end = datetime.combine(start + timedelta(days=7), day_time.min, CAIRO)
            days = [{"date": (start + timedelta(days=i)).isoformat(),
                     "name": DAY_NAMES[(start + timedelta(days=i)).weekday()], "items": []}
                    for i in range(7)]
            by_date = {day["date"]: day for day in days}
            seen = set()
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    page = 1
                    while True:
                        response = await client.post("https://graphql.anilist.co", json={
                            "query": QUERY,
                            "variables": {"from": int(beginning.timestamp()) - 1,
                                          "to": int(end.timestamp()), "page": page},
                        })
                        response.raise_for_status()
                        payload = response.json()
                        if payload.get("errors"):
                            raise ValueError("Airing schedule provider returned an error")
                        result = payload["data"]["Page"]
                        for event in result.get("airingSchedules", []):
                            media = event.get("media") or {}
                            if media.get("isAdult") or not media.get("id") or event["id"] in seen:
                                continue
                            local = datetime.fromtimestamp(event["airingAt"], CAIRO)
                            day = by_date.get(local.date().isoformat())
                            if day is None:
                                continue
                            seen.add(event["id"])
                            titles = media.get("title") or {}
                            day["items"].append({
                                "id": event["id"], "anime_id": media["id"],
                                "title": titles.get("english") or titles.get("romaji") or titles.get("native"),
                                "episode": event["episode"], "airing_at": event["airingAt"],
                                "local_time": local.strftime("%H:%M"),
                                "poster_url": (media.get("coverImage") or {}).get("large"),
                                "banner_url": media.get("bannerImage"), "genres": media.get("genres", []),
                                "duration": media.get("duration"), "score": media.get("averageScore"),
                                "source_url": media.get("siteUrl"),
                            })
                        if not result["pageInfo"].get("hasNextPage"):
                            break
                        page += 1
                        if page > 30:
                            raise ValueError("Schedule pagination limit reached")
                result = {"week_start": key, "today": today.isoformat(), "timezone": "Africa/Cairo",
                          "source": "AniList", "updated_at": datetime.now(CAIRO).isoformat(),
                          "stale": False, "days": days}
                self._cache[key] = (time.monotonic() + 600, result)
                if len(self._cache) > 8:
                    self._cache.pop(next(iter(self._cache)))
                return result
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                if cached:
                    return {**cached[1], "today": today.isoformat(), "stale": True}
                raise


airing_schedule = AiringScheduleService()
