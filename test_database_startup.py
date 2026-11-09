import asyncio
import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app import database
from app.config import settings
import app.models


class StartupFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_failure_stops_startup_without_switching_databases(self):
        connection = Mock(run_sync=AsyncMock(side_effect=RuntimeError("schema failure")))

        @asynccontextmanager
        async def begin():
            yield connection

        with patch.object(database, "engine", Mock(begin=begin)), \
             patch.object(settings, "DATABASE_URL", "postgresql+asyncpg://localhost/example"), \
             patch.object(settings, "SQLITE_FALLBACK", True), \
             patch.object(database, "create_async_engine") as fallback, \
             self.assertLogs("app.database", level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "schema failure"):
                await database.init_db()
        fallback.assert_not_called()


@unittest.skipUnless(os.getenv("POSTGRES_STARTUP_TEST_URL"), "Requires an isolated PostgreSQL test database")
class PostgreSQLStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_workers_can_upgrade_same_legacy_schema(self):
        url = make_url(os.environ["POSTGRES_STARTUP_TEST_URL"])
        self.assertIn("test", url.database.lower())
        engine = create_async_engine(url)
        schema = f"clover_startup_test_{uuid4().hex}"
        barrier = asyncio.Barrier(2)

        async def start_worker():
            async with engine.begin() as conn:
                await conn.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                await barrier.wait()
                await conn.run_sync(database._initialize_schema)

        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                await conn.exec_driver_sql(f'CREATE TABLE "{schema}".catalog_entries (anime_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
                await conn.execute(text(f'INSERT INTO "{schema}".catalog_entries VALUES (97940, :payload)'), {"payload": "{}"})
            results = await asyncio.wait_for(
                asyncio.gather(start_worker(), start_worker(), return_exceptions=True), timeout=30,
            )
            self.assertEqual(results, [None, None])
            async with engine.begin() as conn:
                session_table = await conn.scalar(text("SELECT to_regclass(:table)"), {"table": f"{schema}.admin_sessions"})
                self.assertIsNotNone(session_table)
                rows = (await conn.exec_driver_sql(f'SELECT anime_id, canonical_path FROM "{schema}".catalog_entries')).all()
                self.assertEqual(rows, [(97940, None)])
        finally:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await engine.dispose()
