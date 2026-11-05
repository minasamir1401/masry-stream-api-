import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.config import settings
from app.database import AsyncSessionLocal, init_db
from app.routers import admin, analytics, anime, genres, media, stream
from app.services.admin_auth import ensure_default_admin
from app.services.db_cache import db_cache
from app.services.video_http import close_video_client
from app.services.source_http import close_source_client

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("MasryStream")
for library in ("httpx", "httpcore"):
    logging.getLogger(library).setLevel(logging.WARNING)


async def _cache_purge_loop() -> None:
    """Purge expired DB cache rows every 10 minutes."""
    while True:
        await asyncio.sleep(600)
        try:
            removed = await db_cache.purge_expired()
            if removed:
                logger.info("DB cache: purged %d expired rows", removed)
        except Exception as exc:
            logger.debug("Cache purge error: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing backend services...")
    await init_db()
    async with AsyncSessionLocal() as session:
        try:
            admin_user = await ensure_default_admin(session)
            logger.info(f"Admin system verified. Active admin user: {admin_user.username}")
        except Exception as e:
            logger.warning(f"Could not verify default admin user during startup: {e}")
    purge_task = asyncio.create_task(_cache_purge_loop())
    yield
    purge_task.cancel()
    await close_video_client()
    await close_source_client()
    logger.info("Shutting down backend services...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Media and streaming platform backend.",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(media.router)
app.include_router(anime.router)
app.include_router(stream.router)
app.include_router(genres.router)
app.include_router(analytics.router)
app.include_router(admin.router)


@app.middleware("http")
async def prevent_admin_caching(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    if (
        request.method == "GET"
        and request.url.path.startswith("/api/anime/")
        and not request.url.path.startswith("/api/admin/")
    ):
        if response.status_code >= 400 or any(part in request.url.path for part in ("/stream", "/subtitles/", "/video/", "/hls/")):
            response.headers["Cache-Control"] = "no-store"
        elif "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"
    return response


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "docs": "/docs",
    }


@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "database": "connected",
    }
