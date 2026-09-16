"""Token 互斥锁：SET NX UUID + finally Lua 比对删除。与标记强力锁物理分离。"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ...core.error_codes import ErrorCode
from ...core.errors import fail
from .redis_client import get_redis

_UNLOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


async def require_redis_for_lock() -> None:
    if await get_redis() is None:
        fail(ErrorCode.REDIS_UNAVAILABLE)


@asynccontextmanager
async def token_mutex(
    key: str,
    *,
    ttl_sec: int = 30,
    busy_code: str = ErrorCode.CONFLICT,
    busy_message: str | None = None,
    busy_http_status: int | None = None,
) -> AsyncIterator[None]:
    """获取 Redis NX 互斥锁；Redis 宕机 fail-closed；正常结束 finally 删键。"""
    await require_redis_for_lock()
    client = await get_redis()
    assert client is not None

    token = uuid.uuid4().hex
    acquired = await client.set(key, token, nx=True, ex=max(1, int(ttl_sec)))
    if not acquired:
        fail(busy_code, message=busy_message, http_status=busy_http_status)
    try:
        yield
    finally:
        try:
            await client.eval(_UNLOCK_SCRIPT, 1, key, token)
        except Exception:
            pass


async def try_acquire_token_mutex(key: str, *, ttl_sec: int) -> str | None:
    """尝试占位；成功返回 token，失败返回 None。Redis 不可用时返回空字符串（Worker 降级）。"""
    client = await get_redis()
    if client is None:
        return ""
    token = uuid.uuid4().hex
    acquired = await client.set(key, token, nx=True, ex=max(60, int(ttl_sec)))
    return token if acquired else None


async def release_token_mutex(key: str, token: str) -> None:
    """按 token 释放；空 token 或 Redis 不可用则跳过。"""
    if not token:
        return
    client = await get_redis()
    if client is None:
        return
    try:
        await client.eval(_UNLOCK_SCRIPT, 1, key, token)
    except Exception:
        pass


# 向后兼容别名（原 redis_mutex 模块名）
redis_mutex = token_mutex
