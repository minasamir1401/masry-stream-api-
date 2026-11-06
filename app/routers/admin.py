from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Report, EpisodeComment
from app.services import admin_auth, analytics_service
from app.services.db_cache import db_cache
from app.services.anime_override_service import anime_override_service

router = APIRouter(prefix="/api/admin", tags=["Administration"], include_in_schema=False)


class AdminLoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: SecretStr
    visitor_id: Optional[str] = None


class ChangeCredentialsRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_username: Optional[str] = Field(default=None, max_length=64)
    new_password: Optional[str] = Field(default=None, max_length=1024)


class HideAnimeRequest(BaseModel):
    title: Optional[str] = None


class CustomEpisodeRequest(BaseModel):
    episode_number: int = Field(..., ge=1)
    title: Optional[str] = None
    duration: Optional[int] = None


class CustomServerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=1)
    server_type: str = Field(default="direct", pattern="^(direct|hls|embed)$")
    subtitle_url: Optional[str] = None


class CustomServerUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=1)
    server_type: str = Field(default="direct", pattern="^(direct|hls|embed)$")
    subtitle_url: Optional[str] = None
    is_active: bool = True


class UpdateReportRequest(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(pending|in_progress|resolved|dismissed)$")
    admin_notes: Optional[str] = None


class VisitorBanRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=255)



@router.post("/login")
async def login(
    body: AdminLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    password = body.password.get_secret_value()
    if len(password) > 1024:
        raise HTTPException(400, "كلمة المرور طويلة جدًا")
    username, token = await admin_auth.login(
        username=body.username,
        password=password,
        request=request,
        response=response,
        db=db,
    )
    if body.visitor_id:
        await analytics_service.cleanup_visitor_id(body.visitor_id, db)
        await analytics_service.cleanup_admin_sessions(db, [body.visitor_id])
    return {"status": "authenticated", "username": username, "token": token}


@router.post("/logout")
async def logout(request: Request, response: Response):
    admin_auth.logout(request, response)
    return {"status": "signed_out"}


@router.get("/status")
async def admin_status(current_user: str = Depends(admin_auth.require_admin)):
    return {
        "status": "authenticated",
        "username": current_user,
        "service": "operational",
    }


@router.post("/change-credentials")
async def change_credentials(
    body: ChangeCredentialsRequest,
    current_user: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    if not body.new_username and not body.new_password:
        raise HTTPException(400, "يرجى تحديد اسم مستخدم جديد أو كلمة مرور جديدة")

    updated_username, message = await admin_auth.update_admin_credentials(
        current_username=current_user,
        current_password=body.current_password,
        new_username=body.new_username,
        new_password=body.new_password,
        db=db,
    )
    return {
        "status": "success",
        "username": updated_username,
        "message": message,
    }


@router.get("/analytics/overview")
async def analytics_overview(
    days: int = Query(default=7, ge=0, le=365),
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await analytics_service.get_analytics_summary(days=days, db=db)


@router.get("/analytics/visitor/{visitor_id}")
async def visitor_detail(
    visitor_id: str,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    detail = await analytics_service.get_visitor_detail(visitor_id=visitor_id, db=db)
    if detail is None:
        raise HTTPException(404, "لم يتم العثور على سجل لهذا الزائر")
    return detail


@router.post("/analytics/cleanup-admin")
async def cleanup_admin(
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await analytics_service.cleanup_admin_sessions(db)


@router.post("/visitors/{visitor_id}/ban")
async def admin_ban_visitor(
    visitor_id: str,
    body: Optional[VisitorBanRequest] = None,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    reason = body.reason if body else None
    return await analytics_service.ban_visitor(visitor_id=visitor_id, reason=reason, db=db)


@router.post("/visitors/{visitor_id}/unban")
async def admin_unban_visitor(
    visitor_id: str,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await analytics_service.unban_visitor(visitor_id=visitor_id, db=db)


@router.post("/visitors/{visitor_id}/challenge")
async def admin_challenge_visitor(
    visitor_id: str,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await analytics_service.set_visitor_challenge(visitor_id=visitor_id, required=True, db=db)


@router.post("/visitors/{visitor_id}/unchallenge")
async def admin_unchallenge_visitor(
    visitor_id: str,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await analytics_service.set_visitor_challenge(visitor_id=visitor_id, required=False, db=db)


@router.get("/reports")
async def list_reports(
    category: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(Report).order_by(desc(Report.created_at)).limit(limit)
    if category:
        query = query.where(Report.category == category)
    if status:
        query = query.where(Report.status == status)

    res = await db.execute(query)
    reports = res.scalars().all()
    return {
        "total": len(reports),
        "items": [
            {
                "id": r.id,
                "category": r.category,
                "title": r.title,
                "details": r.details,
                "anime_id": r.anime_id,
                "episode_number": r.episode_number,
                "contact_email": r.contact_email,
                "status": r.status,
                "admin_notes": r.admin_notes,
                "visitor_id": r.visitor_id,
                "created_at": r.created_at.isoformat() if r.created_at else "",
                "updated_at": r.updated_at.isoformat() if r.updated_at else "",
            }
            for r in reports
        ],
    }


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: int,
    body: UpdateReportRequest,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Report).where(Report.id == report_id))
    report = res.scalar_one_or_none()
    if report is None:
        raise HTTPException(404, "البلاغ غير موجود")

    if body.status:
        report.status = body.status
    if body.admin_notes is not None:
        report.admin_notes = body.admin_notes

    await db.commit()
    await db.refresh(report)
    return {"status": "success", "report_id": report.id, "new_status": report.status}


@router.delete("/reports/{report_id}")
async def delete_report(
    report_id: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Report).where(Report.id == report_id))
    report = res.scalar_one_or_none()
    if report is None:
        raise HTTPException(404, "البلاغ غير موجود")

    await db.delete(report)
    await db.commit()
    return {"status": "deleted", "report_id": report_id}


@router.get("/comments")
async def list_admin_comments(
    limit: int = Query(default=50, ge=1, le=200),
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    query = select(EpisodeComment).order_by(desc(EpisodeComment.created_at)).limit(limit)
    res = await db.execute(query)
    comments = res.scalars().all()
    return {
        "total": len(comments),
        "items": [
            {
                "id": c.id,
                "media_id": c.media_id,
                "episode_number": c.episode_number,
                "author_name": c.author_name,
                "content": c.content,
                "visitor_id": c.visitor_id,
                "likes_count": c.likes_count,
                "is_approved": c.is_approved,
                "created_at": c.created_at.isoformat() if c.created_at else "",
            }
            for c in comments
        ],
    }


@router.delete("/comments/{comment_id}")
async def delete_admin_comment(
    comment_id: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(EpisodeComment).where(EpisodeComment.id == comment_id))
    comment = res.scalar_one_or_none()
    if comment is None:
        raise HTTPException(404, "التعليق غير موجود")

    await db.delete(comment)
    await db.commit()
    return {"status": "deleted", "comment_id": comment_id}


@router.patch("/comments/{comment_id}/toggle-approval")
async def toggle_admin_comment_approval(
    comment_id: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(EpisodeComment).where(EpisodeComment.id == comment_id))
    comment = res.scalar_one_or_none()
    if comment is None:
        raise HTTPException(404, "التعليق غير موجود")

    comment.is_approved = not comment.is_approved
    await db.commit()
    return {"status": "success", "comment_id": comment_id, "is_approved": comment.is_approved}


@router.post("/cache/purge")
async def purge_cache(_: str = Depends(admin_auth.require_admin)):
    """Remove all expired cache rows from the database."""
    removed = await db_cache.purge_expired()
    return {"status": "ok", "rows_removed": removed}


@router.delete("/cache/{key:path}")
async def invalidate_cache_key(key: str, _: str = Depends(admin_auth.require_admin)):
    """Manually invalidate a specific cache key (e.g. 'anime:12345', 'homepage:v1')."""
    await db_cache.delete(key)
    return {"status": "deleted", "key": key}


@router.get("/anime/search")
async def admin_search_anime(
    q: str = Query(..., min_length=1),
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.anime_service import anime_service
    results = await anime_service.search(query=q, page=1, per_page=20, allow_hidden=True)
    hidden_ids = await anime_override_service.get_hidden_anime_ids(db)
    items = results.get("items", [])
    for item in items:
        item["is_hidden"] = item["id"] in hidden_ids
    return {"items": items, "total": len(items)}


@router.post("/anime/{anime_id}/hide")
async def admin_hide_anime(
    anime_id: int,
    body: Optional[HideAnimeRequest] = None,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    title = body.title if body else None
    result = await anime_override_service.hide_anime(anime_id=anime_id, title=title, db=db)
    return {"status": "success", "data": result}


@router.post("/anime/{anime_id}/unhide")
async def admin_unhide_anime(
    anime_id: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await anime_override_service.unhide_anime(anime_id=anime_id, db=db)
    return {"status": "success", "data": result}


@router.get("/anime/hidden")
async def admin_list_hidden(
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    records = await anime_override_service.list_hidden_anime(db)
    return {"items": records, "total": len(records)}


@router.get("/anime/{anime_id}/episodes")
async def admin_get_anime_episodes(
    anime_id: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.anime_service import anime_service
    info = await anime_service.episodes_for_anime(anime_id=anime_id, allow_hidden=True)
    if not info:
        raise HTTPException(404, "الأنمي غير موجود")
    custom_eps = await anime_override_service.get_custom_episodes(anime_id, db)
    custom_map = {e["episode_number"]: e for e in custom_eps}
    
    # Also get counts of custom servers for each episode
    from app.models import CustomServer
    from sqlalchemy import select, func
    stmt = (
        select(CustomServer.episode_number, func.count(CustomServer.id))
        .where(CustomServer.anime_id == anime_id)
        .group_by(CustomServer.episode_number)
    )
    res = await db.execute(stmt)
    server_counts = dict(res.all())

    episodes = info.get("episodes", [])
    merged_episodes = []
    seen_numbers = set()

    for ep in episodes:
        num = ep["number"]
        seen_numbers.add(num)
        custom_data = custom_map.get(num)
        merged_episodes.append({
            "number": num,
            "title": custom_data["title"] if custom_data else f"الحلقة {num}",
            "is_custom": bool(custom_data),
            "custom_servers_count": server_counts.get(num, 0),
            "state": ep.get("state", "listed"),
        })

    # Add custom episodes that were not in the upstream feed
    for num, custom_data in custom_map.items():
        if num not in seen_numbers:
            merged_episodes.append({
                "number": num,
                "title": custom_data["title"],
                "is_custom": True,
                "custom_servers_count": server_counts.get(num, 0),
                "state": "custom",
            })

    merged_episodes.sort(key=lambda x: x["number"])
    return {
        "anime_id": anime_id,
        "total": len(merged_episodes),
        "episodes": merged_episodes,
    }


@router.post("/anime/{anime_id}/episodes")
async def admin_add_or_update_episode(
    anime_id: int,
    body: CustomEpisodeRequest,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await anime_override_service.add_or_update_custom_episode(
        anime_id=anime_id,
        episode_number=body.episode_number,
        title=body.title,
        duration=body.duration,
        db=db,
    )
    return {"status": "success", "data": result}


@router.delete("/anime/{anime_id}/episodes/{episode_number}")
async def admin_delete_episode(
    anime_id: int,
    episode_number: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    success = await anime_override_service.delete_custom_episode(
        anime_id=anime_id,
        episode_number=episode_number,
        db=db,
    )
    return {"status": "success" if success else "not_found"}


@router.get("/anime/{anime_id}/episodes/{episode_number}/servers")
async def admin_get_episode_servers(
    anime_id: int,
    episode_number: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.anime_service import anime_service
    custom_servers = await anime_override_service.get_custom_servers(
        anime_id=anime_id,
        episode_number=episode_number,
        db=db,
        active_only=False,
    )
    # Fetch resolved servers without failing if scraper is empty
    live_sources = await anime_service.get_stream_sources(
        anime_id=anime_id,
        episode=episode_number,
        allow_hidden=True,
    )
    scraped_servers = [
        s for s in live_sources.get("servers", [])
        if not s.get("is_custom")
    ]
    return {
        "anime_id": anime_id,
        "episode": episode_number,
        "custom_servers": custom_servers,
        "live_servers": scraped_servers,
    }


@router.post("/anime/{anime_id}/episodes/{episode_number}/servers")
async def admin_add_custom_server(
    anime_id: int,
    episode_number: int,
    body: CustomServerCreateRequest,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await anime_override_service.add_custom_server(
        anime_id=anime_id,
        episode_number=episode_number,
        name=body.name,
        url=body.url,
        server_type=body.server_type,
        subtitle_url=body.subtitle_url,
        db=db,
    )
    return {"status": "success", "data": result}


@router.put("/servers/{server_id}")
async def admin_update_custom_server(
    server_id: int,
    body: CustomServerUpdateRequest,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await anime_override_service.update_custom_server(
        server_id=server_id,
        name=body.name,
        url=body.url,
        server_type=body.server_type,
        subtitle_url=body.subtitle_url,
        is_active=body.is_active,
        db=db,
    )
    if not result:
        raise HTTPException(404, "السيرفر غير موجود")
    return {"status": "success", "data": result}


@router.delete("/servers/{server_id}")
async def admin_delete_custom_server(
    server_id: int,
    _: str = Depends(admin_auth.require_admin),
    db: AsyncSession = Depends(get_db),
):
    success = await anime_override_service.delete_custom_server(server_id=server_id, db=db)
    if not success:
        raise HTTPException(404, "السيرفر غير موجود")
    return {"status": "success"}

