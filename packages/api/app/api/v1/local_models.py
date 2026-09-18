"""登录用户配置自己的本地图片 / 视频模型。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.entity_ids import require_entity_id
from ...core.llm_keys import is_model_configured, is_provider_configured
from ...models.database import get_db
from ...models.job import Model
from ...models.user import User
from ...services.model_availability import is_catalog_model_implemented, provider_group_label
from ...services.user_local_models import (
    create_user_local_model,
    delete_user_local_model,
    list_user_local_models,
)

router = APIRouter()


class UserLocalModelIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    display_name: str = Field(..., alias="displayName")
    category: str
    backend: str = "comfyui"
    upstream_model: str = Field(..., alias="upstreamModel")
    comfy_base_url: Optional[str] = Field("", alias="comfyBaseUrl")
    local_api_base: Optional[str] = Field("", alias="localApiBase")
    comfy_folder: Optional[str] = Field("", alias="comfyFolder")
    comfy_workflow: Optional[Any] = Field(None, alias="comfyWorkflow")


def _out(model: Model) -> dict[str, Any]:
    params = model.parameters if isinstance(model.parameters, dict) else {}
    configured = is_model_configured(model.name) or is_provider_configured(
        model.provider, model_id=model.name
    )
    return {
        "id": str(model.id),
        "name": model.name,
        "displayName": model.display_name or model.name,
        "provider": model.provider,
        "category": model.category,
        "upstreamModel": params.get("upstreamModel") or "",
        "comfyBaseUrl": params.get("comfyBaseUrl") or "",
        "localApiBase": params.get("localApiBase") or "",
        "isAvailable": bool(model.is_available),
        "isConfigured": configured,
        "isImplemented": is_catalog_model_implemented(model),
        "providerGroup": provider_group_label(model.provider or ""),
    }


@router.get("/me/local-models")
async def list_my_local_models(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = await list_user_local_models(db, user)
    return {"items": [_out(m) for m in rows]}


@router.post("/me/local-models")
async def create_my_local_model(
    body: UserLocalModelIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    model = await create_user_local_model(
        db,
        user,
        display_name=body.display_name,
        category=body.category,
        backend=body.backend,
        upstream_model=body.upstream_model,
        comfy_base_url=body.comfy_base_url or "",
        local_api_base=body.local_api_base or "",
        comfy_folder=body.comfy_folder or "",
        comfy_workflow=body.comfy_workflow,
    )
    return _out(model)


@router.delete("/me/local-models/{model_id}")
async def delete_my_local_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await delete_user_local_model(db, user, require_entity_id(model_id, message="模型 ID 无效"))
    return {"ok": True}
