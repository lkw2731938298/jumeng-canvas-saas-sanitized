"""Redis 发号器（单调递增序列）。"""

from __future__ import annotations

from ...core.error_codes import ErrorCode
from ...core.errors import fail
from .redis_client import get_redis
from .redis_keys import sequence_key

SEQUENCE_GENERATION_JOB = "generation_job"

_BOOTSTRAP_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local floor = tonumber(ARGV[1])
if not current then
  redis.call('SET', KEYS[1], floor)
  return floor
end
local cur = tonumber(current)
if cur < floor then
  redis.call('SET', KEYS[1], floor)
  return floor
end
return cur
"""


async def _require_client():
    client = await get_redis()
    if client is None:
        fail(ErrorCode.REDIS_UNAVAILABLE)
    return client


async def bootstrap_sequence(name: str, floor: int) -> int:
    """确保 Redis 计数器 ≥ floor（启动/迁移对账）。"""
    if floor < 0:
        floor = 0
    client = await _require_client()
    result = await client.eval(_BOOTSTRAP_SCRIPT, 1, sequence_key(name), floor)
    return int(result)


async def next_sequence(name: str) -> int:
    client = await _require_client()
    value = await client.incr(sequence_key(name))
    return int(value)


async def allocate_sequence_range(name: str, count: int) -> tuple[int, int]:
    if count <= 0:
        raise ValueError("count must be positive")
    client = await _require_client()
    end = int(await client.incrby(sequence_key(name), count))
    start = end - count + 1
    return start, end


async def next_daily_sequence(name: str, *, day_key: str, ttl_sec: int = 7 * 86400) -> int:
    client = await _require_client()
    key = sequence_key(f"daily:{name}:{day_key}")
    value = int(await client.incr(key))
    if value == 1:
        await client.expire(key, ttl_sec)
    return value
