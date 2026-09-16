"""标记强力锁原语：EXISTS → SET NX "1" → seal；禁止 async with 自动 DELETE。

供 credit_operation_lock / credit_claim_lock 等薄包装调用；Key 字符串由业务层冻结传入。
"""

from __future__ import annotations

from ...core.error_codes import ErrorCode
from ...core.errors import fail
from .redis_client import get_redis
from .redis_store import delete_keys, optional_redis_client, set_key

# 标记锁固定值（与现网 credit_operation_lock._MARK 一致）
MARK_LOCK_VALUE = "1"


async def mark_exists(key: str, *, fail_closed: bool = True) -> bool:
    """EXISTS；fail_closed 时 Redis 不可用抛 REDIS_UNAVAILABLE。"""
    client = await optional_redis_client()
    if client is None:
        if fail_closed:
            fail(ErrorCode.REDIS_UNAVAILABLE)
        return False
    return bool(await client.exists(key))


async def mark_peek_exists(key: str) -> bool | None:
    """只读探测：Redis 不可用时返回 None（支付 notify 等路径）。"""
    client = await optional_redis_client()
    if client is None:
        return None
    return bool(await client.exists(key))


async def mark_claim(
    key: str,
    *,
    ttl_sec: int,
    value: str = MARK_LOCK_VALUE,
    fail_closed: bool = True,
) -> bool | None:
    """SET key value NX EX ttl。fail_closed=False 且 Redis 不可用时返回 None。"""
    client = await optional_redis_client()
    if client is None:
        if fail_closed:
            fail(ErrorCode.REDIS_UNAVAILABLE)
        return None
    return bool(await client.set(key, value, nx=True, ex=max(1, int(ttl_sec))))


async def mark_seal(
    key: str,
    *,
    ttl_sec: int,
    value: str = MARK_LOCK_VALUE,
    fail_closed: bool = True,
) -> None:
    """业务成功后 SET 续 TTL（非 NX）。"""
    if fail_closed:
        await set_key(key, value, ex=max(1, int(ttl_sec)))
        return
    client = await optional_redis_client()
    if client is None:
        return
    await client.set(key, value, ex=max(1, int(ttl_sec)))


async def mark_delete(key: str) -> None:
    """显式清理（管理员 / generation_submit finally）；Redis 不可用时静默跳过。"""
    await delete_keys(key)
