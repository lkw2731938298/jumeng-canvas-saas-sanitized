"""登录用户把自己的本地图/视频模型挂到画布（ComfyUI 文件或 OpenAI 兼容接口）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.comfyui.discover import slug_catalog_name
from ..models.job import Model
from ..models.user import User
from .admin_models import create_admin_model
from .model_catalog_runtime import reload_runtime_model_catalog


def owner_id_of(params: dict[str, Any] | None) -> str:
    if not isinstance(params, dict):
        return ""
    return str(params.get("localOwnerUserId") or "").strip()


def is_visible_local_model(model: Model, user_id: str | None) -> bool:
    """实例级（无主人）所有人可见；用户自建仅主人可见。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    owner = owner_id_of(params)
    if not owner:
        return True
    return bool(user_id) and owner == str(user_id)


async def list_user_local_models(db: AsyncSession, user: User) -> list[Model]:
    result = await db.execute(select(Model).order_by(Model.sort_order.asc(), Model.display_name.asc()))
    uid = str(user.id)
    out: list[Model] = []
    for row in result.scalars().all():
        params = row.parameters if isinstance(row.parameters, dict) else {}
        if params.get("adminSoftDeleted"):
            continue
        if owner_id_of(params) != uid:
            continue
        out.append(row)
    return out


async def create_user_local_model(
    db: AsyncSession,
    user: User,
    *,
    display_name: str,
    category: str,
    backend: str,
    upstream_model: str,
    comfy_base_url: str = "",
    local_api_base: str = "",
    comfy_folder: str = "",
    comfy_workflow: Any = None,
) -> Model:
    """用户自建：backend=comfyui | openai_compat。"""
    display = (display_name or "").strip()
    if not display:
        fail(ErrorCode.VALIDATION_ERROR, message="请填写模型显示名称")
    cat = (category or "").strip().lower()
    if cat not in ("image", "video"):
        fail(ErrorCode.VALIDATION_ERROR, message="分类只能是图片或视频")
    kind = (backend or "").strip().lower()
    if kind in ("local", "openai", "openai_compat", "sglang", "ollama", "vllm"):
        kind = "openai_compat"
    if kind not in ("comfyui", "openai_compat"):
        fail(ErrorCode.VALIDATION_ERROR, message="接入方式请选择 ComfyUI 或 OpenAI 兼容接口")
    upstream = (upstream_model or "").strip()
    if not upstream:
        fail(
            ErrorCode.VALIDATION_ERROR,
            message="请填写本机模型标识（ComfyUI 文件名，或 Ollama/vLLM/SGLang 的 model id）",
        )

    prefix = f"uloc_{str(user.id).replace('-', '')[:8]}"
    name = slug_catalog_name(prefix, f"{cat}_{upstream}")
    existing = await db.execute(select(Model).filter(Model.name == name))
    if existing.scalar_one_or_none():
        name = slug_catalog_name(prefix, f"{cat}_{upstream}_{str(user.id)[-4:]}")

    if kind == "comfyui":
        provider = "comfyui"
        caps = (
            ["text_to_video", "first_frame_to_video", "reference_to_video"]
            if cat == "video"
            else ["text_to_image", "image_to_image"]
        )
        extra: dict[str, Any] = {
            "comfyFile": upstream,
            "comfyFolder": (comfy_folder or "checkpoints").strip() or "checkpoints",
            "comfyBaseUrl": (comfy_base_url or "").strip(),
            "source": "user-local",
            "localOwnerUserId": str(user.id),
        }
        if comfy_workflow:
            extra["comfyWorkflow"] = comfy_workflow
    else:
        provider = "local"
        caps = (
            ["text_to_video", "first_frame_to_video"]
            if cat == "video"
            else ["text_to_image"]
        )
        extra = {
            "localApiBase": (local_api_base or "").strip(),
            "source": "user-local",
            "localOwnerUserId": str(user.id),
        }

    return await create_admin_model(
        db,
        name=name,
        display_name=display,
        provider=provider,
        model_type="local",
        category=cat,
        description="用户自建本地模型",
        sort_order=50,
        is_available=True,
        parameters=extra,
        upstream_model=upstream,
        capabilities=caps,
        implementation="live",
    )


async def delete_user_local_model(db: AsyncSession, user: User, model_id: int) -> None:
    result = await db.execute(select(Model).filter(Model.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)
    params = model.parameters if isinstance(model.parameters, dict) else {}
    if owner_id_of(params) != str(user.id):
        fail(ErrorCode.FORBIDDEN, message="只能删除自己添加的本地模型")
    params = dict(params)
    params["adminSoftDeleted"] = True
    params["is_available_before_soft_delete"] = bool(model.is_available)
    model.parameters = params
    flag_modified(model, "parameters")
    model.is_available = False
    await db.flush()
    await reload_runtime_model_catalog(db)
