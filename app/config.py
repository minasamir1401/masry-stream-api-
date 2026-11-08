import os
import secrets
import time
from pathlib import Path
from typing import Any, List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    PROJECT_NAME: str = "Anime Clover - عالم الأنمي المترجم"
    VERSION: str = "1.0.0"
    DEBUG: bool = True

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_db_url(cls, v: Any) -> str:
        if isinstance(v, str):
            clean = v.strip()
            if clean.startswith("postgres://"):
                return clean.replace("postgres://", "postgresql+asyncpg://", 1)
            if clean.startswith("postgresql://") and not clean.startswith("postgresql+"):
                return clean.replace("postgresql://", "postgresql+asyncpg://", 1)
            return clean
        return str(v)

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
    ADMIN_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://animeclover.click",
        "http://animeclover.click",
    ]
    ADMIN_SECURE_COOKIE: bool = False

    CACHE_DIR: str = str(BACKEND_DIR / "cache")
    POSTER_CACHE_DIR: str = str(BACKEND_DIR / "cache" / "posters")
    DATA_DIR: str = str(BACKEND_DIR / "data")
    STREAM_CHUNK_SIZE: int = 1024 * 1024
    STREAM_PREBUFFER_BYTES: int = 2 * 1024 * 1024
    ARABIC_TRANSLATION_ENABLED: bool = True
    ARABIC_TRANSLATION_MODEL_DIR: str = str(BACKEND_DIR / "data" / "translation" / "en_ar")

    # Anti-Scraping & Streaming Security
    SECURITY_SIGNING_KEY: str = ""
    SECURITY_TOKEN_TTL_SECONDS: int = 300
    ENABLE_ANTI_BOT: bool = True
    ENABLE_TARPIT: bool = True
    TARPIT_DELAY_SECONDS: float = 3.0
    MAX_CONSECUTIVE_SESSION_HOURS: float = 10.0
    SESSION_INACTIVITY_BREAK_MINUTES: float = 30.0
    CONSECUTIVE_LOCKOUT_HOURS: float = 1.0
    ALLOWED_STREAM_HOSTS: List[str] = [
        "animeclover.click",
        "www.animeclover.click",
        "localhost",
        "127.0.0.1",
    ]

    CORS_ORIGINS: List[str] = ["*"]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow",
    )


settings = Settings()

# One private key shared by workers and retained across restarts. Never use the
# old publicly known key, including when it is left in deployment environment.
if not settings.SECURITY_SIGNING_KEY or settings.SECURITY_SIGNING_KEY == "animeclover-secure-streaming-hmac-key-2026-production":
    key_path = Path(settings.DATA_DIR) / "security-signing-key.txt"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with key_path.open("x", encoding="utf-8") as key_file:
            os.chmod(key_path, 0o600)
            key_file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    # Another worker may have created the file just before writing its contents.
    for attempt in range(20):
        settings.SECURITY_SIGNING_KEY = key_path.read_text(encoding="utf-8").strip()
        if settings.SECURITY_SIGNING_KEY:
            break
        time.sleep(0.05)
if len(settings.SECURITY_SIGNING_KEY) < 32:
    raise ValueError("SECURITY_SIGNING_KEY must contain at least 32 characters")
