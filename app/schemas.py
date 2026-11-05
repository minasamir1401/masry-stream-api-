from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class GenreBase(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None


class GenreRead(GenreBase):
    id: int

    class Config:
        from_attributes = True


class MediaBase(BaseModel):
    telegram_channel: str
    telegram_message_id: int
    title: str
    original_title: Optional[str] = None
    description: Optional[str] = None
    release_year: Optional[int] = None
    duration: Optional[int] = None
    file_size: int = 0
    mime_type: str = "video/mp4"
    file_name: Optional[str] = None
    resolution: Optional[str] = None
    quality_label: Optional[str] = None
    poster_url: Optional[str] = None
    media_type: str = "MOVIE"
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    parent_id: Optional[int] = None
    is_available: bool = True
    direct_streamable: bool = True


class EpisodeRead(BaseModel):
    id: int
    telegram_message_id: int
    telegram_channel: str
    telegram_watch_url: Optional[str] = None
    direct_streamable: bool
    title: str
    episode_number: Optional[int] = None
    season_number: Optional[int] = None
    duration: Optional[int] = None
    file_size: int
    resolution: Optional[str] = None
    quality_label: Optional[str] = None
    stream_url: str

    class Config:
        from_attributes = True


class MediaRead(BaseModel):
    id: int
    telegram_channel: str
    telegram_message_id: int
    telegram_watch_url: Optional[str] = None
    title: str
    original_title: Optional[str] = None
    description: Optional[str] = None
    release_year: Optional[int] = None
    duration: Optional[int] = None
    file_size: int
    mime_type: str
    file_name: Optional[str] = None
    resolution: Optional[str] = None
    quality_label: Optional[str] = None
    poster_url: Optional[str] = None
    media_type: str
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    parent_id: Optional[int] = None
    is_available: bool
    direct_streamable: bool
    view_count: int
    created_at: Optional[datetime] = None
    genres: List[GenreRead] = []
    episodes: List[EpisodeRead] = []
    stream_url: Optional[str] = None
    poster_proxy_url: Optional[str] = None

    class Config:
        from_attributes = True


class MediaListResponse(BaseModel):
    items: List[MediaRead]
    total: int
    page: int
    page_size: int
    total_pages: int


class CategoryWithCount(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    count: int = 0


