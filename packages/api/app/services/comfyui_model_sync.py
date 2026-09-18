"""把发现到的 ComfyUI 权重写入画布模型目录（幂等，不覆盖用户已改字段）。"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.comfyui.discover import discover_comfyui_models, slug_catalog_name
from ..models.job import Model
from .admin_models import create_admin_model
from .model_catalog_runtime import reload_runtime_model_catalog

logger = logging.getLogger(__name__)


def _file_key(folder: str, filename: str) -> str:
    return f"{(folder or '').strip()}::{(filename or '').strip()}"


def _is_same_comfy_source(params: dict[str, Any] | None, folder: str, filename: str) -> bool:
    if not isinstance(params, dict):
        return False
    return (
        str(params.get("comfyFile") or "").strip() == (filename or "").strip()
        and str(params.get("comfyFolder") or "").strip() == (folder or "").strip()
    )


async def preview_comfyui_models(base_url: str | None = None) -> dict[str, Any]:
    """只探测、不写库。"""
    url = (base_url or "").strip() or get_settings().comfyui_base_url.strip()
    if not url:
        fail(ErrorCode.VALIDATION_ERROR, message="请填写 ComfyUI 地址，例如 http://127.0.0.1:8188")
    try:
        items = await discover_comfyui_models(url)
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    except Exception as exc:
        fail(
            ErrorCode.UPSTREAM_ERROR,
            message=f"无法连接 ComfyUI（{url}）：{exc}",
        )
    return {"baseUrl": url, "count": len(items), "items": items}


async def _existing_comfy_index(db: AsyncSession) -> dict[str, Model]:
    result = await db.execute(select(Model).filter(Model.provider == "comfyui"))
    index: dict[str, Model] = {}
    for row in result.scalars().all():
        params = row.parameters if isinstance(row.parameters, dict) else {}
        if params.get("adminSoftDeleted"):
            continue
        fn = str(params.get("comfyFile") or "").strip()
        folder = str(params.get("comfyFolder") or "").strip()
        if fn:
            index[_file_key(folder, fn)] = row
    return index


def _unique_name(filename: str, used: set[str]) -> str:
    base = slug_catalog_name("comfy", filename)
    if base not in used:
        used.add(base)
        return base
    digest = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:6]
    name = slug_catalog_name("comfy", f"{filename}_{digest}")
    n = 0
    while name in used:
        n += 1
        name = slug_catalog_name("comfy", f"{filename}_{digest}{n}")
    used.add(name)
    return name


async def sync_comfyui_models_to_catalog(
    db: AsyncSession,
    *,
    base_url: str | None = None,
    items: list[dict[str, Any]] | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    """导入选中（或全部探测到）的本机模型。已存在同文件则跳过。"""
    url = (base_url or "").strip() or get_settings().comfyui_base_url.strip()
    if not url:
        fail(ErrorCode.VALIDATION_ERROR, message="请填写 ComfyUI 地址")

    if items is None:
        preview = await preview_comfyui_models(url)
        items = list(preview.get("items") or [])
    if not items:
        return {"baseUrl": url, "created": 0, "skipped": 0, "createdNames": [], "skippedFiles": []}

    existing = await _existing_comfy_index(db)
    all_names = {row.name for row in (await db.execute(select(Model.name))).scalars().all()}
    created = 0
    skipped = 0
    created_names: list[str] = []
    skipped_files: list[str] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue
        filename = str(raw.get("filename") or raw.get("comfyFile") or "").strip()
        if not filename:
            continue
        folder = str(raw.get("folder") or raw.get("comfyFolder") or "checkpoints").strip() or "checkpoints"
        category = str(raw.get("category") or "image").strip().lower()
        if category not in ("image", "video"):
            category = "image"
        display = str(raw.get("displayName") or filename).strip() or filename
        key = _file_key(folder, filename)
        if key in existing:
            skipped += 1
            skipped_files.append(filename)
            continue

        name = _unique_name(filename, all_names)
        caps = (
            ["text_to_video", "first_frame_to_video", "reference_to_video"]
            if category == "video"
            else ["text_to_image", "image_to_image"]
        )
        extra: dict[str, Any] = {
            "comfyFile": filename,
            "comfyFolder": folder,
            "comfyBaseUrl": url,
            "source": "comfyui-sync",
        }
        if owner_user_id:
            extra["localOwnerUserId"] = str(owner_user_id)
        workflow = raw.get("comfyWorkflow") or raw.get("workflow")
        if workflow:
            extra["comfyWorkflow"] = workflow

        await create_admin_model(
            db,
            name=name,
            display_name=display,
            provider="comfyui",
            model_type="checkpoint" if folder == "checkpoints" else "diffusion",
            category=category,
            description=f"来自 ComfyUI {folder}/{filename}",
            sort_order=100,
            is_available=True,
            parameters=extra,
            upstream_model=filename,
            capabilities=caps,
            implementation="live",
        )
        created += 1
        created_names.append(name)

    await reload_runtime_model_catalog(db)
    logger.info("ComfyUI sync url=%s created=%s skipped=%s", url, created, skipped)
    return {
        "baseUrl": url,
        "created": created,
        "skipped": skipped,
        "createdNames": created_names,
        "skippedFiles": skipped_files,
    }
