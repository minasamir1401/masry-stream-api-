import asyncio
import hashlib
import hmac
import logging
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlsplit
from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger("SecurityShield")

BOT_USER_AGENT_PATTERNS = [
    re.compile(r"python-requests", re.IGNORECASE),
    re.compile(r"aiohttp", re.IGNORECASE),
    re.compile(r"httpx", re.IGNORECASE),
    re.compile(r"scrapy", re.IGNORECASE),
    re.compile(r"urllib", re.IGNORECASE),
    re.compile(r"curl/", re.IGNORECASE),
    re.compile(r"wget/", re.IGNORECASE),
    re.compile(r"go-http-client", re.IGNORECASE),
    re.compile(r"headlesschrome", re.IGNORECASE),
    re.compile(r"phantomjs", re.IGNORECASE),
    re.compile(r"puppeteer", re.IGNORECASE),
    re.compile(r"playwright", re.IGNORECASE),
    re.compile(r"selenium", re.IGNORECASE),
    re.compile(r"postmanruntime", re.IGNORECASE),
]


class SecurityShield:
    def __init__(self):
        self.secret_key = settings.SECURITY_SIGNING_KEY.encode("utf-8")
        self._session_records: dict[str, dict] = {}
        self._last_session_purge: float = time.time()

    def check_consecutive_session(self, client_ip: str) -> Tuple[bool, str, int]:
        now = time.time()
        max_duration = settings.MAX_CONSECUTIVE_SESSION_HOURS * 3600
        inactivity_gap = settings.SESSION_INACTIVITY_BREAK_MINUTES * 60
        lockout_duration = settings.CONSECUTIVE_LOCKOUT_HOURS * 3600

        if now - self._last_session_purge > 600:
            self._last_session_purge = now
            stale_keys = [
                ip for ip, rec in self._session_records.items()
                if now - rec.get("last_seen", 0) > 86400 and now >= rec.get("locked_until", 0)
            ]
            for ip in stale_keys:
                self._session_records.pop(ip, None)

        record = self._session_records.get(client_ip)

        if record:
            locked_until = record.get("locked_until", 0)
            if locked_until and now < locked_until:
                remaining = int(locked_until - now)
                return True, "تم تعليق الجلسة لتجاوز الحد الأقصى للاستخدام المتواصل (10 ساعات). يرجى أخذ استراحة والعودة لاحقاً.", remaining
            elif locked_until and now >= locked_until:
                record["first_seen"] = now
                record["last_seen"] = now
                record["locked_until"] = 0
                return False, "", 0

            last_seen = record.get("last_seen", now)
            # Break detected: user was away longer than inactivity threshold, reset continuous session
            if now - last_seen > inactivity_gap:
                record["first_seen"] = now
                record["last_seen"] = now
                record["locked_until"] = 0
                return False, "", 0

            # Session is continuous
            first_seen = record.get("first_seen", now)
            if now - first_seen > max_duration:
                record["locked_until"] = now + lockout_duration
                return True, "تم تعليق الجلسة لتجاوز الحد الأقصى للاستخدام المتواصل (10 ساعات). يرجى أخذ استراحة والعودة لاحقاً.", int(lockout_duration)

            record["last_seen"] = now
            return False, "", 0

        self._session_records[client_ip] = {
            "first_seen": now,
            "last_seen": now,
            "locked_until": 0,
        }
        return False, "", 0

    def validate_request_access(self, request: Request) -> Tuple[bool, str, int]:
        client_ip = self.get_client_ip(request)

        # 1. Enforce continuous 10-hour limit (intermittent usage is allowed)
        is_locked, lock_reason, retry_after = self.check_consecutive_session(client_ip)
        if is_locked:
            return True, lock_reason, retry_after

        # 2. Enforce anti-bot scraper detection
        is_bot, bot_reason = self.is_automated_client(request)
        if is_bot:
            return True, bot_reason, 0

        return False, "", 0

    def get_client_ip(self, request: Request) -> str:
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        if request.client and request.client.host:
            return request.client.host

        return "127.0.0.1"

    def get_ua_hash(self, user_agent: str) -> str:
        return hashlib.sha256((user_agent or "").encode("utf-8")).hexdigest()[:16]

    def is_automated_client(self, request: Request) -> Tuple[bool, str]:
        if not settings.ENABLE_ANTI_BOT:
            return False, ""

        user_agent = request.headers.get("user-agent", "").strip()
        if not user_agent:
            return True, "Missing User-Agent header"

        for pattern in BOT_USER_AGENT_PATTERNS:
            if pattern.search(user_agent):
                return True, f"Automated crawler detected: {user_agent[:32]}"

        sec_fetch_site = request.headers.get("sec-fetch-site")
        if sec_fetch_site and sec_fetch_site.lower() == "cross-site":
            # Direct cross-site hotlink attempts
            return True, "Unauthorized cross-site request"

        return False, ""

    def verify_referrer_origin(self, request: Request) -> bool:
        if not settings.ENABLE_ANTI_BOT:
            return True

        referer = request.headers.get("referer")
        origin = request.headers.get("origin")

        target = origin or referer
        if not target:
            # Allow direct internal or same-origin player requests if token is valid
            return True

        try:
            parsed = urlsplit(target)
            hostname = (parsed.hostname or "").lower()
            return any(
                hostname == allowed or hostname.endswith(f".{allowed}")
                for allowed in settings.ALLOWED_STREAM_HOSTS
            )
        except Exception:
            return False

    def generate_token(
        self,
        identifier: str,
        client_ip: str,
        user_agent: str,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        ttl = ttl_seconds or settings.SECURITY_TOKEN_TTL_SECONDS
        expires_at = int(time.time()) + ttl
        ua_hash = self.get_ua_hash(user_agent)
        
        payload = f"{identifier}|{client_ip}|{ua_hash}|{expires_at}"
        signature = hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        
        return f"{expires_at}.{ua_hash}.{signature}"

    def verify_token(
        self,
        token: Optional[str],
        identifier: str,
        client_ip: str,
        user_agent: str,
    ) -> Tuple[bool, str]:
        if not token:
            return False, "رمز الحماية مفقود"

        parts = token.split(".")
        if len(parts) != 3:
            return False, "صيغة رمز الحماية غير صالحة"

        expires_str, token_ua_hash, signature = parts

        try:
            expires_at = int(expires_str)
        except ValueError:
            return False, "تاريخ انتهاء الرمز غير صالح"

        if time.time() > expires_at:
            return False, "انتهت صلاحية جلسة المشاهدة"

        expected_ua_hash = self.get_ua_hash(user_agent)
        if not hmac.compare_digest(token_ua_hash, expected_ua_hash):
            return False, "جلسة المشاهدة غير متطابقة مع المتصفح"

        payload = f"{identifier}|{client_ip}|{token_ua_hash}|{expires_at}"
        expected_sig = hmac.new(self.secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

        if not hmac.compare_digest(signature, expected_sig):
            return False, "توقيع الحماية غير صالح"

        return True, ""

    async def apply_tarpit(self, delay: Optional[float] = None) -> None:
        if settings.ENABLE_TARPIT:
            wait_time = delay if delay is not None else settings.TARPIT_DELAY_SECONDS
            await asyncio.sleep(wait_time)


security_shield = SecurityShield()
