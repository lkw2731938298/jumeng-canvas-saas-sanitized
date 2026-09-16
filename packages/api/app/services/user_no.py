"""User business number (U10001) allocation, backfill, and lookup."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import String, cast, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.user import User
from .redis_sequence import (
    SEQUENCE_USER_NO,
    allocate_sequence_range,
    bootstrap_sequence,
    next_sequence,
)

USER_NO_START = 10001
_USER_NO_PATTERN = re.compile(r"^U(\d+)$", re.IGNORECASE)


def format_user_no(seq: int) -> str:
    return f"U{seq}"


def parse_user_no_seq(value: str | None) -> int | None:
    if not value:
        return None
    match = _USER_NO_PATTERN.match(value.strip())
    if not match:
        return None
    return int(match.group(1))


async def _max_user_no_seq(db: AsyncSession) -> int:
    result = await db.execute(
        text(
            "SELECT MAX(CAST(SUBSTRING(user_no, 2) AS UNSIGNED)) "
            "FROM users WHERE user_no IS NOT NULL AND user_no REGEXP '^U[0-9]+$'"
        )
    )
    val = result.scalar()
    if val is None:
        return USER_NO_START - 1
    return int(val)


async def ensure_user_no_sequence_seeded(db: AsyncSession) -> None:
    """Align Redis counter with DB max so new numbers never collide."""
    db_max = await _max_user_no_seq(db)
    floor = max(db_max, USER_NO_START - 1)
    await bootstrap_sequence(SEQUENCE_USER_NO, floor)


async def allocate_user_no(db: AsyncSession) -> str:
    await ensure_user_no_sequence_seeded(db)
    seq = await next_sequence(SEQUENCE_USER_NO)
    if seq < USER_NO_START:
        await bootstrap_sequence(SEQUENCE_USER_NO, USER_NO_START - 1)
        seq = await next_sequence(SEQUENCE_USER_NO)
    return format_user_no(seq)


async def ensure_user_no(db: AsyncSession, user: User) -> str:
    existing = (user.user_no or "").strip()
    if existing:
        return existing
    user.user_no = await allocate_user_no(db)
    return user.user_no


async def backfill_missing_user_nos(db: AsyncSession) -> int:
    result = await db.execute(
        select(User)
        .where(or_(User.user_no.is_(None), User.user_no == ""))
        .order_by(User.created_at.asc(), User.id.asc())
    )
    users = result.scalars().all()
    if not users:
        return 0

    await ensure_user_no_sequence_seeded(db)
    start, _end = await allocate_sequence_range(SEQUENCE_USER_NO, len(users))
    if start < USER_NO_START:
        await bootstrap_sequence(SEQUENCE_USER_NO, USER_NO_START - 1)
        start, _end = await allocate_sequence_range(SEQUENCE_USER_NO, len(users))

    for offset, user in enumerate(users):
        user.user_no = format_user_no(start + offset)
    return len(users)


async def resolve_user_by_ref(db: AsyncSession, ref: str) -> User:
    trimmed = ref.strip()
    if not trimmed:
        fail(ErrorCode.INVALID_ID, message="用户标识无效")

    try:
        user_uuid = UUID(trimmed)
    except ValueError:
        user_uuid = None

    if user_uuid is not None:
        result = await db.execute(select(User).filter(User.id == user_uuid))
        user = result.scalar_one_or_none()
        if user:
            return user

    if parse_user_no_seq(trimmed) is not None:
        result = await db.execute(select(User).filter(User.user_no.ilike(trimmed)))
        user = result.scalar_one_or_none()
        if user:
            return user

    fail(ErrorCode.USER_NOT_FOUND)


async def resolve_user_ids_for_filter(db: AsyncSession, raw: str) -> list[UUID]:
    trimmed = raw.strip()
    if not trimmed:
        return []

    try:
        user_uuid = UUID(trimmed)
        result = await db.execute(select(User.id).filter(User.id == user_uuid))
        row = result.scalar_one_or_none()
        return [row] if row else []
    except ValueError:
        pass

    if parse_user_no_seq(trimmed) is not None:
        result = await db.execute(select(User.id).filter(User.user_no.ilike(f"{trimmed}%")))
        return list(result.scalars().all())

    like = f"%{trimmed}%"
    prefix = f"{trimmed}%"
    result = await db.execute(
        select(User.id).filter(
            or_(
                User.phone.ilike(like),
                User.display_name.ilike(like),
                User.user_no.ilike(prefix),
                cast(User.id, String).like(prefix),
            )
        )
    )
    return list(result.scalars().all())
