import logging
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.models import Media
from app.services.streaming_service import streaming_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["Streaming & Assets"])


@router.get("/stream/{media_id}")
async def stream_media(
    media_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    query = select(Media).options(selectinload(Media.episodes)).where(Media.id == media_id)
    res = await db.execute(query)
    media = res.scalars().first()

    file_path = None
    mime_type = "video/mp4"

    if media:
        target_media = media
        if media.media_type == "SERIES" and media.episodes and len(media.episodes) > 0:
            target_media = sorted(media.episodes, key=lambda x: (x.season_number or 1, x.episode_number or 0))[0]

        if target_media.file_name:
            directory = Path(settings.DATA_DIR).resolve()
            candidate = (directory / target_media.file_name).resolve()
            if candidate.is_relative_to(directory) and candidate.is_file():
                file_path = str(candidate)
                mime_type = target_media.mime_type or "video/mp4"

    if file_path and os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
        status_code, start_byte, end_byte, content_length = streaming_service.parse_range_header(
            range_header=request.headers.get("range"),
            file_size=file_size,
        )
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": mime_type,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Range, Content-Length, Accept-Ranges",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"

        generator = streaming_service.create_file_stream_generator(
            file_path=file_path,
            start_byte=start_byte,
            content_length=content_length,
        )
        return StreamingResponse(
            generator,
            status_code=status_code,
            headers=headers,
            media_type=mime_type,
        )

    raise HTTPException(status_code=404, detail="لا يوجد فيديو متاح للبث لهذا العنوان")


@router.get("/poster/{media_id}")
async def get_poster(
    media_id: int,
    db: AsyncSession = Depends(get_db),
):
    query = select(Media).where(Media.id == media_id)
    res = await db.execute(query)
    media = res.scalars().first()

    if not media:
        raise HTTPException(status_code=404, detail="Poster not found")

    if media.poster_file:
        file_path = os.path.join(settings.POSTER_CACHE_DIR, media.poster_file)
        if os.path.exists(file_path):
            return FileResponse(file_path, media_type="image/jpeg")

    if media.poster_url:
        return RedirectResponse(url=media.poster_url)

    svg_placeholder = """<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900">
        <rect width="600" height="900" fill="#11141C"/>
        <circle cx="300" cy="400" r="100" fill="#7C3AED" opacity="0.2"/>
        <text x="300" y="420" font-family="Arial, sans-serif" font-size="28" fill="#FFFFFF" text-anchor="middle">عالم الأنمي</text>
        <text x="300" y="470" font-family="Arial, sans-serif" font-size="18" fill="#94A3B8" text-anchor="middle">AnimeStream</text>
    </svg>"""
    return Response(content=svg_placeholder, media_type="image/svg+xml")
