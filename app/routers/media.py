import logging
import math
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Genre, Media, MediaType, media_genres
from app.schemas import EpisodeRead, MediaListResponse, MediaRead
from app.services.anime_service import anime_service

router = APIRouter(prefix="/api", tags=["Media"])
logger = logging.getLogger(__name__)


def build_media_urls(media: Media, request: Request) -> dict:
    base_url = str(request.base_url).rstrip("/")
    stream_url = f"{base_url}/api/stream/{media.id}"
    poster_proxy_url = f"{base_url}/api/poster/{media.id}" if media.poster_file else media.poster_url
    return {
        "stream_url": stream_url,
        "poster_proxy_url": poster_proxy_url,
    }


def to_media_read(media: Media, request: Request, episodes: Optional[List[Media]] = None) -> MediaRead:
    urls = build_media_urls(media, request)
    episodes_read = []
    related_episodes = media.episodes if episodes is None else episodes
    if related_episodes:
        for ep in sorted(related_episodes, key=lambda x: (x.season_number or 1, x.episode_number or 0)):
            ep_urls = build_media_urls(ep, request)
            episodes_read.append(
                EpisodeRead(
                    id=ep.id,
                    telegram_message_id=ep.telegram_message_id,
                    telegram_channel=ep.telegram_channel,
                    telegram_watch_url=ep.telegram_watch_url,
                    direct_streamable=ep.direct_streamable,
                    title=ep.title,
                    episode_number=ep.episode_number,
                    season_number=ep.season_number,
                    duration=ep.duration,
                    file_size=ep.file_size,
                    resolution=ep.resolution,
                    quality_label=ep.quality_label,
                    stream_url=ep_urls["stream_url"],
                )
            )

    return MediaRead(
        id=media.id,
        telegram_channel=media.telegram_channel,
        telegram_message_id=media.telegram_message_id,
        telegram_watch_url=media.telegram_watch_url,
        title=media.title,
        original_title=media.original_title,
        description=media.description,
        release_year=media.release_year,
        duration=media.duration,
        file_size=media.file_size,
        mime_type=media.mime_type,
        file_name=media.file_name,
        resolution=media.resolution,
        quality_label=media.quality_label,
        poster_url=media.poster_url,
        media_type=media.media_type,
        season_number=media.season_number,
        episode_number=media.episode_number,
        parent_id=media.parent_id,
        is_available=media.is_available,
        direct_streamable=media.direct_streamable,
        view_count=media.view_count,
        created_at=media.created_at,
        genres=media.genres,
        episodes=episodes_read,
        stream_url=urls["stream_url"],
        poster_proxy_url=urls["poster_proxy_url"],
    )


@router.get("/movies", response_model=MediaListResponse)
async def list_movies(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    genre: Optional[str] = Query(None, description="Genre slug"),
    media_type: Optional[str] = Query("MOVIE", description="MOVIE, SERIES, or ALL"),
    sort_by: str = Query("latest", pattern="^(latest|size|duration|views|title)$"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Media).options(selectinload(Media.genres), selectinload(Media.episodes))
    count_query = select(func.count(func.distinct(Media.id)))

    # Filter media_type
    if media_type and media_type != "ALL":
        query = query.where(Media.media_type == media_type)
        count_query = count_query.where(Media.media_type == media_type)
    elif not media_type or media_type == "ALL":
        # Don't show raw episodes in top-level catalog list
        query = query.where(Media.media_type != MediaType.EPISODE.value)
        count_query = count_query.where(Media.media_type != MediaType.EPISODE.value)

    # Filter by genre slug
    if genre:
        query = query.join(Media.genres).where(Genre.slug == genre)
        count_query = count_query.join(Media.genres).where(Genre.slug == genre)

    # Sorting
    if sort_by == "size":
        query = query.order_by(desc(Media.file_size))
    elif sort_by == "duration":
        query = query.order_by(desc(Media.duration))
    elif sort_by == "views":
        query = query.order_by(desc(Media.view_count))
    elif sort_by == "title":
        query = query.order_by(Media.title.asc())
    else:
        query = query.order_by(desc(Media.created_at))

    # Total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    if total == 0:
        try:
            if media_type == "SERIES":
                anime_data = await anime_service.get_popular_series(page=page, per_page=page_size)
            elif media_type == "MOVIE":
                anime_data = await anime_service.get_anime_movies(page=page, per_page=page_size)
            else:
                anime_data = await anime_service.get_trending(page=page, per_page=page_size)
            if anime_data.get("items"):
                return MediaListResponse(**anime_data)
        except Exception as e:
            logger.warning("Anime fallback in list_movies failed: %s", e)

    # Pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    media_items = result.scalars().unique().all()

    items = [to_media_read(m, request) for m in media_items]
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    return MediaListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/movies/{media_id}", response_model=MediaRead)
async def get_movie(
    media_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(Media)
        .options(selectinload(Media.genres), selectinload(Media.episodes))
        .where(Media.id == media_id)
    )
    result = await db.execute(query)
    media = result.scalars().first()

    if not media:
        anime = await anime_service.get_by_id(media_id)
        if anime:
            return anime
        raise HTTPException(status_code=404, detail="المحتوى المطلوب غير موجود")

    episodes = None
    # Include sibling episodes in the response without changing their stored parent.
    if media.parent_id and not media.episodes:
        parent_query = (
            select(Media)
            .options(selectinload(Media.episodes))
            .where(Media.id == media.parent_id)
        )
        parent_res = await db.execute(parent_query)
        parent = parent_res.scalars().first()
        if parent and parent.episodes:
            episodes = parent.episodes

    # Increment view count
    media.view_count += 1
    await db.commit()

    return to_media_read(media, request, episodes=episodes)


@router.get("/series", response_model=MediaListResponse)
async def list_series(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    genre: Optional[str] = Query(None),
    sort_by: str = Query("latest"),
    db: AsyncSession = Depends(get_db),
):
    return await list_movies(
        request=request,
        page=page,
        page_size=page_size,
        genre=genre,
        media_type="SERIES",
        sort_by=sort_by,
        db=db,
    )


@router.get("/search", response_model=MediaListResponse)
async def search_media(
    request: Request,
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    search_term = f"%{q.strip()}%"
    filter_condition = or_(
        Media.title.ilike(search_term),
        Media.original_title.ilike(search_term),
        Media.description.ilike(search_term),
    )

    count_query = (
        select(func.count(Media.id))
        .where(filter_condition)
        .where(Media.media_type != MediaType.EPISODE.value)
    )
    total_res = await db.execute(count_query)
    total = total_res.scalar() or 0

    if total == 0:
        try:
            anime_res = await anime_service.search(query=q.strip(), page=page, per_page=page_size)
            if anime_res.get("items"):
                return MediaListResponse(**anime_res)
        except Exception as e:
            logger.warning("Anime search fallback failed: %s", e)

    query = (
        select(Media)
        .options(selectinload(Media.genres), selectinload(Media.episodes))
        .where(filter_condition)
        .where(Media.media_type != MediaType.EPISODE.value)
        .order_by(desc(Media.created_at))
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    result = await db.execute(query)
    media_items = result.scalars().unique().all()
    items = [to_media_read(m, request) for m in media_items]
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    return MediaListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
