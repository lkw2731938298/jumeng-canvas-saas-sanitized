from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from ....core.entity_ids import require_entity_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_MODELS
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....core.llm_keys import is_model_configured, is_provider_configured
from ....models.database import get_db
from ....models.job import Model
from ....models.user import User
from ....schemas.admin import AdminModelCreateIn, AdminModelOut, AdminModelPatchIn, AdminModelTestOut
from ....services.admin_models import (
    create_admin_model,
    patch_admin_model_fields,
    soft_delete_admin_model,
    evaluate_model_connectivity,
)
from ....services.generation_presets import default_parameters_for_model, public_presets
from ....services.model_availability import is_catalog_model_implemented, provider_group_label
from ....services.model_catalog_runtime import reload_runtime_model_catalog

router = APIRouter()


class ComfyuiSyncIn(BaseModel):
    model_config = {"populate_by_name": True}

    base_url: Optional[str] = Field(None, alias="baseUrl")
    items: Optional[list[dict[str, Any]]] = None


def _model_out(model: Model) -> AdminModelOut:
    params = model.parameters if isinstance(model.parameters, dict) else {}
    configured = is_model_configured(model.name) or is_provider_configured(
        model.provider, model_id=model.name
    )
    return AdminModelOut(
        id=str(model.id),
        name=model.name,
        display_name=model.display_name or model.name,
        provider=model.provider,
        model_type=model.model_type,
        category=model.category,
        description=model.description,
        is_available=bool(model.is_available),
        sort_order=model.sort_order or 0,
        parameters=params,
        created_at=model.created_at,
        is_configured=configured,
        is_implemented=is_catalog_model_implemented(model),
        provider_group=provider_group_label(model.provider or ""),
    )


@router.get("/models", response_model=list[AdminModelOut])
async def list_admin_models(
    category: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    include_deleted: bool = Query(False, alias="includeDeleted"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    query = select(Model)

    if category:
        query = query.filter(Model.category == category)
    if provider:
        query = query.filter(Model.provider == provider)
    if search:
        query = query.filter(
            (Model.name.ilike(f"%{search}%")) | (Model.display_name.ilike(f"%{search}%"))
        )

    query = query.order_by(Model.category.asc(), Model.sort_order.asc(), Model.display_name.asc())
    result = await db.execute(query)
    models = list(result.scalars().all())
    if not include_deleted:
        models = [
            m
            for m in models
            if not (isinstance(m.parameters, dict) and m.parameters.get("adminSoftDeleted"))
        ]
    return [_model_out(model) for model in models]


@router.get("/models/ui-tags")
async def get_admin_model_ui_tags(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """管理端：读取全局模型 UI 标签库。"""
    from ....services.model_ui_tags import get_model_ui_tags

    cfg = await get_model_ui_tags(db)
    return {
        "version": cfg.get("version", 1),
        "tags": [
            {
                "id": t["id"],
                "label": t["label"],
                "sort_order": t.get("sortOrder", 0),
                "enabled": bool(t.get("enabled", True)),
                "categories": list(t.get("categories") or []),
            }
            for t in cfg.get("tags") or []
        ],
    }


@router.put("/models/ui-tags")
async def put_admin_model_ui_tags(
    body: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """管理端：覆盖写入全局模型 UI 标签库。"""
    from ....services.model_ui_tags import set_model_ui_tags

    cfg = await set_model_ui_tags(db, body if isinstance(body, dict) else {}, bump_version=True)
    return {
        "version": cfg.get("version", 1),
        "tags": [
            {
                "id": t["id"],
                "label": t["label"],
                "sort_order": t.get("sortOrder", 0),
                "enabled": bool(t.get("enabled", True)),
                "categories": list(t.get("categories") or []),
            }
            for t in cfg.get("tags") or []
        ],
    }


@router.get("/models/ui-series")
async def get_admin_model_ui_series(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """管理端：读取画布选模左侧系列展示顺序。"""
    from ....services.model_ui_series import get_model_ui_series

    cfg = await get_model_ui_series(db)
    return {
        "version": cfg.get("version", 1),
        "series": [
            {
                "label": s["label"],
                "sort_order": s.get("sortOrder", 0),
                "enabled": bool(s.get("enabled", True)),
                "categories": list(s.get("categories") or []),
            }
            for s in cfg.get("series") or []
        ],
    }


@router.put("/models/ui-series")
async def put_admin_model_ui_series(
    body: dict,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """管理端：覆盖写入画布选模左侧系列展示顺序。"""
    from ....services.model_ui_series import set_model_ui_series

    cfg = await set_model_ui_series(db, body if isinstance(body, dict) else {}, bump_version=True)
    return {
        "version": cfg.get("version", 1),
        "series": [
            {
                "label": s["label"],
                "sort_order": s.get("sortOrder", 0),
                "enabled": bool(s.get("enabled", True)),
                "categories": list(s.get("categories") or []),
            }
            for s in cfg.get("series") or []
        ],
    }


@router.post("/models", response_model=AdminModelOut)
async def create_admin_model_route(
    body: AdminModelCreateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """新建模型（name 创建后不可修改）。"""
    model = await create_admin_model(
        db,
        name=body.name,
        display_name=body.display_name,
        provider=body.provider,
        model_type=body.model_type,
        category=body.category,
        description=body.description,
        sort_order=body.sort_order,
        is_available=body.is_available,
        cover_url=body.cover_url,
        parameters=body.parameters,
        upstream_model=body.upstream_model,
        capabilities=body.capabilities,
        implementation=body.implementation,
        channels=body.channels,
    )
    return _model_out(model)


@router.get("/models/comfyui/discover")
async def discover_comfyui_local_models(
    base_url: Optional[str] = Query(None, alias="baseUrl"),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """探测用户自己的 ComfyUI 本机模型（不写库）。"""
    from ....services.comfyui_model_sync import preview_comfyui_models

    return await preview_comfyui_models(base_url)


@router.post("/models/comfyui/sync")
async def sync_comfyui_local_models(
    body: ComfyuiSyncIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """一键把探测到的（或勾选的）ComfyUI 权重写入画布模型目录。"""
    from ....services.comfyui_model_sync import sync_comfyui_models_to_catalog

    return await sync_comfyui_models_to_catalog(
        db,
        base_url=body.base_url,
        items=body.items,
    )


@router.get("/models/{model_id}", response_model=AdminModelOut)
async def get_admin_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)
    return _model_out(model)


@router.patch("/models/{model_id}", response_model=AdminModelOut)
async def patch_admin_model(
    model_id: str,
    body: AdminModelPatchIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)

    model = await patch_admin_model_fields(
        db,
        model,
        is_available=body.is_available,
        sort_order=body.sort_order,
        display_name=body.display_name,
        provider=body.provider,
        model_type=body.model_type,
        category=body.category,
        description=body.description,
        cover_url=body.cover_url,
        parameters=body.parameters,
        generation_presets=body.generation_presets,
        upstream_model=body.upstream_model,
        capabilities=body.capabilities,
        implementation=body.implementation,
        channels=body.channels,
    )
    return _model_out(model)


@router.delete("/models/{model_id}", response_model=AdminModelOut)
async def delete_admin_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """软删：停用模型，保留历史任务引用。"""
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)

    model = await soft_delete_admin_model(db, model)
    return _model_out(model)


@router.post("/models/{model_id}/test", response_model=AdminModelTestOut)
async def test_admin_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    """连通性测试：校验密钥可读与接入状态。"""
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)

    outcome = evaluate_model_connectivity(model)
    return AdminModelTestOut(
        ok=outcome["ok"],
        message=outcome["message"],
        isConfigured=outcome["is_configured"],
        isImplemented=outcome["is_implemented"],
    )


@router.post("/models/{model_id}/presets/reset", response_model=AdminModelOut)
async def reset_model_presets(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MODELS)),
):
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)

    defaults = default_parameters_for_model(model.name, model.provider, model.category)
    presets = defaults.get("generationPresets") if defaults else None
    if not presets:
        fail(ErrorCode.NO_DEFAULT_PRESETS)

    params = dict(model.parameters or {})
    params["generationPresets"] = public_presets(presets) or presets
    model.parameters = params
    await db.flush()
    await reload_runtime_model_catalog(db)
    return _model_out(model)
