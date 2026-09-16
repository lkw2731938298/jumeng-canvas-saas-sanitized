"""P3：运行时模型目录从 MySQL 加载；registry 仅作 sync 种子。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.model_registry import (
    CANVAS_MODEL_BY_NAME,
    CanvasModelSpec,
    Capability,
    VideoMode,
)
from ..models.job import Model
from .model_catalog_cache import invalidate_model_catalog_cache

logger = logging.getLogger(__name__)

_runtime_specs: dict[str, CanvasModelSpec] = {}
_runtime_catalog_ver: int = -1


def catalog_source() -> str:
    """db | registry | dual（小写）。"""
    return (get_settings().model_catalog_source or "db").strip().lower()


def uses_db_catalog_runtime() -> bool:
    return catalog_source() in ("db", "dual")


def model_spec_from_catalog(model: Model) -> CanvasModelSpec:
    """将 ORM Model 行转为运行时 CanvasModelSpec。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    raw_caps = params.get("capabilities") or []
    capabilities: tuple[Capability, ...] = tuple(
        c for c in raw_caps if isinstance(c, str) and c
    )  # type: ignore[return-value]

    video_mode_raw = params.get("videoMode")
    video_mode: VideoMode | None = None
    if isinstance(video_mode_raw, str) and video_mode_raw in ("r2v", "i2v", "lip_sync", "t2v"):
        video_mode = video_mode_raw  # type: ignore[assignment]

    impl_raw = str(params.get("implementation") or "reserved").lower()
    implementation: str = impl_raw if impl_raw in ("live", "reserved") else "reserved"

    meta_keys = {
        "upstreamModel",
        "capabilities",
        "implementation",
        "providerGroup",
        "videoMode",
        "videoMinDuration",
        "videoMaxDuration",
        "generationPresets",
        "pricing",
        "compatible_node_types",
        "adminSoftDeleted",
        "adminSoftDeletedAt",
        "channels",  # 主/副上游通道配置，不进入 parameters_extra
    }
    parameters_extra: dict[str, Any] = {
        k: v for k, v in params.items() if k not in meta_keys
    }

    provider = (model.provider or "comfyui").strip().lower()
    category = (model.category or "image").strip().lower()

    return CanvasModelSpec(
        name=model.name,
        display_name=model.display_name or model.name,
        provider=provider,  # type: ignore[arg-type]
        model_type=model.model_type or "checkpoint",
        category=category,  # type: ignore[arg-type]
        description=model.description or "",
        sort_order=int(model.sort_order or 0),
        upstream_model=str(params.get("upstreamModel") or ""),
        capabilities=capabilities,
        video_mode=video_mode,
        parameters_extra=parameters_extra,
        implementation=implementation,  # type: ignore[arg-type]
    )


def resolve_model_spec(model_id: str) -> CanvasModelSpec | None:
    """按 MODEL_CATALOG_SOURCE 解析模型规格。"""
    source = catalog_source()
    if source in ("db", "dual"):
        hit = _runtime_specs.get(model_id)
        if hit is not None:
            return hit
        if source == "db":
            return None
    if source == "registry":
        return CANVAS_MODEL_BY_NAME.get(model_id)
    return CANVAS_MODEL_BY_NAME.get(model_id)


def iter_runtime_model_names() -> list[str]:
    """当前进程内已加载的 DB 模型 name 列表。"""
    return list(_runtime_specs.keys())


def clear_runtime_model_specs() -> None:
    global _runtime_specs, _runtime_catalog_ver
    _runtime_specs = {}
    _runtime_catalog_ver = -1


async def _load_model_specs_from_db(db: AsyncSession) -> dict[str, CanvasModelSpec]:
    """从 MySQL 全量加载模型规格（权威源）。"""
    result = await db.execute(select(Model))
    models = list(result.scalars().all())
    loaded: dict[str, CanvasModelSpec] = {}
    for row in models:
        params = row.parameters if isinstance(row.parameters, dict) else {}
        if params.get("adminSoftDeleted"):
            continue
        try:
            loaded[row.name] = model_spec_from_catalog(row)
        except Exception as exc:
            logger.warning("Skip invalid model row %s: %s", row.name, exc)
    return loaded


def _apply_runtime_specs(loaded: dict[str, CanvasModelSpec], *, ver: int) -> None:
    global _runtime_specs, _runtime_catalog_ver
    _runtime_specs = loaded
    _runtime_catalog_ver = ver


async def warm_runtime_model_specs(db: AsyncSession) -> int:
    """从 DB 加载并发布 Redis 共享缓存，再写入本进程 L1 快照。"""
    global _runtime_catalog_ver
    from ..common.utils.redis_cache import get_cache_version
    from .runtime_shared_cache import load_model_catalog_from_redis, publish_model_catalog_to_redis

    if not uses_db_catalog_runtime():
        clear_runtime_model_specs()
        return 0

    ver = await get_cache_version("model")
    loaded = await _load_model_specs_from_db(db)
    await publish_model_catalog_to_redis(loaded, ver=ver)
    _apply_runtime_specs(loaded, ver=ver)
    logger.info("Runtime model catalog loaded: %s model(s) from DB → Redis + L1", len(_runtime_specs))
    return len(_runtime_specs)


async def _refresh_runtime_specs_from_redis() -> bool:
    """版本变化时优先从 Redis 拉取全量规格到本进程 L1。"""
    from ..common.utils.redis_cache import get_cache_version
    from .runtime_shared_cache import load_model_catalog_from_redis

    ver = await get_cache_version("model")
    loaded = await load_model_catalog_from_redis()
    if loaded is None:
        return False
    _apply_runtime_specs(loaded, ver=ver)
    logger.info("Runtime model catalog refreshed from Redis: %s model(s), ver=%s", len(loaded), ver)
    return True


async def refresh_runtime_model_specs_if_version_changed(db: AsyncSession) -> bool:
    """model 域版本变化时刷新规格快照（优先 Redis，miss 时回源 DB 并再发布）。"""
    if not uses_db_catalog_runtime():
        return False
    from ..common.utils.redis_cache import get_cache_version

    ver = await get_cache_version("model")
    if _runtime_specs and ver == _runtime_catalog_ver:
        return False
    if await _refresh_runtime_specs_from_redis():
        return True
    await warm_runtime_model_specs(db)
    return True


async def reload_runtime_model_catalog(db: AsyncSession) -> None:
    """admin 写模型后：失效缓存并刷新运行时规格。

    缓存刷新失败不阻断 DB 写（启停/编辑已 flush）；仅打日志，避免 Redis
    异常导致「更新模型状态失败」整单回滚。
    """
    try:
        await invalidate_model_catalog_cache()
        await warm_runtime_model_specs(db)
    except Exception:  # noqa: BLE001
        logger.exception("reload_runtime_model_catalog failed; DB write kept")
