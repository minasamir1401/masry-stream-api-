import hashlib
import hmac
import math
import os
import secrets
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from time import monotonic
from pathlib import Path
from typing import Optional, Tuple

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AdminSession, AdminUser
from app.database import get_db

COOKIE_NAME = "masry_admin"
SESSION_SECONDS = 7 * 24 * 60 * 60  # 7 days
login_failures: deque[float] = deque()


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()


def verify_password(password: str, salt: str, expected_hash: str) -> bool:
    computed = hash_password(password, salt)
    return secrets.compare_digest(computed, expected_hash)


def get_stored_fallback_password() -> str:
    if settings.ADMIN_PASSWORD:
        return settings.ADMIN_PASSWORD
    path = Path(settings.DATA_DIR) / "admin-password.txt"
    try:
        with path.open(encoding="utf-8") as file:
            content = file.read().strip()
            if content:
                return content
    except FileNotFoundError:
        pass
    new_password = secrets.token_urlsafe(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_password + "\n", encoding="utf-8")
    return new_password


def save_fallback_password(password: str) -> None:
    path = Path(settings.DATA_DIR) / "admin-password.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password + "\n", encoding="utf-8")


async def ensure_default_admin(db: AsyncSession) -> AdminUser:
    result = await db.execute(select(AdminUser).limit(1))
    admin = result.scalar_one_or_none()
    if admin is not None:
        return admin

    default_username = "admin"
    default_pass = get_stored_fallback_password()
    salt = secrets.token_hex(16)
    pwd_hash = hash_password(default_pass, salt)

    admin = AdminUser(
        username=default_username,
        password_hash=pwd_hash,
        salt=salt,
    )
    db.add(admin)
    await db.commit()
    await db.refresh(admin)
    return admin


def check_admin_origin(request: Request):
    origin = request.headers.get("origin")
    if origin is not None:
        if "*" in settings.ADMIN_ORIGINS:
            return
        if origin in settings.ADMIN_ORIGINS:
            return
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if host and (f"http://{host}" == origin or f"https://{host}" == origin):
            return
        raise HTTPException(403, "افتح لوحة الإدارة من رابط الموقع المسموح")


async def session_username(token: Optional[str], db: AsyncSession) -> Optional[str]:
    if not token or len(token) > 256:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(AdminUser.username).join(AdminSession, AdminSession.user_id == AdminUser.id).where(
            AdminSession.token_hash == token_hash,
            AdminSession.expires_at > datetime.now(timezone.utc),
        )
    )
    return result.scalar_one_or_none()


def extract_token_from_request(request: Request) -> Optional[str]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token

    header_token = request.headers.get("x-admin-token")
    if header_token:
        return header_token.strip()

    auth = request.headers.get("authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:].strip()

    return None


async def is_valid_admin_token(token: Optional[str], db: AsyncSession) -> bool:
    return await session_username(token, db) is not None


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    check_admin_origin(request)
    username = await session_username(extract_token_from_request(request), db)
    if not username:
        raise HTTPException(401, "انتهت الجلسة. يرجى تسجيل الدخول مجددًا")
    return username


async def login(
    username: str,
    password: str,
    request: Request,
    response: Response,
    db: AsyncSession,
) -> Tuple[str, str]:
    check_admin_origin(request)
    now = monotonic()
    while login_failures and login_failures[0] <= now - 60:
        login_failures.popleft()
    if len(login_failures) >= 5:
        retry_after = max(1, math.ceil(60 - (now - login_failures[0])))
        raise HTTPException(
            429,
            "محاولات تسجيل دخول كثيرة. انتظر قبل المحاولة مجددًا",
            headers={"Retry-After": str(retry_after)},
        )

    clean_user = username.strip().lower()
    result = await db.execute(select(AdminUser).where(AdminUser.username == clean_user))
    admin_user = result.scalar_one_or_none()

    if admin_user is None:
        all_admins = await db.execute(select(AdminUser).limit(1))
        if all_admins.scalar_one_or_none() is None:
            admin_user = await ensure_default_admin(db)
            if clean_user != admin_user.username:
                admin_user = None

    is_valid = False
    if admin_user is not None:
        is_valid = verify_password(password, admin_user.salt, admin_user.password_hash)

    if not is_valid:
        login_failures.append(now)
        raise HTTPException(401, "اسم المستخدم أو كلمة المرور غير صحيحة")

    login_failures.clear()

    session_token = secrets.token_urlsafe(48)
    await db.execute(delete(AdminSession).where(AdminSession.expires_at <= datetime.now(timezone.utc)))
    db.add(AdminSession(
        token_hash=hashlib.sha256(session_token.encode()).hexdigest(),
        user_id=admin_user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS),
    ))
    await db.commit()

    is_secure = (
        settings.ADMIN_SECURE_COOKIE
        or request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )
    response.set_cookie(
        COOKIE_NAME,
        session_token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_secure,
        path="/",
    )
    return admin_user.username, session_token


async def logout(request: Request, response: Response, db: AsyncSession):
    token = extract_token_from_request(request)
    if token:
        await db.execute(delete(AdminSession).where(
            AdminSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(COOKIE_NAME, path="/api")


async def update_admin_credentials(
    current_username: str,
    current_password: str,
    new_username: Optional[str],
    new_password: Optional[str],
    db: AsyncSession,
) -> Tuple[str, str]:
    result = await db.execute(
        select(AdminUser).where(AdminUser.username == current_username)
    )
    admin_user = result.scalar_one_or_none()
    if admin_user is None:
        raise HTTPException(404, "المستخدم غير موجود")

    if not verify_password(current_password, admin_user.salt, admin_user.password_hash):
        raise HTTPException(400, "كلمة المرور الحالية غير صحيحة")

    updated_username = admin_user.username
    if new_username:
        clean_new_user = new_username.strip().lower()
        if len(clean_new_user) < 3 or len(clean_new_user) > 32:
            raise HTTPException(400, "يجب أن يتراوح طول اسم المستخدم بين 3 و32 حرفًا")
        if clean_new_user != admin_user.username:
            check_existing = await db.execute(
                select(AdminUser).where(AdminUser.username == clean_new_user)
            )
            if check_existing.scalar_one_or_none() is not None:
                raise HTTPException(400, "اسم المستخدم الجديد مستخدم بالفعل")
            admin_user.username = clean_new_user
            updated_username = clean_new_user

    if new_password:
        if len(new_password) < 6 or len(new_password) > 256:
            raise HTTPException(400, "يجب أن تتكون كلمة المرور الجديدة من 6 أحرف على الأقل")
        new_salt = secrets.token_hex(16)
        admin_user.salt = new_salt
        admin_user.password_hash = hash_password(new_password, new_salt)

    await db.execute(delete(AdminSession).where(AdminSession.user_id == admin_user.id))
    await db.commit()
    await db.refresh(admin_user)
    return updated_username, "تم تحديث بيانات الحساب بنجاح"
