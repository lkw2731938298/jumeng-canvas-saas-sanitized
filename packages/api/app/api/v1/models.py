from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional

import asyncio

from ...core.deps import get_current_user_optional
from ...models.user import User
from ...services.model_availability import is_catalog_model_implemented, provider_group_label
from ...models.database import get_db
from ...models.job import Model
from ...services.model_catalog_cache import get_cached_available_models_list, get_cached_is_model_configured
from ...services.credit_pricing import extract_pricing_config
from ...services.generation_presets import public_presets
from ...services.model_ui_tags import read_model_ui_tag_ids
from ...services.storage_urls import normalize_browser_storage_url
from ...services.user_local_models import is_visible_local_model

router = APIRouter()

def _public_parameters(params: dict | None, *, category: str | None = None) -> dict | None:
    if not params or not isinstance(params, dict):
        return params
    out = dict(params)
    presets = params.get("generationPresets")
    if presets:
        out["generationPresets"] = public_presets(presets)
    pricing = extract_pricing_config(params, category=category)
    out["pricing"] = {
        "version": pricing.get("version"),
        "baseCost": pricing.get("baseCost"),
        "mode": pricing.get("mode"),
        "options": pricing.get("options") or {},
    }
    return out

@router.get("")
async def list_models(
    category: Optional[str] = Query(None),
    model_type: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """List available models with optional filtering."""
    from ...services.runtime_catalog_refresh import ensure_runtime_catalog_fresh

    await ensure_runtime_catalog_fresh(db)

    if not any((category, model_type, provider, search)):
        models = await get_cached_available_models_list(db)
    else:
        q = select(Model).filter(Model.is_available == True)
        if category:
            q = q.filter(Model.category == category)
        if model_type:
            q = q.filter(Model.model_type == model_type)
        if provider:
            q = q.filter(Model.provider == provider)
        if search:
            q = q.filter(
                (Model.name.ilike(f"%{search}%"))
                | (Model.display_name.ilike(f"%{search}%"))
            )
        q = q.order_by(Model.sort_order.asc(), Model.display_name.asc())
        result = await db.execute(q)
        models = list(result.scalars().all())

    uid = str(user.id) if user is not None else None
    models = [
        m
        for m in models
        if not (isinstance(m.parameters, dict) and m.parameters.get("adminSoftDeleted"))
        and is_visible_local_model(m, uid)
    ]

    configured_flags = await asyncio.gather(
        *(get_cached_is_model_configured(db, m.name, provider=m.provider) for m in models)
    )

    return [
        {
            "id": str(m.id),
            "name": m.name,
            "display_name": m.display_name or m.name,
            "provider": m.provider,
            "model_type": m.model_type,
            "category": m.category,
            "description": m.description,
            "cover_url": normalize_browser_storage_url(m.cover_url) if m.cover_url else m.cover_url,
            "parameters": _public_parameters(
                m.parameters if isinstance(m.parameters, dict) else None,
                category=m.category,
            ),
            "is_available": m.is_available,
            "is_configured": configured,
            "is_implemented": is_catalog_model_implemented(m),
            "provider_group": provider_group_label(m.provider or ""),
            "ui_tag_ids": read_model_ui_tag_ids(
                m.parameters if isinstance(m.parameters, dict) else None
            ),
        }
        for m, configured in zip(models, configured_flags, strict=True)
    ]


@router.get("/ui-tags")
async def list_model_ui_tags(
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """画布模型选择 UI 标签（仅启用项；可按类目过滤）。"""
    from ...services.model_ui_tags import get_model_ui_tags, public_model_ui_tags

    cfg = await get_model_ui_tags(db)
    return {
        "version": cfg.get("version", 1),
        "tags": public_model_ui_tags(cfg, category=category),
    }


@router.get("/ui-series")
async def list_model_ui_series(
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """画布选模左侧系列展示顺序（仅启用项；可按类目过滤）。"""
    from ...services.model_ui_series import get_model_ui_series, public_model_ui_series

    cfg = await get_model_ui_series(db)
    return {
        "version": cfg.get("version", 1),
        "series": public_model_ui_series(cfg, category=category),
    }


@router.get("/categories")
async def list_categories(db: AsyncSession = Depends(get_db)):
    """List distinct model categories with counts."""
    result = await db.execute(
        select(Model.category, func.count(Model.id))
        .filter(Model.is_available == True)
        .group_by(Model.category)
        .order_by(Model.category)
    )
    return [{"category": row[0], "count": row[1]} for row in result.all()]
