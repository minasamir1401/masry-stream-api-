import logging
from typing import Any, Dict, List, Optional, Set
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AnimeOverride, CustomEpisode, CustomServer
from app.services.db_cache import db_cache

logger = logging.getLogger(__name__)


class AnimeOverrideService:
    def __init__(self) -> None:
        self._hidden_ids: Optional[Set[int]] = None

    async def get_hidden_anime_ids(self, db: AsyncSession) -> Set[int]:
        if self._hidden_ids is not None:
            return self._hidden_ids
        stmt = select(AnimeOverride.anime_id).where(AnimeOverride.is_hidden.is_(True))
        result = await db.execute(stmt)
        self._hidden_ids = set(result.scalars().all())
        return self._hidden_ids

    def get_hidden_anime_ids_sync(self) -> Set[int]:
        return self._hidden_ids or set()

    async def is_anime_hidden(self, anime_id: int, db: AsyncSession) -> bool:
        hidden = await self.get_hidden_anime_ids(db)
        return anime_id in hidden

    async def hide_anime(self, anime_id: int, title: Optional[str], db: AsyncSession) -> Dict[str, Any]:
        stmt = select(AnimeOverride).where(AnimeOverride.anime_id == anime_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.is_hidden = True
            if title:
                record.title = title
        else:
            record = AnimeOverride(anime_id=anime_id, is_hidden=True, title=title)
            db.add(record)

        await db.commit()
        await db.refresh(record)

        if self._hidden_ids is not None:
            self._hidden_ids.add(anime_id)

        # Invalidate related caches
        await db_cache.delete(f"anime:{anime_id}")
        await db_cache.delete("homepage:v2")
        return {"anime_id": record.anime_id, "is_hidden": record.is_hidden, "title": record.title}

    async def unhide_anime(self, anime_id: int, db: AsyncSession) -> Dict[str, Any]:
        stmt = select(AnimeOverride).where(AnimeOverride.anime_id == anime_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            record.is_hidden = False
            await db.commit()
            await db.refresh(record)

        if self._hidden_ids is not None:
            self._hidden_ids.discard(anime_id)

        await db_cache.delete(f"anime:{anime_id}")
        await db_cache.delete("homepage:v2")
        return {"anime_id": anime_id, "is_hidden": False}

    async def list_hidden_anime(self, db: AsyncSession) -> List[Dict[str, Any]]:
        stmt = select(AnimeOverride).where(AnimeOverride.is_hidden.is_(True)).order_by(AnimeOverride.updated_at.desc())
        result = await db.execute(stmt)
        records = result.scalars().all()
        return [
            {
                "anime_id": r.anime_id,
                "title": r.title or f"Anime #{r.anime_id}",
                "is_hidden": r.is_hidden,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]

    async def get_custom_episodes(self, anime_id: int, db: AsyncSession) -> List[Dict[str, Any]]:
        stmt = select(CustomEpisode).where(CustomEpisode.anime_id == anime_id).order_by(CustomEpisode.episode_number.asc())
        result = await db.execute(stmt)
        records = result.scalars().all()
        return [
            {
                "id": r.id,
                "anime_id": r.anime_id,
                "episode_number": r.episode_number,
                "title": r.title or f"الحلقة {r.episode_number}",
                "duration": r.duration,
                "is_hidden": r.is_hidden,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]

    async def add_or_update_custom_episode(
        self,
        anime_id: int,
        episode_number: int,
        title: Optional[str],
        duration: Optional[int],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        stmt = select(CustomEpisode).where(
            CustomEpisode.anime_id == anime_id,
            CustomEpisode.episode_number == episode_number,
        )
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if record:
            if title is not None:
                record.title = title
            if duration is not None:
                record.duration = duration
        else:
            record = CustomEpisode(
                anime_id=anime_id,
                episode_number=episode_number,
                title=title or f"الحلقة {episode_number}",
                duration=duration,
            )
            db.add(record)

        await db.commit()
        await db.refresh(record)

        await db_cache.delete(f"anime:{anime_id}")
        return {
            "id": record.id,
            "anime_id": record.anime_id,
            "episode_number": record.episode_number,
            "title": record.title,
            "duration": record.duration,
        }

    async def delete_custom_episode(self, anime_id: int, episode_number: int, db: AsyncSession) -> bool:
        stmt = delete(CustomEpisode).where(
            CustomEpisode.anime_id == anime_id,
            CustomEpisode.episode_number == episode_number,
        )
        result = await db.execute(stmt)
        await db.commit()
        await db_cache.delete(f"anime:{anime_id}")
        return result.rowcount > 0

    async def get_custom_servers(
        self,
        anime_id: int,
        episode_number: int,
        db: AsyncSession,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        query = select(CustomServer).where(
            CustomServer.anime_id == anime_id,
            CustomServer.episode_number == episode_number,
        )
        if active_only:
            query = query.where(CustomServer.is_active.is_(True))
        query = query.order_by(CustomServer.order_index.asc(), CustomServer.id.asc())

        result = await db.execute(query)
        records = result.scalars().all()
        return [
            {
                "id": r.id,
                "anime_id": r.anime_id,
                "episode_number": r.episode_number,
                "name": r.name,
                "url": r.url,
                "server_type": r.server_type,
                "subtitle_url": r.subtitle_url,
                "order_index": r.order_index,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]

    async def add_custom_server(
        self,
        anime_id: int,
        episode_number: int,
        name: str,
        url: str,
        server_type: str,
        subtitle_url: Optional[str],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        server = CustomServer(
            anime_id=anime_id,
            episode_number=episode_number,
            name=name.strip(),
            url=url.strip(),
            server_type=server_type if server_type in ("direct", "hls", "embed") else "direct",
            subtitle_url=subtitle_url.strip() if subtitle_url else None,
        )
        db.add(server)
        await db.commit()
        await db.refresh(server)

        # Clear stream cache for this episode so the new server appears immediately
        await db_cache.delete(f"stream:v4:{anime_id}:{episode_number}")
        return {
            "id": server.id,
            "anime_id": server.anime_id,
            "episode_number": server.episode_number,
            "name": server.name,
            "url": server.url,
            "server_type": server.server_type,
            "subtitle_url": server.subtitle_url,
            "is_active": server.is_active,
        }

    async def update_custom_server(
        self,
        server_id: int,
        name: str,
        url: str,
        server_type: str,
        subtitle_url: Optional[str],
        is_active: bool,
        db: AsyncSession,
    ) -> Optional[Dict[str, Any]]:
        stmt = select(CustomServer).where(CustomServer.id == server_id)
        result = await db.execute(stmt)
        server = result.scalar_one_or_none()
        if not server:
            return None

        server.name = name.strip()
        server.url = url.strip()
        server.server_type = server_type if server_type in ("direct", "hls", "embed") else "direct"
        server.subtitle_url = subtitle_url.strip() if subtitle_url else None
        server.is_active = is_active

        await db.commit()
        await db.refresh(server)
        await db_cache.delete(f"stream:v4:{server.anime_id}:{server.episode_number}")
        return {
            "id": server.id,
            "anime_id": server.anime_id,
            "episode_number": server.episode_number,
            "name": server.name,
            "url": server.url,
            "server_type": server.server_type,
            "subtitle_url": server.subtitle_url,
            "is_active": server.is_active,
        }

    async def delete_custom_server(self, server_id: int, db: AsyncSession) -> bool:
        stmt = select(CustomServer).where(CustomServer.id == server_id)
        result = await db.execute(stmt)
        server = result.scalar_one_or_none()
        if not server:
            return False

        anime_id = server.anime_id
        episode_number = server.episode_number

        await db.delete(server)
        await db.commit()
        await db_cache.delete(f"stream:v4:{anime_id}:{episode_number}")
        return True


anime_override_service = AnimeOverrideService()

