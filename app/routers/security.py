from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import VisitorSession
from app.services.security_shield import security_shield
from app.services import analytics_service

router = APIRouter(prefix="/api/security", tags=["Security"])


class VerifyChallengeRequest(BaseModel):
    visitor_id: str = Field(..., min_length=4, max_length=64)
    answer: int
    token: str = Field(..., min_length=10, max_length=256)


@router.get("/challenge")
async def get_challenge(
    vid: str = Query(..., min_length=4, max_length=64),
    request: Request = None,
):
    return security_shield.generate_math_challenge(visitor_id=vid)


@router.post("/verify-challenge")
async def verify_challenge(
    body: VerifyChallengeRequest,
    db: AsyncSession = Depends(get_db),
):
    success, message = await analytics_service.verify_visitor_challenge(
        visitor_id=body.visitor_id,
        answer=body.answer,
        token=body.token,
        db=db,
    )
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"status": "ok", "message": message}


@router.get("/status")
async def get_security_status(
    vid: str = Query(..., min_length=4, max_length=64),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(VisitorSession).where(VisitorSession.visitor_id == vid))
    visitor = res.scalar_one_or_none()
    if not visitor:
        return {
            "is_banned": False,
            "ban_reason": None,
            "requires_challenge": False,
        }
    return {
        "is_banned": bool(visitor.is_banned),
        "ban_reason": visitor.ban_reason,
        "requires_challenge": bool(visitor.requires_challenge),
    }
