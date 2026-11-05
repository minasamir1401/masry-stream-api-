import enum
from datetime import datetime
from typing import List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class CacheEntry(Base):
    __tablename__ = "cache_entries"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, index=True)


class MediaType(str, enum.Enum):
    MOVIE = "MOVIE"
    SERIES = "SERIES"
    EPISODE = "EPISODE"


# Many-to-Many association table between Media and Genres
media_genres = Table(
    "media_genres",
    Base.metadata,
    Column("media_id", Integer, ForeignKey("media.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    media_items: Mapped[List["Media"]] = relationship(
        "Media",
        secondary=media_genres,
        back_populates="genres",
    )


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_channel: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    telegram_message_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    telegram_watch_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    original_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    release_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # In seconds
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)  # In bytes
    mime_type: Mapped[str] = mapped_column(String(64), default="video/mp4")
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    quality_label: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    poster_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    poster_file: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    media_type: Mapped[str] = mapped_column(
        String(16), default=MediaType.MOVIE.value, nullable=False, index=True
    )
    season_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("media.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    direct_streamable: Mapped[bool] = mapped_column(Boolean, default=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("telegram_channel", "telegram_message_id", name="uq_channel_message"),
        Index("ix_media_title_type", "title", "media_type"),
    )

    genres: Mapped[List[Genre]] = relationship(
        "Genre",
        secondary=media_genres,
        back_populates="media_items",
        lazy="selectin",
    )

    episodes: Mapped[List["Media"]] = relationship(
        "Media",
        back_populates="parent",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    parent: Mapped[Optional["Media"]] = relationship(
        "Media",
        back_populates="episodes",
        remote_side=[id],
    )


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    last_scraped_message_id: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS")
    items_found: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    salt: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )


class VisitorSession(Base):
    __tablename__ = "visitor_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visitor_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_page_views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_anime_browsed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_anime_played: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_watch_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_path: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )  # 'page_view', 'heartbeat', 'browse_anime', 'play_anime'
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    anime_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    anime_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    episode_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    referrer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class AnimeOverride(Base):
    __tablename__ = "anime_overrides"

    anime_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_episodes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CustomEpisode(Base):
    __tablename__ = "custom_episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anime_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("anime_id", "episode_number", name="uq_custom_anime_ep"),
    )


class CustomServer(Base):
    __tablename__ = "custom_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anime_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    episode_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    server_type: Mapped[str] = mapped_column(String(16), default="direct", nullable=False)
    subtitle_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


