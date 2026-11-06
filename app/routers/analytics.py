import hashlib
from typing import Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import admin_auth, analytics_service

router = APIRouter(prefix="/api/analytics", tags=["Analytics"])


class TrackEventRequest(BaseModel):
    visitor_id: str = Field(..., min_length=4, max_length=64)
    event_type: str = Field(..., max_length=32)
    path: str = Field(default="/", max_length=255)
    anime_id: Optional[int] = None
    anime_title: Optional[str] = Field(default=None, max_length=255)
    episode_number: Optional[int] = None
    duration_seconds: int = Field(default=0, ge=0, le=3600)


@router.post("/track")
async def track_event(
    body: TrackEventRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if body.path.startswith("/admin") or body.path == "/admin":
        return {"status": "ignored", "reason": "admin_path"}

    admin_token = request.cookies.get(admin_auth.COOKIE_NAME)
    if admin_token and admin_auth.is_valid_admin_token(admin_token):
        return {"status": "ignored", "reason": "admin_session"}
    ip = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16] if ip else None
    user_agent = request.headers.get("user-agent", "")
    referrer = request.headers.get("referer", "")

    return await analytics_service.record_tracking_event(
        visitor_id=body.visitor_id,
        event_type=body.event_type,
        path=body.path,
        anime_id=body.anime_id,
        anime_title=body.anime_title,
        episode_number=body.episode_number,
        duration_seconds=body.duration_seconds,
        ip_hash=ip_hash,
        user_agent=user_agent,
        referrer=referrer,
        db=db,
    )

