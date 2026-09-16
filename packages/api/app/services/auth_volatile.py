"""基于 Redis 的短时键值存储，供认证风控与短信验证码等易失数据使用。"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .redis_client import get_redis

_volatile_lock = asyncio.Lock()
_volatile_store: dict[str, tuple[Any, float]] = {}


def _volatile_prune(now_ts: float | None = None) -> None:
    now = now_ts or time.time()
    expired = [key for key, (_, expires_at) in _volatile_store.items() if expires_at <= now]
    for key in expired:
        _volatile_store.pop(key, None)


async def volatile_get(key: str) -> Any | None:
    """读取带 TTL 的易失键值，不存在或已过期返回 None。"""
    client = await get_redis()
    if client is not None:
        raw = await client.get(key)
        if raw is None:
            return None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and "v" in parsed:
                return parsed["v"]
        except json.JSONDecodeError:
            return raw
        return raw
    async with _volatile_lock:
        _volatile_prune()
        item = _volatile_store.get(key)
        return item[0] if item else None


async def volatile_set(key: str, value: Any, ttl_seconds: int) -> None:
    """写入带过期时间的易失键值。"""
    client = await get_redis()
    if client is not None:
        await client.set(key, json.dumps({"v": value}, ensure_ascii=False), ex=max(1, ttl_seconds))
        return
    async with _volatile_lock:
        _volatile_prune()
        _volatile_store[key] = (value, time.time() + max(1, ttl_seconds))


async def volatile_delete(*keys: str) -> None:
    """删除一个或多个易失键。"""
    client = await get_redis()
    if client is not None and keys:
        await client.delete(*keys)
        return
    async with _volatile_lock:
        for key in keys:
            _volatile_store.pop(key, None)


async def volatile_incr(key: str, ttl_seconds: int) -> int:
    """原子递增计数器并刷新 TTL，返回递增后的值。"""
    client = await get_redis()
    if client is not None:
        value = int(await client.incr(key))
        if value == 1 or int(await client.ttl(key)) < 0:
            await client.expire(key, max(1, ttl_seconds))
        return value
    async with _volatile_lock:
        now_ts = time.time()
        _volatile_prune(now_ts)
        value, expires_at = _volatile_store.get(key, (0, now_ts + max(1, ttl_seconds)))
        next_value = int(value or 0) + 1
        _volatile_store[key] = (next_value, expires_at)
        return next_value


async def volatile_ttl(key: str) -> int:
    """返回键剩余存活秒数，不存在则返回 0。"""
    client = await get_redis()
    if client is not None:
        ttl = int(await client.ttl(key))
        return max(0, ttl)
    async with _volatile_lock:
        _volatile_prune()
        item = _volatile_store.get(key)
        if not item:
            return 0
        return max(0, int(item[1] - time.time()))
