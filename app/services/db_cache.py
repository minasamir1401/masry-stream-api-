"""
Database-backed cache for AniList metadata and stream sources.

Stores fetched data persistently so restarts and multiple workers
share the same warm cache without hammering external APIs.

TTLs (seconds):
  - Anime detail       : 24 hours  — titles / descriptions rarely change
  - List results       : 30 minutes — trending / popular shift slowly
  - Stream sources     : 10 minutes — embed URLs expire, so keep short
  - Homepage payload   : 60 minutes — curated set, refreshed on-demand
"""

import json
import logging
import time
from typing import Any, Dict, Optional

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import CacheEntry

logger = logging.getLogger(__name__)

TTL_ANIME_DETAIL = 86_400       # 24 h
TTL_LIST         = 1_800        # 30 min
TTL_STREAM       = 1_800        # 30 min
TTL_HOMEPAGE     = 3_600        # 1 h


class DBCache:
    """Thin async wrapper around the cache_entries table."""

    # In-memory L1 layer avoids a DB round-trip for hot keys.
    # Sized to hold several 300-segment HLS playlists plus catalog metadata.
    _L1_MAX = 20_000
    _l1: Dict[str, tuple] = {}   # key -> (expires_at, value)

    # ------------------------------------------------------------------ #
    # Public interface                                                     #
    # ------------------------------------------------------------------ #

    async def get(self, key: str) -> Optional[Any]:
        """Return cached value or None if missing / expired."""
        # L1 hit
        entry = self._l1.get(key)
        if entry:
            expires_at, value = entry
            if expires_at > time.monotonic():
                return value
            del self._l1[key]

        # L2 — database
        try:
            async with AsyncSessionLocal() as session:
                row = await self._db_get(session, key)
                if row is not None:
                    remaining = max(0, row["expires_at"] - time.time())
                    self._l1_set(key, time.monotonic() + remaining, row["value"])
                    return row["value"]
        except Exception as exc:
            logger.debug("db_cache.get failed for %s: %s", key, exc)

        return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Persist value with the given TTL (seconds)."""
        expires_at = int(time.time()) + ttl
        self._l1_set(key, time.monotonic() + ttl, value)

        try:
            async with AsyncSessionLocal() as session:
                await self._db_set(session, key, value, expires_at)
                await session.commit()
        except Exception as exc:
            logger.debug("db_cache.set failed for %s: %s", key, exc)

    async def delete(self, key: str) -> None:
        self._l1.pop(key, None)
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    delete(CacheEntry).where(CacheEntry.key == key)
                )
                await session.commit()
        except Exception as exc:
            logger.debug("db_cache.delete failed for %s: %s", key, exc)

    async def set_many(self, values: Dict[str, Any], ttl: int) -> None:
        """Persist a catalog page or HLS playlist map in one bulk transaction."""
        if not values:
            return
        expires_at = int(time.time()) + ttl
        for key, value in values.items():
            self._l1_set(key, time.monotonic() + ttl, value)
        try:
            async with AsyncSessionLocal() as session:
                await self._db_set_many(session, values, expires_at)
                await session.commit()
        except Exception as exc:
            logger.debug("db_cache.set_many failed: %s", exc)

    async def purge_expired(self) -> int:
        """Remove stale rows; called periodically by a background task."""
        now = int(time.time())
        # Clear L1 expired entries
        self._l1 = {k: v for k, v in self._l1.items() if v[0] > time.monotonic()}
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    delete(CacheEntry).where(CacheEntry.expires_at < now)
                )
                await session.commit()
                return result.rowcount  # type: ignore[return-value]
        except Exception as exc:
            logger.debug("db_cache.purge_expired failed: %s", exc)
            return 0

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    def _l1_set(self, key: str, expires_mono: float, value: Any) -> None:
        if len(self._l1) >= self._L1_MAX:
            # Evict the first (oldest inserted) entry
            oldest = next(iter(self._l1))
            del self._l1[oldest]
        self._l1[key] = (expires_mono, value)

    @staticmethod
    async def _db_get(session: AsyncSession, key: str) -> Optional[Dict[str, Any]]:
        now = int(time.time())
        result = await session.execute(
            select(CacheEntry).where(
                CacheEntry.key == key,
                CacheEntry.expires_at > now,
            )
        )
        row: Optional[CacheEntry] = result.scalars().first()
        if row is None:
            return None
        return {"value": json.loads(row.value), "expires_at": row.expires_at}

    @staticmethod
    async def _db_set(
        session: AsyncSession, key: str, value: Any, expires_at: int
    ) -> None:
        payload = json.dumps(value, default=str)

        # Try a dialect-agnostic upsert via raw SQL so it works for both
        # PostgreSQL and SQLite without conditional branching.
        dialect = session.bind.dialect.name if session.bind else "sqlite"  # type: ignore[union-attr]

        if dialect == "postgresql":
            stmt = (
                pg_insert(CacheEntry)
                .values(key=key, value=payload, expires_at=expires_at)
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"value": payload, "expires_at": expires_at},
                )
            )
            await session.execute(stmt)
        else:
            # SQLite — INSERT OR REPLACE
            await session.execute(
                text(
                    "INSERT OR REPLACE INTO cache_entries (key, value, expires_at) "
                    "VALUES (:key, :value, :expires_at)"
                ),
                {"key": key, "value": payload, "expires_at": expires_at},
            )

    @staticmethod
    async def _db_set_many(
        session: AsyncSession, values: Dict[str, Any], expires_at: int
    ) -> None:
        rows = [
            {"key": key, "value": json.dumps(value, default=str), "expires_at": expires_at}
            for key, value in values.items()
        ]
        dialect = session.bind.dialect.name if session.bind else "sqlite"  # type: ignore[union-attr]
        if dialect == "postgresql":
            stmt = pg_insert(CacheEntry).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={"value": stmt.excluded.value, "expires_at": stmt.excluded.expires_at},
            )
            await session.execute(stmt)
        else:
            await session.execute(
                text(
                    "INSERT OR REPLACE INTO cache_entries (key, value, expires_at) "
                    "VALUES (:key, :value, :expires_at)"
                ),
                rows,
            )


db_cache = DBCache()

