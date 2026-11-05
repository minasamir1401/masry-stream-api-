from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnalyticsEvent, VisitorSession


async def record_tracking_event(
    visitor_id: str,
    event_type: str,
    path: str,
    anime_id: Optional[int],
    anime_title: Optional[str],
    episode_number: Optional[int],
    duration_seconds: int,
    ip_hash: Optional[str],
    user_agent: Optional[str],
    referrer: Optional[str],
    db: AsyncSession,
) -> Dict[str, Any]:
    safe_duration = max(0, min(duration_seconds or 0, 300))

    session_query = await db.execute(
        select(VisitorSession).where(VisitorSession.visitor_id == visitor_id)
    )
    visitor = session_query.scalar_one_or_none()

    if visitor is None:
        visitor = VisitorSession(
            visitor_id=visitor_id,
            ip_hash=ip_hash,
            user_agent=user_agent[:250] if user_agent else None,
            duration_seconds=safe_duration if event_type == "heartbeat" else 0,
            total_page_views=1 if event_type == "page_view" else 0,
            total_anime_browsed=1 if event_type == "browse_anime" else 0,
            total_anime_played=1 if event_type == "play_anime" else 0,
            total_watch_seconds=safe_duration if event_type == "play_anime" else 0,
            last_path=path[:250] if path else None,
        )
        db.add(visitor)
    else:
        visitor.last_path = path[:250] if path else visitor.last_path
        if user_agent and not visitor.user_agent:
            visitor.user_agent = user_agent[:250]
        if ip_hash and not visitor.ip_hash:
            visitor.ip_hash = ip_hash

        if event_type == "heartbeat":
            # Cap each heartbeat to 60s to prevent arbitrary time inflating
            step = min(safe_duration, 60)
            visitor.duration_seconds += step
        elif event_type == "page_view":
            visitor.total_page_views += 1
        elif event_type == "browse_anime":
            visitor.total_anime_browsed += 1
        elif event_type == "play_anime":
            visitor.total_anime_played += 1
            visitor.total_watch_seconds += safe_duration

    event = AnalyticsEvent(
        visitor_id=visitor_id,
        event_type=event_type,
        path=path[:250] if path else "/",
        anime_id=anime_id,
        anime_title=anime_title[:250] if anime_title else None,
        episode_number=episode_number,
        duration_seconds=safe_duration,
        referrer=referrer[:250] if referrer else None,
    )
    db.add(event)

    await db.commit()
    return {"status": "ok"}


async def get_analytics_summary(days: int, db: AsyncSession) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days if days > 0 else 3650)
    active_window = now - timedelta(minutes=5)

    # 1. Total visitors and active now
    total_visitors_res = await db.execute(
        select(func.count(VisitorSession.id))
    )
    total_visitors = total_visitors_res.scalar() or 0

    active_now_res = await db.execute(
        select(func.count(VisitorSession.id)).where(VisitorSession.last_seen >= active_window)
    )
    active_now = active_now_res.scalar() or 0

    # 2. Time spent open on site
    duration_res = await db.execute(
        select(
            func.coalesce(func.sum(VisitorSession.duration_seconds), 0),
            func.coalesce(func.sum(VisitorSession.total_watch_seconds), 0),
            func.coalesce(func.sum(VisitorSession.total_anime_browsed), 0),
            func.coalesce(func.sum(VisitorSession.total_anime_played), 0),
        )
    )
    duration_row = duration_res.first()
    total_duration_sec = duration_row[0] if duration_row else 0
    total_watch_sec = duration_row[1] if duration_row else 0
    total_browsed = duration_row[2] if duration_row else 0
    total_played = duration_row[3] if duration_row else 0

    total_duration_minutes = round(total_duration_sec / 60, 1)
    total_watch_minutes = round(total_watch_sec / 60, 1)
    avg_duration_minutes = (
        round((total_duration_sec / 60) / total_visitors, 1) if total_visitors > 0 else 0
    )

    # 3. Top watched / listened anime
    watched_query = (
        select(
            AnalyticsEvent.anime_id,
            AnalyticsEvent.anime_title,
            func.count(AnalyticsEvent.id).label("plays_count"),
            func.coalesce(func.sum(AnalyticsEvent.duration_seconds), 0).label("watch_seconds"),
            func.count(func.distinct(AnalyticsEvent.visitor_id)).label("unique_viewers"),
        )
        .where(
            AnalyticsEvent.event_type == "play_anime",
            AnalyticsEvent.created_at >= cutoff,
            AnalyticsEvent.anime_title.isnot(None),
        )
        .group_by(AnalyticsEvent.anime_id, AnalyticsEvent.anime_title)
        .order_by(desc("plays_count"))
        .limit(15)
    )
    watched_res = await db.execute(watched_query)
    top_watched = [
        {
            "anime_id": row.anime_id,
            "title": row.anime_title or "أنمي غير معروف",
            "plays_count": row.plays_count,
            "watch_minutes": round((row.watch_seconds or 0) / 60, 1),
            "unique_viewers": row.unique_viewers,
        }
        for row in watched_res.all()
    ]

    # 4. Top browsed anime
    browsed_query = (
        select(
            AnalyticsEvent.anime_id,
            AnalyticsEvent.anime_title,
            func.count(AnalyticsEvent.id).label("browse_count"),
            func.count(func.distinct(AnalyticsEvent.visitor_id)).label("unique_browsers"),
        )
        .where(
            AnalyticsEvent.event_type == "browse_anime",
            AnalyticsEvent.created_at >= cutoff,
            AnalyticsEvent.anime_title.isnot(None),
        )
        .group_by(AnalyticsEvent.anime_id, AnalyticsEvent.anime_title)
        .order_by(desc("browse_count"))
        .limit(15)
    )
    browsed_res = await db.execute(browsed_query)
    top_browsed = [
        {
            "anime_id": row.anime_id,
            "title": row.anime_title or "أنمي غير معروف",
            "browse_count": row.browse_count,
            "unique_browsers": row.unique_browsers,
        }
        for row in browsed_res.all()
    ]

    # 5. Recent visitor sessions (who opened the site, for how many minutes, pages, anime played)
    recent_visitors_query = (
        select(VisitorSession)
        .order_by(desc(VisitorSession.last_seen))
        .limit(20)
    )
    recent_visitors_res = await db.execute(recent_visitors_query)
    recent_sessions = [
        {
            "visitor_id": v.visitor_id[:16],
            "first_seen": v.first_seen.isoformat() if v.first_seen else "",
            "last_seen": v.last_seen.isoformat() if v.last_seen else "",
            "duration_minutes": round(v.duration_seconds / 60, 1),
            "page_views": v.total_page_views,
            "anime_browsed": v.total_anime_browsed,
            "anime_played": v.total_anime_played,
            "watch_minutes": round(v.total_watch_seconds / 60, 1),
            "last_path": v.last_path or "/",
        }
        for v in recent_visitors_res.scalars().all()
    ]

    # 6. Recent live event stream
    recent_events_query = (
        select(AnalyticsEvent)
        .order_by(desc(AnalyticsEvent.created_at))
        .limit(30)
    )
    recent_events_res = await db.execute(recent_events_query)
    recent_events = [
        {
            "id": ev.id,
            "visitor_id": ev.visitor_id[:12],
            "event_type": ev.event_type,
            "path": ev.path,
            "anime_title": ev.anime_title,
            "episode_number": ev.episode_number,
            "duration_seconds": ev.duration_seconds,
            "created_at": ev.created_at.isoformat() if ev.created_at else "",
        }
        for ev in recent_events_res.scalars().all()
    ]

    return {
        "period_days": days,
        "overview": {
            "total_visitors": total_visitors,
            "active_now": active_now,
            "total_duration_minutes": total_duration_minutes,
            "avg_duration_minutes": avg_duration_minutes,
            "total_anime_played": total_played,
            "total_watch_minutes": total_watch_minutes,
            "total_anime_browsed": total_browsed,
        },
        "top_watched_anime": top_watched,
        "top_browsed_anime": top_browsed,
        "recent_sessions": recent_sessions,
        "recent_events": recent_events,
    }

