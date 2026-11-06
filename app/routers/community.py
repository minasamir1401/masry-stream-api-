import hashlib
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import EpisodeComment, EpisodeRating, Media, Report

router = APIRouter(prefix="/api", tags=["Community & Feedback"])


class CreateReportRequest(BaseModel):
    category: str = Field(..., pattern="^(site_issue|anime_request|broken_episode)$")
    title: str = Field(..., min_length=2, max_length=255)
    details: str = Field(..., min_length=5, max_length=5000)
    anime_id: Optional[int] = None
    episode_number: Optional[int] = None
    contact_email: Optional[str] = Field(default=None, max_length=255)
    visitor_id: Optional[str] = Field(default=None, max_length=64)


class CreateCommentRequest(BaseModel):
    author_name: str = Field(default="متابع", min_length=1, max_length=64)
    content: str = Field(..., min_length=1, max_length=2000)
    parent_id: Optional[int] = None
    visitor_id: Optional[str] = Field(default=None, max_length=64)


class SubmitRatingRequest(BaseModel):
    score: int = Field(..., ge=1, le=5)
    visitor_id: str = Field(..., min_length=4, max_length=64)


@router.post("/reports")
async def submit_report(
    body: CreateReportRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16] if ip else None

    report = Report(
        category=body.category,
        title=body.title.strip(),
        details=body.details.strip(),
        anime_id=body.anime_id,
        episode_number=body.episode_number,
        contact_email=body.contact_email.strip() if body.contact_email else None,
        visitor_id=body.visitor_id,
        ip_hash=ip_hash,
        status="pending",
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)

    return {
        "status": "success",
        "report_id": report.id,
        "message": "تم استلام طلبك أو بلاغك بنجاح وسيقوم فريق الإدارة بمراجعته فوراً",
    }


@router.get("/comments/{media_id}/{episode}")
async def get_episode_comments(
    media_id: int,
    episode: int,
    visitor_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(EpisodeComment)
        .where(
            EpisodeComment.media_id == media_id,
            EpisodeComment.episode_number == episode,
            EpisodeComment.is_approved.is_(True),
        )
        .order_by(desc(EpisodeComment.created_at))
        .limit(100)
    )
    res = await db.execute(query)
    comments = res.scalars().all()

    return {
        "media_id": media_id,
        "episode_number": episode,
        "total": len(comments),
        "items": [
            {
                "id": c.id,
                "author_name": c.author_name,
                "content": c.content,
                "parent_id": c.parent_id,
                "likes_count": c.likes_count,
                "is_current_user": bool(visitor_id and c.visitor_id == visitor_id),
                "created_at": c.created_at.isoformat() if c.created_at else "",
            }
            for c in comments
        ],
    }


@router.post("/comments/{comment_id}/like")
async def like_episode_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(EpisodeComment).where(EpisodeComment.id == comment_id)
    )
    comment = res.scalar_one_or_none()
    if comment is None:
        raise HTTPException(404, "التعليق غير موجود")

    comment.likes_count += 1
    await db.commit()
    return {"status": "ok", "likes_count": comment.likes_count}


@router.post("/comments/{media_id}/{episode}")
async def post_episode_comment(
    media_id: int,
    episode: int,
    body: CreateCommentRequest,
    db: AsyncSession = Depends(get_db),
):
    clean_name = body.author_name.strip() or "متابع"
    clean_text = body.content.strip()
    if not clean_text:
        raise HTTPException(400, "لا يمكن ترك نص التعليق فارغاً")

    comment = EpisodeComment(
        media_id=media_id,
        episode_number=episode,
        author_name=clean_name[:64],
        content=clean_text[:2000],
        parent_id=body.parent_id,
        visitor_id=body.visitor_id,
        is_approved=True,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return {
        "status": "success",
        "comment": {
            "id": comment.id,
            "author_name": comment.author_name,
            "content": comment.content,
            "parent_id": comment.parent_id,
            "likes_count": comment.likes_count,
            "is_current_user": True,
            "created_at": comment.created_at.isoformat() if comment.created_at else "",
        },
    }


@router.get("/ratings/{media_id}/{episode}")
async def get_episode_rating(
    media_id: int,
    episode: int,
    visitor_id: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stats_query = select(
        func.count(EpisodeRating.id),
        func.coalesce(func.avg(EpisodeRating.score), 0.0),
    ).where(
        EpisodeRating.media_id == media_id,
        EpisodeRating.episode_number == episode,
    )
    stats_res = await db.execute(stats_query)
    row = stats_res.first()
    total_votes = row[0] if row else 0
    avg_score = round(float(row[1]), 1) if row else 0.0

    user_score = None
    if visitor_id:
        user_query = select(EpisodeRating.score).where(
            EpisodeRating.media_id == media_id,
            EpisodeRating.episode_number == episode,
            EpisodeRating.visitor_id == visitor_id,
        )
        user_res = await db.execute(user_query)
        user_score = user_res.scalar_one_or_none()

    return {
        "media_id": media_id,
        "episode_number": episode,
        "average_score": avg_score,
        "total_votes": total_votes,
        "user_score": user_score,
    }


@router.post("/ratings/{media_id}/{episode}")
async def submit_episode_rating(
    media_id: int,
    episode: int,
    body: SubmitRatingRequest,
    db: AsyncSession = Depends(get_db),
):
    existing_query = select(EpisodeRating).where(
        EpisodeRating.media_id == media_id,
        EpisodeRating.episode_number == episode,
        EpisodeRating.visitor_id == body.visitor_id,
    )
    existing_res = await db.execute(existing_query)
    existing_rating = existing_res.scalar_one_or_none()

    if existing_rating:
        existing_rating.score = body.score
    else:
        new_rating = EpisodeRating(
            media_id=media_id,
            episode_number=episode,
            visitor_id=body.visitor_id,
            score=body.score,
        )
        db.add(new_rating)

    await db.commit()

    stats_query = select(
        func.count(EpisodeRating.id),
        func.coalesce(func.avg(EpisodeRating.score), 0.0),
    ).where(
        EpisodeRating.media_id == media_id,
        EpisodeRating.episode_number == episode,
    )
    stats_res = await db.execute(stats_query)
    row = stats_res.first()
    total_votes = row[0] if row else 0
    avg_score = round(float(row[1]), 1) if row else 0.0

    return {
        "status": "success",
        "average_score": avg_score,
        "total_votes": total_votes,
        "user_score": body.score,
    }
