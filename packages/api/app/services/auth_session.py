"""画布 HMAC 会话 token（方案 B）：DB 记录 + 吊销，兼容历史 JWT。"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select

from ..core.entity_ids import parse_entity_id
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.security import decode_jwt_token
from ..models.user import User, UserSession


def _session_secret() -> str:
    settings = get_settings()
    explicit = (getattr(settings, "canvas_session_secret", None) or "").strip()
    return explicit or settings.jwt_secret


def session_ttl_seconds() -> int:
    """会话 token 有效期（秒），至少 1 小时。"""
    settings = get_settings()
    return max(int(getattr(settings, "canvas_session_ttl_seconds", 0) or 0), 3600)


def sign_canvas_session(user_id: str, phone: str) -> str:
    """签发 HMAC 会话 token：userId|phone|exp|sig。"""
    exp = int(time.time()) + session_ttl_seconds()
    phone_norm = phone or ""
    payload = f"{user_id}|{phone_norm}|{exp}"
    sig = hmac.new(
        _session_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}|{sig}"


def verify_canvas_session_token(token: str) -> tuple[str, str] | None:
    """校验 HMAC 会话 token，成功返回 (user_id, phone)，失败返回 None。"""
    parts = (token or "").split("|")
    if len(parts) != 4:
        return None
    user_id, phone, exp_raw, sig = parts
    if not user_id:
        return None
    try:
        exp = int(exp_raw)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    payload = f"{user_id}|{phone}|{exp}"
    expected = hmac.new(
        _session_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    return user_id, phone


def is_hmac_session_token(token: str) -> bool:
    """判断 token 是否为有效的 HMAC 会话格式。"""
    return "|" in (token or "") and verify_canvas_session_token(token) is not None


def is_legacy_jwt_token(token: str) -> bool:
    """判断 token 是否为历史 JWT 格式（含点号、非 HMAC）。"""
    token = token or ""
    return "." in token and token.count(".") >= 2 and "|" not in token


async def get_active_session(db: AsyncSession, token: str) -> UserSession | None:
    """查询未过期的 user_sessions 记录。"""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(UserSession).filter(
            UserSession.token == token,
            UserSession.expires_at > now,
        )
    )
    return result.scalar_one_or_none()


async def create_user_session(db: AsyncSession, user: User) -> str:
    """登录成功后创建会话记录并返回 token。"""
    phone = user.phone or ""
    token = sign_canvas_session(str(user.id), phone)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=session_ttl_seconds())
    db.add(
        UserSession(
            user_id=user.id,
            token=token,
            expires_at=expires_at,
        )
    )
    await db.flush()
    return token


async def revoke_session(db: AsyncSession, *, token: str, user_id: int) -> int:
    """登出时删除 user_sessions 中的 token，返回删除行数。"""
    result = await db.execute(
        delete(UserSession).where(
            UserSession.token == token,
            UserSession.user_id == user_id,
        )
    )
    await db.flush()
    return int(result.rowcount or 0)


async def _load_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    uid = parse_entity_id(user_id)
    if uid is None:
        return None
    result = await db.execute(select(User).filter(User.id == uid))
    return result.scalar_one_or_none()


async def _load_user_by_sub(db: AsyncSession, sub: str) -> User | None:
    uid = parse_entity_id(sub)
    if uid is not None:
        result = await db.execute(select(User).filter(User.id == uid))
        user = result.scalar_one_or_none()
        if user:
            return user
    result = await db.execute(select(User).filter(User.source_user_id == sub))
    return result.scalar_one_or_none()


def _user_phone_matches(user: User, phone_from_token: str) -> bool:
    return (user.phone or "") == (phone_from_token or "")


async def resolve_user_from_token(db: AsyncSession, token: str) -> User | None:
    """从 token 解析当前用户：优先 HMAC 会话，兼容 JWT + user_sessions。"""
    if not token:
        return None

    if is_legacy_jwt_token(token):
        session = await get_active_session(db, token)
        if not session:
            return None
        payload = decode_jwt_token(token)
        if not payload:
            return None
        user = await _load_user_by_sub(db, str(payload.get("sub", "")))
        if not user or user.is_active is False:
            return None
        if session.user_id != user.id:
            return None
        return user

    verified = verify_canvas_session_token(token)
    if not verified:
        return None
    user_id, phone = verified
    session = await get_active_session(db, token)
    if not session:
        return None
    user = await _load_user_by_id(db, user_id)
    if not user or user.is_active is False:
        return None
    if session.user_id != user.id:
        return None
    if not _user_phone_matches(user, phone):
        return None
    return user
