"""平台级 Prompt 模板/配置：OSS 权威 + Redis 跨进程缓存。"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..common.utils.redis_cache import bump_cache_version, get_cache_version
from ..common.utils.redis_keys import build_cache_key
from ..common.utils.redis_store import optional_redis_client
from ..integrations.oss.canvas_storage import get_canvas_storage

logger = logging.getLogger(__name__)

PROMPT_TEMPLATES_OSS_REL = "prompt-templates.json"
PROMPT_CONFIG_OSS_REL = "prompt-config.json"

PROMPT_TEMPLATES_CACHE_KEY = build_cache_key("platform", "prompt-templates", "json")
PROMPT_CONFIG_CACHE_KEY = build_cache_key("platform", "prompt-config", "json")

PROMPT_SHARED_CACHE_TTL_SEC = 7 * 24 * 3600


def _envelope(ver: int, data: Any) -> str:
    return json.dumps({"ver": ver, "data": data}, ensure_ascii=False)


def _parse_envelope(raw: str, *, expected_ver: int) -> Any | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("ver") != expected_ver:
        return None
    return payload.get("data")


async def read_prompt_templates_from_redis() -> dict[str, Any] | None:
    client = await optional_redis_client()
    if client is None:
        return None
    ver = await get_cache_version("prompt_templates")
    raw = await client.get(PROMPT_TEMPLATES_CACHE_KEY)
    if not raw:
        return None
    data = _parse_envelope(raw, expected_ver=ver)
    return data if isinstance(data, dict) else None


async def read_prompt_config_from_redis() -> dict[str, Any] | None:
    client = await optional_redis_client()
    if client is None:
        return None
    ver = await get_cache_version("prompt_config")
    raw = await client.get(PROMPT_CONFIG_CACHE_KEY)
    if not raw:
        return None
    data = _parse_envelope(raw, expected_ver=ver)
    return data if isinstance(data, dict) else None


async def publish_prompt_templates_to_redis(data: dict[str, Any], *, ver: int | None = None) -> bool:
    client = await optional_redis_client()
    if client is None:
        return False
    version = ver if ver is not None else await get_cache_version("prompt_templates")
    await client.set(
        PROMPT_TEMPLATES_CACHE_KEY,
        _envelope(version, data),
        ex=PROMPT_SHARED_CACHE_TTL_SEC,
    )
    return True


async def publish_prompt_config_to_redis(data: dict[str, Any], *, ver: int | None = None) -> bool:
    client = await optional_redis_client()
    if client is None:
        return False
    version = ver if ver is not None else await get_cache_version("prompt_config")
    await client.set(
        PROMPT_CONFIG_CACHE_KEY,
        _envelope(version, data),
        ex=PROMPT_SHARED_CACHE_TTL_SEC,
    )
    return True


def read_prompt_templates_from_oss() -> dict[str, Any] | None:
    storage = get_canvas_storage()
    data = storage.read_platform_json(PROMPT_TEMPLATES_OSS_REL)
    return data if isinstance(data, dict) else None


def read_prompt_config_from_oss() -> dict[str, Any] | None:
    storage = get_canvas_storage()
    data = storage.read_platform_json(PROMPT_CONFIG_OSS_REL)
    return data if isinstance(data, dict) else None


def write_prompt_templates_to_oss(data: dict[str, Any]) -> None:
    get_canvas_storage().write_platform_json(PROMPT_TEMPLATES_OSS_REL, data)
    logger.info("Wrote platform prompt templates to OSS")


def write_prompt_config_to_oss(data: dict[str, Any]) -> None:
    get_canvas_storage().write_platform_json(PROMPT_CONFIG_OSS_REL, data)
    logger.info("Wrote platform prompt config to OSS")


async def persist_prompt_templates_shared(data: dict[str, Any]) -> int:
    """写 OSS 并刷新 Redis 缓存版本。"""
    write_prompt_templates_to_oss(data)
    ver = await bump_cache_version("prompt_templates")
    await publish_prompt_templates_to_redis(data, ver=ver)
    return ver


async def persist_prompt_config_shared(data: dict[str, Any]) -> int:
    """写 OSS 并刷新 Redis 缓存版本。"""
    write_prompt_config_to_oss(data)
    ver = await bump_cache_version("prompt_config")
    await publish_prompt_config_to_redis(data, ver=ver)
    return ver


async def bump_prompt_templates_cache() -> int:
    return await bump_cache_version("prompt_templates")


async def bump_prompt_config_cache() -> int:
    return await bump_cache_version("prompt_config")
