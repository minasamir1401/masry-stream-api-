import os
from pathlib import Path
from typing import Any, List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    PROJECT_NAME: str = "Anime Clover - عالم الأنمي المترجم"
    VERSION: str = "1.0.0"
    DEBUG: bool = True

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on", "dev", "debug")
        return bool(v)

    @field_validator("ADMIN_ORIGINS", "CORS_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: Any) -> List[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str):
            clean = v.strip()
            if clean.startswith("[") and clean.endswith("]"):
                try:
                    import json
                    parsed = json.loads(clean)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            return [x.strip() for x in clean.split(",") if x.strip()]
        return []

    API_PREFIX: str = "/api"

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/masry_stream"
    SQLITE_FALLBACK: bool = True
    SQLITE_URL: str = f"sqlite+aiosqlite:///{str(BACKEND_DIR / 'data' / 'masry_stream.db').replace(os.sep, '/')}"

    ADMIN_PASSWORD: Optional[str] = None
    ADMIN_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    ADMIN_SECURE_COOKIE: bool = False

    CACHE_DIR: str = str(BACKEND_DIR / "cache")
    POSTER_CACHE_DIR: str = str(BACKEND_DIR / "cache" / "posters")
    DATA_DIR: str = str(BACKEND_DIR / "data")
    STREAM_CHUNK_SIZE: int = 1024 * 1024
    STREAM_PREBUFFER_BYTES: int = 2 * 1024 * 1024

    CORS_ORIGINS: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow",
    )


settings = Settings()
