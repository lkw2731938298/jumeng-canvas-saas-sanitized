"""Numeric entity ID parsing and display helpers (BIGINT primary keys)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.user import User

USER_ID_START = 10001
_DEV_USER_ID = 1
_USER_NO_PATTERN = re.compile(r"^U(\d+)$", re.IGNORECASE)
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def format_user_display_id(user_id: int | None) -> str:
    """用户展示编号：仅数字（如 10032），不加 U 前缀；解析仍兼容历史 U10032。"""
    if user_id is None:
        return ""
    return str(user_id)


def parse_user_display_id(value: str | None) -> int | None:
    if not value:
        return None
    trimmed = value.strip()
    match = _USER_NO_PATTERN.match(trimmed)
    if match:
        return int(match.group(1))
    return parse_entity_id(trimmed)


def parse_entity_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    trimmed = str(value).strip()
    if not trimmed:
        return None
    if trimmed.isdigit():
        parsed = int(trimmed)
        return parsed if parsed > 0 else None
    return None


def require_entity_id(value: str | int | None, *, message: str = "标识无效") -> int:
    parsed = parse_entity_id(value)
    if parsed is None:
        fail(ErrorCode.INVALID_ID, message=message)
    return parsed


def is_legacy_uuid(value: str | None) -> bool:
    if not value:
        return False
    return bool(_UUID_PATTERN.match(value.strip()))


def rewrite_uuid_map_in_json(value: Any, uuid_to_int: dict[str, int]) -> Any:
    """Recursively replace known UUID strings with numeric strings in JSON-like structures."""
    if isinstance(value, dict):
        return {k: rewrite_uuid_map_in_json(v, uuid_to_int) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_uuid_map_in_json(item, uuid_to_int) for item in value]
    if isinstance(value, str) and value in uuid_to_int:
        return str(uuid_to_int[value])
    return value


async def resolve_user_by_ref(db: AsyncSession, ref: str) -> User:
    trimmed = ref.strip()
    if not trimmed:
        fail(ErrorCode.INVALID_ID, message="用户标识无效")

    user_id = parse_user_display_id(trimmed)
    if user_id is not None:
        result = await db.execute(select(User).filter(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            return user

    if is_legacy_uuid(trimmed):
        fail(ErrorCode.USER_NOT_FOUND)

    like = f"%{trimmed}%"
    prefix = f"{trimmed}%"
    result = await db.execute(
        select(User).filter(
            or_(
                User.phone.ilike(like),
                User.display_name.ilike(like),
                cast(User.id, String).like(prefix),
            )
        )
    )
    users = result.scalars().all()
    if len(users) == 1:
        return users[0]
    if len(users) > 1:
        fail(ErrorCode.INVALID_ID, message="匹配到多个用户，请缩小筛选条件")
    fail(ErrorCode.USER_NOT_FOUND)


async def resolve_user_ids_for_filter(db: AsyncSession, raw: str) -> list[int]:
    trimmed = raw.strip()
    if not trimmed:
        return []

    user_id = parse_user_display_id(trimmed)
    if user_id is not None:
        result = await db.execute(select(User.id).filter(User.id == user_id))
        row = result.scalar_one_or_none()
        return [row] if row else []

    like = f"%{trimmed}%"
    prefix = f"{trimmed}%"
    result = await db.execute(
        select(User.id).filter(
            or_(
                User.phone.ilike(like),
                User.display_name.ilike(like),
                cast(User.id, String).like(prefix),
            )
        )
    )
    return list(result.scalars().all())
