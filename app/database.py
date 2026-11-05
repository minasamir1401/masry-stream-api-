import os
import logging
from typing import AsyncGenerator
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

logger = logging.getLogger(__name__)

os.makedirs(settings.DATA_DIR, exist_ok=True)
os.makedirs(settings.POSTER_CACHE_DIR, exist_ok=True)


class Base(DeclarativeBase):
    pass


def get_engine():
    db_url = settings.DATABASE_URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    is_sqlite = db_url.startswith("sqlite")

    engine_kwargs = {
        "echo": False,
        "future": True,
    }

    if not is_sqlite:
        engine_kwargs["pool_pre_ping"] = True
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 20

    try:
        engine = create_async_engine(db_url, **engine_kwargs)
        return engine
    except Exception as e:
        if settings.SQLITE_FALLBACK and not is_sqlite:
            logger.warning(
                f"PostgreSQL connection init failed: {e}. Falling back to SQLite: {settings.SQLITE_URL}"
            )
            return create_async_engine(settings.SQLITE_URL, echo=False, future=True)
        raise e


engine = get_engine()
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def _initialize_schema(conn):
    Base.metadata.create_all(conn)
    for table, column, definition in (
        ("media", "telegram_watch_url", "VARCHAR(512)"),
        ("scrape_logs", "source", "VARCHAR(16)"),
    ):
        if conn.dialect.name == "postgresql":
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}"))
        elif column not in {field["name"] for field in inspect(conn).get_columns(table)}:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


async def init_db():
    global engine, AsyncSessionLocal
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_initialize_schema)
        logger.info("Database schema initialized successfully")
        try:
            from app.services.anime_override_service import anime_override_service
            async with AsyncSessionLocal() as session:
                await anime_override_service.get_hidden_anime_ids(session)
        except Exception as err:
            logger.warning("Could not preload hidden anime ids: %s", err)
    except Exception as e:
        if settings.SQLITE_FALLBACK and not settings.DATABASE_URL.startswith("sqlite"):
            logger.warning(
                f"Failed to connect to PostgreSQL ({e}). Initializing SQLite fallback engine."
            )
            engine = create_async_engine(settings.SQLITE_URL, echo=False, future=True)
            AsyncSessionLocal.configure(bind=engine)
            async with engine.begin() as conn:
                await conn.run_sync(_initialize_schema)
            logger.info("SQLite fallback database initialized successfully")
            try:
                from app.services.anime_override_service import anime_override_service
                async with AsyncSessionLocal() as session:
                    await anime_override_service.get_hidden_anime_ids(session)
            except Exception as err:
                logger.warning("Could not preload hidden anime ids: %s", err)
        else:
            logger.error(
                "Database initialization failed: %s. Continuing startup in degraded mode.", e
            )

