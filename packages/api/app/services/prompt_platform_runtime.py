"""平台 Prompt 运行时 L1 快照：Worker/API 分机从 OSS+Redis 对齐。"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from .prompt_platform_storage import (
    persist_prompt_config_shared,
    persist_prompt_templates_shared,
    read_prompt_config_from_oss,
    read_prompt_config_from_redis,
    read_prompt_templates_from_oss,
    read_prompt_templates_from_redis,
)

logger = logging.getLogger(__name__)

_runtime_templates: dict[str, Any] | None = None
_runtime_config: dict[str, Any] | None = None
_runtime_templates_ver: int = -1
_runtime_config_ver: int = -1


def get_l1_prompt_templates() -> dict[str, Any] | None:
    return _runtime_templates


def get_l1_prompt_config() -> dict[str, Any] | None:
    return _runtime_config


def apply_l1_prompt_templates(data: dict[str, Any], *, ver: int) -> None:
    global _runtime_templates, _runtime_templates_ver
    _runtime_templates = deepcopy(data)
    _runtime_templates_ver = ver


def apply_l1_prompt_config(data: dict[str, Any], *, ver: int) -> None:
    global _runtime_config, _runtime_config_ver
    _runtime_config = deepcopy(data)
    _runtime_config_ver = ver


def clear_l1_prompt_platform() -> None:
    global _runtime_templates, _runtime_config, _runtime_templates_ver, _runtime_config_ver
    _runtime_templates = None
    _runtime_config = None
    _runtime_templates_ver = -1
    _runtime_config_ver = -1


async def _refresh_templates_from_redis() -> bool:
    from ..common.utils.redis_cache import get_cache_version

    data = await read_prompt_templates_from_redis()
    if data is None:
        return False
    ver = await get_cache_version("prompt_templates")
    apply_l1_prompt_templates(data, ver=ver)
    return True


async def _refresh_config_from_redis() -> bool:
    from ..common.utils.redis_cache import get_cache_version
    from .prompt_config import _ensure_bundled_tools

    data = await read_prompt_config_from_redis()
    if data is None:
        return False
    merged, changed = _ensure_bundled_tools(data)
    ver = await get_cache_version("prompt_config")
    # 旧快照缺新工具时回写 Redis/OSS，避免各进程长期 404
    if changed:
        ver = await persist_prompt_config_shared(merged)
    apply_l1_prompt_config(merged, ver=ver)
    return True


async def warm_prompt_templates(*, seed_from_local: Any | None = None) -> dict[str, Any]:
    """加载模板：Redis → OSS → 本地种子 → 发布共享缓存。"""
    from ..common.utils.redis_cache import get_cache_version

    if await _refresh_templates_from_redis():
        return deepcopy(_runtime_templates or {})

    data = read_prompt_templates_from_oss()
    if data is None and seed_from_local is not None:
        data = deepcopy(seed_from_local)
    if data is None:
        from .prompt_templates import _load_store_from_files

        data = _load_store_from_files()

    ver = await persist_prompt_templates_shared(data)
    apply_l1_prompt_templates(data, ver=ver)
    logger.info("Warm prompt templates: %s item(s), ver=%s", len(data.get("templates") or []), ver)
    return deepcopy(data)


async def warm_prompt_config(*, seed_from_local: Any | None = None) -> dict[str, Any]:
    """加载配置：Redis → OSS → 本地种子 → 发布共享缓存。

    若快照缺少内置默认中的新工具，由 _refresh / 下方合并补齐并回写共享缓存。
    """
    from .prompt_config import _ensure_bundled_tools, _load_config_from_files

    if await _refresh_config_from_redis():
        return deepcopy(_runtime_config or {})

    data = read_prompt_config_from_oss()
    if data is None and seed_from_local is not None:
        data = deepcopy(seed_from_local)
    if data is None:
        data = _load_config_from_files()

    merged, changed = _ensure_bundled_tools(data or {"version": 2, "tools": {}})
    ver = await persist_prompt_config_shared(merged)
    apply_l1_prompt_config(merged, ver=ver)
    logger.info(
        "Warm prompt config: %s tool(s), ver=%s, bundled_fill=%s",
        len((merged.get("tools") or {}).keys()),
        ver,
        changed,
    )
    return deepcopy(merged)


async def warm_prompt_platform_caches() -> None:
    """API/Worker 启动时预热平台 Prompt 缓存。"""
    await warm_prompt_templates()
    await warm_prompt_config()


async def refresh_prompt_templates_if_version_changed() -> bool:
    from ..common.utils.redis_cache import get_cache_version

    ver = await get_cache_version("prompt_templates")
    if _runtime_templates is not None and ver == _runtime_templates_ver:
        return False
    if await _refresh_templates_from_redis():
        return True
    await warm_prompt_templates()
    return True


async def refresh_prompt_config_if_version_changed() -> bool:
    from ..common.utils.redis_cache import get_cache_version

    ver = await get_cache_version("prompt_config")
    if _runtime_config is not None and ver == _runtime_config_ver:
        return False
    if await _refresh_config_from_redis():
        return True
    await warm_prompt_config()
    return True


async def ensure_prompt_platform_fresh() -> None:
    """版本变化时从 Redis/OSS 刷新本进程 Prompt L1。"""
    await refresh_prompt_templates_if_version_changed()
    await refresh_prompt_config_if_version_changed()
