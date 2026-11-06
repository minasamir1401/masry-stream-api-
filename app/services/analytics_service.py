import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnalyticsEvent, VisitorSession
from app.services.security_shield import security_shield


def clean_anime_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    cleaned = title.strip()
    cleaned = re.sub(r"^(?:أنمي|انمي|مسلسل|فيلم)\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+مترجم(?:\s+كامل)?(?:\s+اون\s+لاين)?(?:\s+HD)?$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+-\s+AnimeClover.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*\(.*?\)\s*", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or title.strip()


def format_user_agent(ua: Optional[str]) -> str:
    if not ua:
        return "متصفح غير معروف"
    ua_lower = ua.lower()
    device = "هاتف محمول" if any(w in ua_lower for w in ("mobile", "android", "iphone")) else "كمبيوتر"
    
    os_name = "Windows"
    if "android" in ua_lower:
        os_name = "Android"
    elif "iphone" in ua_lower or "ipad" in ua_lower or "ios" in ua_lower:
        os_name = "iOS"
    elif "mac" in ua_lower:
        os_name = "macOS"
    elif "linux" in ua_lower:
        os_name = "Linux"

    browser = "متصفح"
    if "chrome" in ua_lower and "edg" not in ua_lower and "opr" not in ua_lower:
        browser = "Chrome"
    elif "firefox" in ua_lower:
        browser = "Firefox"
    elif "safari" in ua_lower and "chrome" not in ua_lower:
        browser = "Safari"
    elif "edg" in ua_lower:
        browser = "Edge"

    return f"{device} ({browser} - {os_name})"


def build_action_description(event_type: str, anime_title: Optional[str], episode: Optional[int], duration_sec: int, path: str) -> str:
    cleaned = clean_anime_title(anime_title)
    duration_min = round(duration_sec / 60, 1) if duration_sec >= 60 else None

    if event_type == "play_anime":
        ep_text = f" الحلقة {episode}" if episode else ""
        dur_text = f" (شاهد {duration_min} دقيقة)" if duration_min else ""
        return f"مشاهدة{ep_text} من {cleaned or 'الأنمي'}{dur_text}"

    if event_type == "browse_anime":
        ep_text = f" - الحلقة {episode}" if episode else ""
        return f"فتح تفاصيل {cleaned or 'عمل أنمي'}{ep_text}"

    if event_type == "search":
        query = path.split("q=")[-1].split("&")[0] if "q=" in path else (cleaned or "")
        return f"بحث في الموقع عن '{query}'" if query else "استخدام محرك البحث"

    if event_type == "page_view":
        if path == "/":
            return "تصفح الصفحة الرئيسية"
        if path.startswith("/series"):
            return "تصفح قائمة مسلسلات الأنمي"
        if path.startswith("/movies"):
            return "تصفح قائمة أفلام الأنمي"
        if path.startswith("/schedule"):
            return "استعراض جدول مواعيد الحلقات الأسبوعي"
        if path.startswith("/favorites"):
            return "فتح قائمة المفضلة"
        return f"زيارة مسار: {path}"

    return f"نشاط: {event_type}"


async def cleanup_visitor_id(visitor_id: str, db: AsyncSession) -> None:
    if not visitor_id:
        return
    await db.execute(delete(AnalyticsEvent).where(AnalyticsEvent.visitor_id == visitor_id))
    await db.execute(delete(VisitorSession).where(VisitorSession.visitor_id == visitor_id))
    await db.commit()


async def cleanup_admin_sessions(db: AsyncSession, extra_visitor_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    admin_vids_res = await db.execute(
        select(AnalyticsEvent.visitor_id).where(AnalyticsEvent.path.like("/admin%")).distinct()
    )
    admin_vids = set(admin_vids_res.scalars().all())

    session_vids_res = await db.execute(
        select(VisitorSession.visitor_id).where(VisitorSession.last_path.like("/admin%")).distinct()
    )
    admin_vids.update(session_vids_res.scalars().all())

    if extra_visitor_ids:
        for vid in extra_visitor_ids:
            if vid:
                admin_vids.add(vid)

    deleted_count = len(admin_vids)
    if admin_vids:
        await db.execute(delete(AnalyticsEvent).where(AnalyticsEvent.visitor_id.in_(admin_vids)))
        await db.execute(delete(VisitorSession).where(VisitorSession.visitor_id.in_(admin_vids)))
        await db.commit()

    return {"status": "ok", "deleted_sessions": deleted_count}


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
    if not path or path.startswith("/admin"):
        return {"status": "ignored", "reason": "admin_path"}

    safe_duration = max(0, min(duration_seconds or 0, 300))
    cleaned_title = clean_anime_title(anime_title)

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
            last_path=path[:250] if path else "/",
        )
        db.add(visitor)
    else:
        visitor.last_path = path[:250] if path else visitor.last_path
        if user_agent and not visitor.user_agent:
            visitor.user_agent = user_agent[:250]
        if ip_hash and not visitor.ip_hash:
            visitor.ip_hash = ip_hash

        if event_type == "heartbeat":
            step = min(safe_duration, 60)
            visitor.duration_seconds += step
        elif event_type == "page_view":
            visitor.total_page_views += 1
        elif event_type == "browse_anime":
            visitor.total_anime_browsed += 1
        elif event_type == "play_anime":
            visitor.total_anime_played += 1
            visitor.total_watch_seconds += safe_duration

    # Heartbeats update keepalive only and do not flood the discrete events table
    if event_type != "heartbeat":
        event = AnalyticsEvent(
            visitor_id=visitor_id,
            event_type=event_type,
            path=path[:250] if path else "/",
            anime_id=anime_id,
            anime_title=cleaned_title[:250] if cleaned_title else None,
            episode_number=episode_number,
            duration_seconds=safe_duration,
            referrer=referrer[:250] if referrer else None,
        )
        db.add(event)

    if getattr(visitor, "is_banned", False):
        security_shield.set_visitor_ban(visitor_id, True)
    if getattr(visitor, "requires_challenge", False):
        security_shield.set_visitor_challenge(visitor_id, True)

    await db.commit()
    return {
        "status": "ok",
        "is_banned": bool(getattr(visitor, "is_banned", False)),
        "ban_reason": getattr(visitor, "ban_reason", None) or "تم حظر هذا الجهاز من قبل إدارة الموقع",
        "requires_challenge": bool(getattr(visitor, "requires_challenge", False)),
    }


async def get_analytics_summary(days: int, db: AsyncSession) -> Dict[str, Any]:
    # Purge any residual admin test sessions
    await cleanup_admin_sessions(db)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days if days > 0 else 3650)
    active_window = now - timedelta(minutes=5)

    base_filter = VisitorSession.last_path.not_like("/admin%")

    total_visitors_res = await db.execute(
        select(func.count(VisitorSession.id)).where(base_filter)
    )
    total_visitors = total_visitors_res.scalar() or 0

    active_now_res = await db.execute(
        select(func.count(VisitorSession.id)).where(
            base_filter,
            VisitorSession.last_seen >= active_window,
        )
    )
    active_now = active_now_res.scalar() or 0

    duration_res = await db.execute(
        select(
            func.coalesce(func.sum(VisitorSession.duration_seconds), 0),
            func.coalesce(func.sum(VisitorSession.total_watch_seconds), 0),
            func.coalesce(func.sum(VisitorSession.total_anime_browsed), 0),
            func.coalesce(func.sum(VisitorSession.total_anime_played), 0),
        ).where(base_filter)
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
            AnalyticsEvent.path.not_like("/admin%"),
        )
        .group_by(AnalyticsEvent.anime_id, AnalyticsEvent.anime_title)
        .order_by(desc("plays_count"))
        .limit(15)
    )
    watched_res = await db.execute(watched_query)
    top_watched = [
        {
            "anime_id": row.anime_id,
            "title": clean_anime_title(row.anime_title) or "أنمي غير معروف",
            "plays_count": row.plays_count,
            "watch_minutes": round((row.watch_seconds or 0) / 60, 1),
            "unique_viewers": row.unique_viewers,
        }
        for row in watched_res.all()
    ]

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
            AnalyticsEvent.path.not_like("/admin%"),
        )
        .group_by(AnalyticsEvent.anime_id, AnalyticsEvent.anime_title)
        .order_by(desc("browse_count"))
        .limit(15)
    )
    browsed_res = await db.execute(browsed_query)
    top_browsed = [
        {
            "anime_id": row.anime_id,
            "title": clean_anime_title(row.anime_title) or "أنمي غير معروف",
            "browse_count": row.browse_count,
            "unique_browsers": row.unique_browsers,
        }
        for row in browsed_res.all()
    ]

    recent_visitors_query = (
        select(VisitorSession)
        .where(base_filter)
        .order_by(desc(VisitorSession.last_seen))
        .limit(30)
    )
    recent_visitors_res = await db.execute(recent_visitors_query)
    session_models = recent_visitors_res.scalars().all()
    visitor_ids = [v.visitor_id for v in session_models]

    events_by_visitor: Dict[str, List[AnalyticsEvent]] = {vid: [] for vid in visitor_ids}
    if visitor_ids:
        events_batch_res = await db.execute(
            select(AnalyticsEvent)
            .where(
                AnalyticsEvent.visitor_id.in_(visitor_ids),
                AnalyticsEvent.event_type != "heartbeat",
                AnalyticsEvent.path.not_like("/admin%"),
            )
            .order_by(desc(AnalyticsEvent.created_at))
            .limit(500)
        )
        for ev in events_batch_res.scalars().all():
            events_by_visitor.setdefault(ev.visitor_id, []).append(ev)

    recent_sessions = []
    for v in session_models:
        v_events = events_by_visitor.get(v.visitor_id, [])
        animes_dict: Dict[str, Dict[str, Any]] = {}
        
        for ev in v_events:
            title = clean_anime_title(ev.anime_title)
            if not title:
                continue
            if title not in animes_dict:
                animes_dict[title] = {
                    "anime_id": ev.anime_id,
                    "title": title,
                    "episodes": set(),
                    "watch_seconds": 0,
                    "plays": 0,
                    "browses": 0,
                }
            if ev.event_type == "play_anime":
                animes_dict[title]["plays"] += 1
                animes_dict[title]["watch_seconds"] += ev.duration_seconds
                if ev.episode_number:
                    animes_dict[title]["episodes"].add(ev.episode_number)
            elif ev.event_type == "browse_anime":
                animes_dict[title]["browses"] += 1
                if ev.episode_number:
                    animes_dict[title]["episodes"].add(ev.episode_number)

        animes_list = [
            {
                "anime_id": data["anime_id"],
                "title": data["title"],
                "episodes": sorted(list(data["episodes"])),
                "watch_minutes": round(data["watch_seconds"] / 60, 1),
                "plays": data["plays"],
                "browses": data["browses"],
            }
            for data in sorted(animes_dict.values(), key=lambda x: (x["watch_seconds"], x["plays"], x["browses"]), reverse=True)
        ]

        primary_anime = animes_list[0]["title"] if animes_list else None

        # Build human-readable action summary
        duration_min = round(v.duration_seconds / 60, 1)
        watch_min = round(v.total_watch_seconds / 60, 1)
        if animes_list and watch_min > 0:
            top_a = animes_list[0]
            ep_str = f"الحلقة {', '.join(str(e) for e in top_a['episodes'][:3])}" if top_a['episodes'] else "حلقات"
            actions_summary = f"شاهد {ep_str} من {top_a['title']} ({top_a['watch_minutes']} دقيقة) وقضى {duration_min} دقيقة بالموقع"
        elif animes_list:
            actions_summary = f"تصفح عمل {animes_list[0]['title']} وقضى {duration_min} دقيقة بالموقع"
        elif v.total_page_views > 1:
            actions_summary = f"تصفح {v.total_page_views} صفحات في الموقع وقضى {duration_min} دقيقة"
        else:
            actions_summary = f"زيارة سريعة للموقع لمدة {duration_min} دقيقة"

        is_active = bool(v.last_seen and (now - v.last_seen.replace(tzinfo=timezone.utc if v.last_seen.tzinfo is None else v.last_seen.tzinfo)).total_seconds() <= 300)

        recent_sessions.append({
            "visitor_id": v.visitor_id,
            "display_id": f"زائر #{v.visitor_id[-4:].upper()}",
            "first_seen": v.first_seen.isoformat() if v.first_seen else "",
            "last_seen": v.last_seen.isoformat() if v.last_seen else "",
            "is_active": is_active,
            "duration_minutes": duration_min,
            "watch_minutes": watch_min,
            "browse_minutes": max(0.0, round(duration_min - watch_min, 1)),
            "page_views": v.total_page_views,
            "anime_browsed": v.total_anime_browsed,
            "anime_played": v.total_anime_played,
            "last_path": v.last_path or "/",
            "device": format_user_agent(v.user_agent),
            "primary_anime": primary_anime,
            "animes_list": animes_list,
            "actions_summary": actions_summary,
            "is_banned": bool(getattr(v, "is_banned", False)),
            "ban_reason": getattr(v, "ban_reason", None),
            "requires_challenge": bool(getattr(v, "requires_challenge", False)),
            "recent_actions": [
                {
                    "id": ev.id,
                    "event_type": ev.event_type,
                    "action_text": build_action_description(ev.event_type, ev.anime_title, ev.episode_number, ev.duration_seconds, ev.path),
                    "anime_title": clean_anime_title(ev.anime_title),
                    "episode_number": ev.episode_number,
                    "duration_seconds": ev.duration_seconds,
                    "created_at": ev.created_at.isoformat() if ev.created_at else "",
                }
                for ev in v_events[:6]
            ],
        })

    recent_events_query = (
        select(AnalyticsEvent)
        .where(
            AnalyticsEvent.event_type != "heartbeat",
            AnalyticsEvent.path.not_like("/admin%"),
        )
        .order_by(desc(AnalyticsEvent.created_at))
        .limit(50)
    )
    recent_events_res = await db.execute(recent_events_query)
    recent_events = [
        {
            "id": ev.id,
            "visitor_id": ev.visitor_id,
            "display_id": f"زائر #{ev.visitor_id[-4:].upper()}",
            "event_type": ev.event_type,
            "action_text": build_action_description(ev.event_type, ev.anime_title, ev.episode_number, ev.duration_seconds, ev.path),
            "path": ev.path,
            "anime_title": clean_anime_title(ev.anime_title),
            "episode_number": ev.episode_number,
            "duration_seconds": ev.duration_seconds,
            "duration_minutes": round(ev.duration_seconds / 60, 1) if ev.duration_seconds >= 60 else None,
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


async def get_visitor_detail(visitor_id: str, db: AsyncSession) -> Optional[Dict[str, Any]]:
    session_res = await db.execute(
        select(VisitorSession).where(VisitorSession.visitor_id == visitor_id)
    )
    visitor = session_res.scalar_one_or_none()
    if visitor is None:
        return None

    now = datetime.now(timezone.utc)
    is_active = bool(visitor.last_seen and (now - visitor.last_seen.replace(tzinfo=timezone.utc if visitor.last_seen.tzinfo is None else visitor.last_seen.tzinfo)).total_seconds() <= 300)

    events_res = await db.execute(
        select(AnalyticsEvent)
        .where(
            AnalyticsEvent.visitor_id == visitor_id,
            AnalyticsEvent.event_type != "heartbeat",
        )
        .order_by(desc(AnalyticsEvent.created_at))
        .limit(100)
    )
    events = events_res.scalars().all()

    animes_dict: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        title = clean_anime_title(ev.anime_title)
        if not title:
            continue
        if title not in animes_dict:
            animes_dict[title] = {
                "anime_id": ev.anime_id,
                "title": title,
                "episodes": set(),
                "watch_seconds": 0,
                "plays": 0,
                "browses": 0,
                "last_seen": ev.created_at.isoformat() if ev.created_at else "",
            }
        if ev.event_type == "play_anime":
            animes_dict[title]["plays"] += 1
            animes_dict[title]["watch_seconds"] += ev.duration_seconds
            if ev.episode_number:
                animes_dict[title]["episodes"].add(ev.episode_number)
        elif ev.event_type == "browse_anime":
            animes_dict[title]["browses"] += 1
            if ev.episode_number:
                animes_dict[title]["episodes"].add(ev.episode_number)

    animes_list = [
        {
            "anime_id": data["anime_id"],
            "title": data["title"],
            "episodes": sorted(list(data["episodes"])),
            "watch_minutes": round(data["watch_seconds"] / 60, 1),
            "plays": data["plays"],
            "browses": data["browses"],
            "last_seen": data["last_seen"],
        }
        for data in sorted(animes_dict.values(), key=lambda x: (x["watch_seconds"], x["plays"], x["browses"]), reverse=True)
    ]

    timeline = [
        {
            "id": ev.id,
            "event_type": ev.event_type,
            "action_text": build_action_description(ev.event_type, ev.anime_title, ev.episode_number, ev.duration_seconds, ev.path),
            "path": ev.path,
            "anime_title": clean_anime_title(ev.anime_title),
            "episode_number": ev.episode_number,
            "duration_seconds": ev.duration_seconds,
            "created_at": ev.created_at.isoformat() if ev.created_at else "",
        }
        for ev in events
    ]

    total_dur_min = round(visitor.duration_seconds / 60, 1)
    watch_min = round(visitor.total_watch_seconds / 60, 1)

    return {
        "visitor_id": visitor.visitor_id,
        "display_id": f"زائر #{visitor.visitor_id[-4:].upper()}",
        "first_seen": visitor.first_seen.isoformat() if visitor.first_seen else "",
        "last_seen": visitor.last_seen.isoformat() if visitor.last_seen else "",
        "is_active": is_active,
        "duration_minutes": total_dur_min,
        "watch_minutes": watch_min,
        "browse_minutes": max(0.0, round(total_dur_min - watch_min, 1)),
        "page_views": visitor.total_page_views,
        "anime_browsed": visitor.total_anime_browsed,
        "anime_played": visitor.total_anime_played,
        "last_path": visitor.last_path or "/",
        "device": format_user_agent(visitor.user_agent),
        "is_banned": bool(getattr(visitor, "is_banned", False)),
        "ban_reason": getattr(visitor, "ban_reason", None),
        "requires_challenge": bool(getattr(visitor, "requires_challenge", False)),
        "animes": animes_list,
        "timeline": timeline,
    }


async def ban_visitor(visitor_id: str, reason: Optional[str], db: AsyncSession) -> Dict[str, Any]:
    res = await db.execute(select(VisitorSession).where(VisitorSession.visitor_id == visitor_id))
    visitor = res.scalar_one_or_none()
    if visitor is None:
        visitor = VisitorSession(
            visitor_id=visitor_id,
            is_banned=True,
            ban_reason=reason or "تم الحظر بواسطة الإدارة",
        )
        db.add(visitor)
    else:
        visitor.is_banned = True
        visitor.ban_reason = reason or "تم الحظر بواسطة الإدارة"
    await db.commit()
    security_shield.set_visitor_ban(visitor_id, True)
    return {"status": "banned", "visitor_id": visitor_id}


async def unban_visitor(visitor_id: str, db: AsyncSession) -> Dict[str, Any]:
    res = await db.execute(select(VisitorSession).where(VisitorSession.visitor_id == visitor_id))
    visitor = res.scalar_one_or_none()
    if visitor:
        visitor.is_banned = False
        visitor.ban_reason = None
        await db.commit()
    security_shield.set_visitor_ban(visitor_id, False)
    return {"status": "unbanned", "visitor_id": visitor_id}


async def set_visitor_challenge(visitor_id: str, required: bool, db: AsyncSession) -> Dict[str, Any]:
    res = await db.execute(select(VisitorSession).where(VisitorSession.visitor_id == visitor_id))
    visitor = res.scalar_one_or_none()
    if visitor is None and required:
        visitor = VisitorSession(
            visitor_id=visitor_id,
            requires_challenge=True,
        )
        db.add(visitor)
    elif visitor:
        visitor.requires_challenge = required
        if not required:
            visitor.challenge_solved_at = datetime.now(timezone.utc)
    await db.commit()
    security_shield.set_visitor_challenge(visitor_id, required)
    return {"status": "ok", "requires_challenge": required, "visitor_id": visitor_id}


async def verify_visitor_challenge(visitor_id: str, answer: int, token: str, db: AsyncSession) -> Tuple[bool, str]:
    is_valid, error = security_shield.verify_math_challenge(visitor_id, answer, token)
    if not is_valid:
        return False, error

    res = await db.execute(select(VisitorSession).where(VisitorSession.visitor_id == visitor_id))
    visitor = res.scalar_one_or_none()
    if visitor:
        visitor.requires_challenge = False
        visitor.challenge_solved_at = datetime.now(timezone.utc)
        await db.commit()
    security_shield.set_visitor_challenge(visitor_id, False)
    return True, "تم التحقق بنجاح"
