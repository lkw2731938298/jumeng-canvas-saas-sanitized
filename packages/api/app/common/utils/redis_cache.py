"""Cache-Aside 模板：读穿透 + 版本号失效；Redis 不可用时降级直读 loader。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from .redis_client import get_redis
from .redis_keys import cache_version_key
from .redis_store import optional_redis_client

T = TypeVar("T")

# 默认模型目录缓存 TTL（秒）
DEFAULT_MODEL_CACHE_TTL_SEC = 600


async def get_cache_version(domain: str) -> int:
    """读取域版本号；不存在视为 0。"""
    client = await optional_redis_client()
    if client is None:
        return 0
    raw = await client.get(cache_version_key(domain))
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


async def bump_cache_version(domain: str) -> int:
    """递增域版本号，使带 ver 的缓存条目失效。"""
    client = await optional_redis_client()
    if client is None:
        return 0
    return int(await client.incr(cache_version_key(domain)))


async def cache_invalidate(*keys: str) -> int:
    """删除指定缓存 key；Redis 不可用时返回 0。"""
    if not keys:
        return 0
    client = await optional_redis_client()
    if client is None:
        return 0
    return int(await client.delete(*keys))


async def cache_get_or_load(
    key: str,
    *,
    ttl: int,
    loader: Callable[[], Awaitable[T | None]],
    version_domain: str | None = None,
    cache_null: bool = False,
) -> T | None:
    """先读 Redis；miss 或版本不一致则 await loader() 并回写。

    - Redis 不可用：直接 await loader()（读路径 fail-open）
    - cache_null=False 时不缓存 None（避免新建模型后长期 404）
    """
    ver = await get_cache_version(version_domain) if version_domain else 0
    client = await optional_redis_client()
    if client is not None:
        raw = await client.get(key)
        if raw:
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and payload.get("ver") == ver:
                    return payload.get("data")  # type: ignore[return-value]
            except json.JSONDecodeError:
                pass

    data = await loader()
    if client is not None and (data is not None or cache_null):
        envelope = json.dumps({"ver": ver, "data": data}, ensure_ascii=False)
        await client.set(key, envelope, ex=max(1, int(ttl)))
    return data


async def bump_model_catalog_cache() -> int:
    """模型目录 DB 变更后调用，全局失效模型读缓存。"""
    return await bump_cache_version("model")
