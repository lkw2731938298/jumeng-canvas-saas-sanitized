"""通用 Redis 命令封装；不含锁语义与业务 Key 规则。"""

from __future__ import annotations

import json
from typing import Any

from ...core.error_codes import ErrorCode
from ...core.errors import fail
from .redis_client import get_redis


async def require_redis_client():
    """Redis 不可用时 fail-closed。"""
    client = await get_redis()
    if client is None:
        fail(ErrorCode.REDIS_UNAVAILABLE)
    return client


async def optional_redis_client():
    return await get_redis()


async def set_key(
    key: str,
    value: str,
    *,
    ex: int | None = None,
    nx: bool = False,
) -> bool:
    client = await require_redis_client()
    result = await client.set(key, value, ex=ex, nx=nx)
    return bool(result)


async def setex_key(key: str, ttl: int, value: str) -> None:
    client = await require_redis_client()
    await client.set(key, value, ex=max(1, int(ttl)))


async def get_key(key: str) -> str | None:
    client = await require_redis_client()
    return await client.get(key)


async def delete_keys(*keys: str) -> int:
    if not keys:
        return 0
    client = await optional_redis_client()
    if client is None:
        return 0
    return int(await client.delete(*keys))


async def exists_key(key: str) -> bool:
    client = await require_redis_client()
    return bool(await client.exists(key))


async def peek_exists_key(key: str) -> bool | None:
    """只读探测：Redis 不可用时返回 None。"""
    client = await optional_redis_client()
    if client is None:
        return None
    return bool(await client.exists(key))


async def expire_key(key: str, ttl: int) -> bool:
    client = await require_redis_client()
    return bool(await client.expire(key, max(1, int(ttl))))


async def ttl_key(key: str) -> int:
    client = await optional_redis_client()
    if client is None:
        return 0
    ttl = int(await client.ttl(key))
    return max(0, ttl)


async def incr_key(key: str) -> int:
    client = await require_redis_client()
    return int(await client.incr(key))


async def incrby_key(key: str, amount: int) -> int:
    client = await require_redis_client()
    return int(await client.incrby(key, amount))


async def set_json(key: str, value: Any, *, ttl: int) -> None:
    client = await require_redis_client()
    payload = json.dumps({"v": value}, ensure_ascii=False)
    await client.set(key, payload, ex=max(1, int(ttl)))


async def get_json(key: str) -> Any | None:
    client = await optional_redis_client()
    if client is None:
        return None
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


async def lrange_all(key: str) -> list[str]:
    """读取 List 全部元素；Redis 不可用时返回空列表。"""
    client = await optional_redis_client()
    if client is None:
        return []
    raw = await client.lrange(key, 0, -1)
    return [str(item) for item in raw] if raw else []


async def replace_list(key: str, items: list[str], *, ttl: int | None = None) -> None:
    """原子替换 List：DEL 后 RPUSH 全量元素，可选 TTL。"""
    client = await optional_redis_client()
    if client is None:
        return
    pipe = client.pipeline(transaction=True)
    pipe.delete(key)
    if items:
        pipe.rpush(key, *items)
    if ttl is not None and ttl > 0:
        pipe.expire(key, max(1, int(ttl)))
    await pipe.execute()
