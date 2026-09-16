"""共享 async Redis 客户端（连接层；业务命令见 redis_store / redis_mark_lock）。"""

from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis

from ...core.config import get_settings

logger = logging.getLogger(__name__)

_redis: Optional[aioredis.Redis] = None
_redis_checked = False


async def get_redis() -> Optional[aioredis.Redis]:
    """返回全局 Redis 客户端；不可用时返回 None（调用方按域决定 fail-closed）。"""
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    settings = get_settings()
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        client = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=2.0)
        await client.ping()
        _redis = client
        logger.info("Redis connected at %s", url.split("@")[-1])
    except Exception as exc:
        logger.warning("Redis unavailable (%s); cache/rate-limit disabled", exc)
        _redis = None
    return _redis


async def close_redis() -> None:
    """关闭连接（测试或进程退出）。"""
    global _redis, _redis_checked
    if _redis is not None:
        await _redis.aclose()
    _redis = None
    _redis_checked = False
