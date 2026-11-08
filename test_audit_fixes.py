"""Regression coverage for authentication, SEO inventory and analytics correctness.

Run with an isolated DATABASE_URL / DATA_DIR; all test data uses an in-memory DB.
"""
import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException, Response
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from starlette.requests import Request

from app.database import Base
from app.models import AdminUser, AdminSession, AnalyticsEvent, CatalogEntry, AnimeOverride, VisitorSession
from app.services import admin_auth, analytics_service, seo_catalog
from app.services.anime_service import AnimeService


class AuditFixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.db = self.sessions()
        admin_auth.login_failures.clear()

    async def asyncTearDown(self):
        await self.db.close()
        await self.engine.dispose()

    def request(self, token=None):
        headers = [(b"origin", b"http://127.0.0.1:3000")]
        if token:
            headers.append((b"cookie", f"masry_admin={token}".encode()))
        return Request({"type": "http", "scheme": "http", "server": ("127.0.0.1", 3000), "path": "/", "headers": headers})

    async def admin(self):
        salt = "test-salt"
        user = AdminUser(username="owner", salt=salt, password_hash=admin_auth.hash_password("test-password", salt))
        self.db.add(user)
        await self.db.commit()
        return user

    async def test_session_is_stored_hashed_and_logout_revokes_across_connections(self):
        await self.admin()
        _, token = await admin_auth.login("owner", "test-password", self.request(), Response(), self.db)
        session = (await self.db.execute(select(AdminSession))).scalar_one()
        self.assertNotEqual(token, session.token_hash)
        async with self.sessions() as other:
            self.assertEqual(await admin_auth.session_username(token, other), "owner")
        await admin_auth.logout(self.request(token), Response(), self.db)
        async with self.sessions() as other:
            self.assertIsNone(await admin_auth.session_username(token, other))
        self.assertIsNone(await admin_auth.session_username("admin.1.9999999999.forged", self.db))

    async def test_credential_change_revokes_existing_sessions_and_does_not_recreate_admin(self):
        user = await self.admin()
        _, token = await admin_auth.login("owner", "test-password", self.request(), Response(), self.db)
        await admin_auth.update_admin_credentials("owner", "test-password", "new-owner", None, self.db)
        self.assertIsNone(await admin_auth.session_username(token, self.db))
        self.assertEqual((await admin_auth.ensure_default_admin(self.db)).id, user.id)
        self.assertEqual(len((await self.db.execute(select(AdminUser))).scalars().all()), 1)

    async def test_expired_sessions_are_rejected(self):
        user = await self.admin()
        self.db.add(AdminSession(token_hash=hashlib.sha256(b"expired-token").hexdigest(), user_id=user.id, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await self.db.commit()
        self.assertIsNone(await admin_auth.session_username("expired-token", self.db))

    async def track(self, kind, duration=0, event_id=None):
        return await analytics_service.record_tracking_event("visitor-test", kind, "/watch/1?ep=1", 1, "Example", 1, duration, None, "test", "https://google.com/", self.db, event_id, 2)

    async def test_progress_does_not_increase_play_count_and_event_retry_is_idempotent(self):
        await self.track("play_anime", event_id="unique-start")
        await self.track("play_anime", event_id="unique-start")
        await self.track("watch_progress", 15, "unique-progress")
        await self.track("heartbeat", 20, "unique-heartbeat")
        visitor = (await self.db.execute(select(VisitorSession))).scalar_one()
        self.assertEqual((visitor.total_anime_played, visitor.total_watch_seconds), (1, 15))
        report = await analytics_service.get_analytics_summary(7, self.db)
        self.assertEqual(report["overview"]["total_anime_played"], 1)
        self.assertEqual(report["top_watched_anime"][0]["plays_count"], 1)

    async def test_period_filter_excludes_older_activity_from_every_overview_total(self):
        await self.track("page_view", event_id="current-page")
        self.db.add(AnalyticsEvent(visitor_id="old-browser", event_type="play_anime", path="/watch/1", anime_id=1, anime_title="Example", duration_seconds=0, created_at=datetime.now(timezone.utc) - timedelta(days=40)))
        await self.db.commit()
        week = await analytics_service.get_analytics_summary(7, self.db)
        lifetime = await analytics_service.get_analytics_summary(0, self.db)
        self.assertEqual(week["overview"]["total_visitors"], 1)
        self.assertEqual(week["overview"]["total_anime_played"], 0)
        self.assertEqual(lifetime["overview"]["total_visitors"], 2)
        self.assertEqual(lifetime["overview"]["total_anime_played"], 1)

    async def test_inventory_preserves_long_series_and_excludes_hidden_and_unaired(self):
        await seo_catalog.remember_catalog(self.db, {
            "anime:1": {"id": 1, "title": "Long", "media_type": "SERIES", "status": "FINISHED", "total_episodes": 170},
            "anime:2": {"id": 2, "title": "Future", "media_type": "SERIES", "status": "NOT_YET_RELEASED", "total_episodes": 12},
            "anime:3": {"id": 3, "title": "Hidden", "media_type": "SERIES", "status": "FINISHED", "total_episodes": 10},
        })
        self.db.add(AnimeOverride(anime_id=3, is_hidden=True))
        await self.db.commit()
        with patch.object(seo_catalog, "AsyncSessionLocal", self.sessions):
            paths = await seo_catalog.sitemap_paths("episodes")
        self.assertEqual(len(paths), 170)
        self.assertIn("/watch/1?ep=170", paths)
        self.assertFalse(any(path.startswith("/watch/2") or path.startswith("/watch/3") for path in paths))

    async def test_provider_429_is_retryable_not_missing(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429)))
        self.addAsyncCleanup(client.aclose)
        with patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=None)), patch("app.services.anime_service._get_client", AsyncMock(return_value=client)):
            with self.assertRaises(HTTPException) as error:
                await AnimeService().get_by_id(100)
        self.assertEqual(error.exception.status_code, 503)

    async def test_expired_memory_metadata_does_not_hide_new_episode(self):
        service = AnimeService()
        service._anime_cache[1] = {"id": 1, "title": "old"}
        service._anime_cache_expires[1] = 0
        fresh = {"id": 1, "title": "fresh"}
        with patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=fresh)):
            self.assertEqual(await service.get_by_id(1), fresh)

    async def test_unknown_airing_index_is_retryable_instead_of_creating_false_404s(self):
        service = AnimeService()
        service._anime_cache[1] = {"id": 1, "title": "Ongoing", "status": "RELEASING", "total_episodes": 12}
        with patch("app.services.anime_service.db_cache.get", AsyncMock(return_value=None)), \
             patch("app.services.anime_service.get_episode_index", AsyncMock(return_value={})), \
             patch("app.services.anime_service.anime3rb_episode_index", AsyncMock(return_value={})), \
             patch("app.services.anime_service.get_anime4up_episodes", AsyncMock(return_value={})), \
             patch("app.services.anime_service.live_episode_index", AsyncMock(return_value={})), \
             patch("app.services.anime_override_service.anime_override_service.get_custom_episodes", AsyncMock(return_value=[])):
            with self.assertRaises(HTTPException) as error:
                await service.episodes_for_anime(1, verify_sources=False)
        self.assertEqual(error.exception.status_code, 503)

    async def test_video_relay_does_not_accept_an_unsigned_source(self):
        from app.routers.anime import get_anime_video
        request = Request({"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [(b"user-agent", b"test-browser")]})
        with self.assertRaises(HTTPException) as error:
            await get_anime_video(1, 1, request, server=0, source=None, st=None)
        self.assertEqual(error.exception.status_code, 403)

    async def test_existing_analytics_table_is_extended_without_deleting_rows(self):
        from app.database import _initialize_schema
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE TABLE analytics_events (id INTEGER PRIMARY KEY)"))
                await connection.execute(text("INSERT INTO analytics_events (id) VALUES (42)"))
                await connection.run_sync(_initialize_schema)
                columns = await connection.run_sync(lambda sync: {column["name"] for column in inspect(sync).get_columns("analytics_events")})
                self.assertTrue({"event_id", "schema_version"}.issubset(columns))
                self.assertEqual((await connection.execute(text("SELECT id FROM analytics_events"))).scalar(), 42)
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
