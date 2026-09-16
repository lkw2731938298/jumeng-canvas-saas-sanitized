"""Lightweight Redis cache helpers (manifest + rate limiting)."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .redis_client import get_redis

logger = logging.getLogger(__name__)

MANIFEST_CACHE_TTL_S = 60


def manifest_cache_key(project_id: str) -> str:
    return f"manifest:{project_id}"


async def get_cached_manifest(project_id: str) -> Optional[list[dict[str, Any]]]:
    redis = await get_redis()
    if not redis:
        return None
    try:
        raw = await redis.get(manifest_cache_key(project_id))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except Exception as exc:
        logger.debug("Manifest cache read failed: %s", exc)
        return None


async def set_cached_manifest(project_id: str, items: list[dict[str, Any]]) -> None:
    redis = await get_redis()
    if not redis:
        return
    try:
        await redis.setex(
            manifest_cache_key(project_id),
            MANIFEST_CACHE_TTL_S,
            json.dumps(items, ensure_ascii=False),
        )
    except Exception as exc:
        logger.debug("Manifest cache write failed: %s", exc)


async def invalidate_manifest_cache(project_id: str) -> None:
    redis = await get_redis()
    if not redis:
        return
    try:
        await redis.delete(manifest_cache_key(project_id))
    except Exception as exc:
        logger.debug("Manifest cache invalidate failed: %s", exc)


async def check_rate_limit(
    key: str,
    *,
    limit: int,
    window_s: int,
) -> bool:
    """Return True if the request is allowed; False if rate limited."""
    redis = await get_redis()
    if not redis:
        return True
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_s)
        return int(count) <= limit
    except Exception as exc:
        logger.debug("Rate limit check failed: %s", exc)
        return True
