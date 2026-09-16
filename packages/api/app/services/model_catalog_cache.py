"""模型目录读缓存：按 name / id + alias 走 Cache-Aside；admin 写后 bump 版本。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.job import Model
from ..common.utils.redis_cache import DEFAULT_MODEL_CACHE_TTL_SEC, bump_model_catalog_cache, cache_get_or_load
from ..common.utils.redis_keys import build_cache_key

# 对外 re-export，供 admin / sync 调用
invalidate_model_catalog_cache = bump_model_catalog_cache


def model_row_cache_key_by_name(name: str, *, category: str | None = None) -> str:
    """cache:model:name:{name}[:cat:{category}]:row"""
    if category:
        return build_cache_key("model", f"name:{name}:cat:{category}", "row")
    return build_cache_key("model", f"name:{name}", "row")


def model_row_cache_key_by_id(model_id: int | str) -> str:
    return build_cache_key("model", str(model_id), "row")


def model_list_cache_key() -> str:
    """可用模型全量列表（无筛选）。"""
    return build_cache_key("model", "list", "available")


def serialize_model_row(model: Model) -> dict:
    """ORM → 可 JSON 序列化的 dict（用于 Redis 缓存）。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    return {
        "id": int(model.id),
        "name": model.name,
        "display_name": model.display_name,
        "provider": model.provider,
        "model_type": model.model_type,
        "category": model.category,
        "description": model.description,
        "cover_url": model.cover_url,
        "parameters": params,
        "is_available": bool(model.is_available),
        "sort_order": int(model.sort_order or 0),
    }


def deserialize_model_row(data: dict) -> Model:
    """缓存 dict →  detached Model（只读提交/展示用）。"""
    return Model(
        id=data["id"],
        name=data["name"],
        display_name=data.get("display_name"),
        provider=data.get("provider") or "comfyui",
        model_type=data.get("model_type") or "checkpoint",
        category=data.get("category") or "image",
        description=data.get("description"),
        cover_url=data.get("cover_url"),
        parameters=data.get("parameters") if isinstance(data.get("parameters"), dict) else {},
        is_available=bool(data.get("is_available", True)),
        sort_order=int(data.get("sort_order") or 0),
    )


async def _load_model_row_from_db(
    db: AsyncSession,
    *,
    name: str | None = None,
    model_id: int | None = None,
    category: str | None = None,
    available_only: bool = False,
) -> dict | None:
    if name is None and model_id is None:
        return None
    query = select(Model)
    if model_id is not None:
        query = query.filter(Model.id == model_id)
    if name is not None:
        query = query.filter(Model.name == name)
    if category:
        query = query.filter(Model.category == category)
    if available_only:
        query = query.filter(Model.is_available.is_(True))
    result = await db.execute(query)
    model = result.scalar_one_or_none()
    if model is None:
        return None
    return serialize_model_row(model)


async def get_cached_model_by_name(
    db: AsyncSession,
    name: str,
    *,
    category: str | None = None,
    available_only: bool = False,
    ttl_sec: int = DEFAULT_MODEL_CACHE_TTL_SEC,
) -> Model | None:
    """按 name（+ 可选 category）读模型；先 Redis 后 DB。"""
    key = model_row_cache_key_by_name(name, category=category if category else None)
    suffix = ":avail" if available_only else ""
    cache_key = f"{key}{suffix}"

    async def loader() -> dict | None:
        return await _load_model_row_from_db(
            db,
            name=name,
            category=category,
            available_only=available_only,
        )

    data = await cache_get_or_load(
        cache_key,
        ttl=ttl_sec,
        loader=loader,
        version_domain="model",
    )
    return deserialize_model_row(data) if data else None


async def get_cached_model_by_id(
    db: AsyncSession,
    model_id: int,
    *,
    ttl_sec: int = DEFAULT_MODEL_CACHE_TTL_SEC,
) -> Model | None:
    """按 DB id 读模型；先 Redis 后 DB。"""
    cache_key = model_row_cache_key_by_id(model_id)

    async def loader() -> dict | None:
        return await _load_model_row_from_db(db, model_id=model_id)

    data = await cache_get_or_load(
        cache_key,
        ttl=ttl_sec,
        loader=loader,
        version_domain="model",
    )
    return deserialize_model_row(data) if data else None


async def get_cached_available_models_list(
    db: AsyncSession,
    *,
    ttl_sec: int = DEFAULT_MODEL_CACHE_TTL_SEC,
) -> list[Model]:
    """读全部 is_available 模型列表（用于无筛选 list_models）。"""

    async def loader() -> list[dict]:
        result = await db.execute(
            select(Model)
            .filter(Model.is_available.is_(True))
            .order_by(Model.sort_order.asc(), Model.display_name.asc())
        )
        return [serialize_model_row(m) for m in result.scalars().all()]

    data = await cache_get_or_load(
        model_list_cache_key(),
        ttl=ttl_sec,
        loader=loader,
        version_domain="model",
        cache_null=True,
    )
    if not isinstance(data, list):
        return []
    return [deserialize_model_row(row) for row in data if isinstance(row, dict)]


async def get_cached_is_model_configured(
    db: AsyncSession,
    model_name: str,
    *,
    provider: str | None = None,
    ttl_sec: int = 60,
) -> bool:
    """模型是否已配置密钥（credential 域版本缓存 + Redis 密钥快照）。"""
    from .credential_service import resolve_model_configured_status

    async def loader() -> bool:
        return await resolve_model_configured_status(db, model_name, provider=provider)

    cache_key = build_cache_key("model", f"name:{model_name}", "configured")
    if provider:
        cache_key = f"{cache_key}:p:{provider}"

    data = await cache_get_or_load(
        cache_key,
        ttl=ttl_sec,
        loader=loader,
        version_domain="credential",
        cache_null=True,
    )
    return bool(data)
