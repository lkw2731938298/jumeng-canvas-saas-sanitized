"""Agent Access Key：签发、校验、吊销。"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.user import User
from ..models.user_agent_key import UserAgentKey

_KEY_PREFIX = "jm_ak_"
_MAX_ACTIVE_KEYS = 5


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _new_raw_key() -> str:
    # jm_ak_ + 40 hex ≈ 与常见 Access Key 长度接近
    return f"{_KEY_PREFIX}{secrets.token_hex(20)}"


def key_to_public_dict(row: UserAgentKey) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "keyPrefix": row.key_prefix,
        "createdAt": to_cst_iso(row.created_at) if row.created_at else None,
        "lastUsedAt": to_cst_iso(row.last_used_at) if row.last_used_at else None,
        "revokedAt": to_cst_iso(row.revoked_at) if row.revoked_at else None,
        "note": row.note,
    }


async def list_user_agent_keys(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(UserAgentKey)
            .where(UserAgentKey.user_id == int(user.id))
            .order_by(UserAgentKey.created_at.desc())
        )
    ).scalars().all()
    return [key_to_public_dict(r) for r in rows]


async def create_user_agent_key(
    db: AsyncSession,
    user: User,
    *,
    name: str = "default",
    note: str | None = None,
) -> dict[str, Any]:
    """创建 Key；响应含 accessKey 明文（仅此一次）。"""
    active = (
        await db.execute(
            select(UserAgentKey).where(
                UserAgentKey.user_id == int(user.id),
                UserAgentKey.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    if len(list(active)) >= _MAX_ACTIVE_KEYS:
        fail(
            ErrorCode.BAD_REQUEST,
            message=f"每个账号最多 {_MAX_ACTIVE_KEYS} 个有效 Access Key，请先吊销不用的密钥",
        )

    raw = _new_raw_key()
    row = UserAgentKey(
        user_id=int(user.id),
        key_prefix=raw[:16],
        key_hash=_hash_key(raw),
        name=(name or "default").strip()[:64] or "default",
        note=(note or None),
    )
    db.add(row)
    await db.flush()
    out = key_to_public_dict(row)
    out["accessKey"] = raw
    return out


async def revoke_user_agent_key(db: AsyncSession, user: User, key_id: str) -> dict[str, Any]:
    try:
        kid = int(str(key_id).strip())
    except (TypeError, ValueError):
        fail(ErrorCode.NOT_FOUND, message="Access Key 不存在")
    row = (
        await db.execute(
            select(UserAgentKey).where(
                UserAgentKey.id == kid,
                UserAgentKey.user_id == int(user.id),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        fail(ErrorCode.NOT_FOUND, message="Access Key 不存在")
    if row.revoked_at is None:
        row.revoked_at = now_cst_naive()
        await db.flush()
    return key_to_public_dict(row)


async def resolve_user_from_access_key(db: AsyncSession, raw_key: str) -> User | None:
    """OpenAPI Bearer Access Key → User；无效返回 None。"""
    token = (raw_key or "").strip()
    if not token.startswith(_KEY_PREFIX) or len(token) < 20:
        return None
    digest = _hash_key(token)
    row = (
        await db.execute(
            select(UserAgentKey).where(
                UserAgentKey.key_hash == digest,
                UserAgentKey.revoked_at.is_(None),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    user = (
        await db.execute(select(User).where(User.id == int(row.user_id)).limit(1))
    ).scalar_one_or_none()
    if user is None:
        return None
    row.last_used_at = now_cst_naive()
    await db.flush()
    return user
