"""Ephemeral online presence for canvas collaboration (Redis, best-effort)."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..models.user import User
from .redis_client import get_redis

logger = logging.getLogger(__name__)

PRESENCE_TTL_S = 30


def _presence_hash_key(project_id: str) -> str:
    return f"presence:project:{project_id}"


async def touch_project_presence(project_id: str, user: User) -> None:
    redis = await get_redis()
    if not redis:
        return
    key = _presence_hash_key(project_id)
    payload = json.dumps(
        {
            "displayName": user.display_name or "",
            "ts": time.time(),
        },
        ensure_ascii=False,
    )
    try:
        await redis.hset(key, str(user.id), payload)
        await redis.expire(key, PRESENCE_TTL_S * 3)
    except Exception as exc:
        logger.debug("Presence touch failed: %s", exc)


async def list_project_presence(
    project_id: str,
    *,
    exclude_user_id: str | None = None,
) -> list[dict[str, Any]]:
    redis = await get_redis()
    if not redis:
        return []
    key = _presence_hash_key(project_id)
    try:
        raw = await redis.hgetall(key)
    except Exception as exc:
        logger.debug("Presence list failed: %s", exc)
        return []

    if not raw:
        return []

    now = time.time()
    cutoff = now - PRESENCE_TTL_S
    stale_fields: list[str] = []
    active: list[dict[str, Any]] = []

    for field, value in raw.items():
        user_id = field.decode() if isinstance(field, bytes) else str(field)
        if exclude_user_id and user_id == exclude_user_id:
            continue
        text = value.decode() if isinstance(value, bytes) else str(value)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            stale_fields.append(user_id)
            continue
        ts = float(data.get("ts") or 0)
        if ts < cutoff:
            stale_fields.append(user_id)
            continue
        active.append(
            {
                "userId": user_id,
                "displayName": str(data.get("displayName") or ""),
            }
        )

    if stale_fields:
        try:
            await redis.hdel(key, *stale_fields)
        except Exception:
            pass

    active.sort(key=lambda row: row["displayName"] or row["userId"])
    return active


async def clear_project_presence(project_id: str, user_id: str) -> None:
    redis = await get_redis()
    if not redis:
        return
    try:
        await redis.hdel(_presence_hash_key(project_id), user_id)
    except Exception as exc:
        logger.debug("Presence clear failed: %s", exc)
