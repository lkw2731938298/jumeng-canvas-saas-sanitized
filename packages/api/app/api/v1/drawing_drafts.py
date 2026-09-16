"""图片节点画板草稿：矢量 JSON 存 OSS，按 projectId + nodeId 隔离。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.datetime_util import cst_iso_now
from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...integrations.oss.canvas_storage import get_canvas_storage
from ...models.database import get_db
from ...models.user import User
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder

router = APIRouter()


class DrawingDraftPayload(BaseModel):
    """画板文档 JSON（前端 DrawingDocument v2）"""
    document: dict[str, Any] = Field(default_factory=dict)
    expectedRevision: int | None = None


class DrawingDraftResponse(BaseModel):
    projectId: str
    nodeId: str
    document: dict[str, Any] = Field(default_factory=dict)
    revision: int = 0
    ossKey: str = ""
    updatedAt: str = ""


def _draft_rel(node_id: str) -> str:
    return f"drawings/{node_id}.json"


def _meta_rel(node_id: str) -> str:
    return f"drawings/{node_id}.meta.json"


def _empty_response(project_id: str, node_id: str) -> DrawingDraftResponse:
    return DrawingDraftResponse(
        projectId=project_id,
        nodeId=node_id,
        document={},
        revision=0,
        ossKey="",
        updatedAt="",
    )


@router.get("/projects/{project_id}/nodes/{node_id}", response_model=DrawingDraftResponse)
async def get_drawing_draft(
    project_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await require_project_access(db, current_user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    meta = await asyncio.to_thread(
        storage.read_json, project_id, _meta_rel(node_id), storage_folder=folder
    )
    if not isinstance(meta, dict):
        return _empty_response(project_id, node_id)

    oss_key = str(meta.get("ossKey") or "")
    if not oss_key:
        oss_key = storage.project_key(project_id, _draft_rel(node_id), storage_folder=folder)

    document = await asyncio.to_thread(
        storage.read_json, project_id, _draft_rel(node_id), storage_folder=folder
    )
    if not isinstance(document, dict):
        document = {}

    return DrawingDraftResponse(
        projectId=project_id,
        nodeId=node_id,
        document=document,
        revision=int(meta.get("revision") or 0),
        ossKey=oss_key,
        updatedAt=str(meta.get("updatedAt") or ""),
    )


@router.put("/projects/{project_id}/nodes/{node_id}", response_model=DrawingDraftResponse)
async def save_drawing_draft(
    project_id: str,
    node_id: str,
    payload: DrawingDraftPayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await require_project_access(db, user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()

    meta = await asyncio.to_thread(
        storage.read_json, project_id, _meta_rel(node_id), storage_folder=folder
    )
    current_revision = int(meta.get("revision") or 0) if isinstance(meta, dict) else 0

    # 未传 expectedRevision 时直接覆盖同 node 草稿文件（显式保存/取消保存并返回）
    if payload.expectedRevision is not None and payload.expectedRevision != current_revision:
        fail(ErrorCode.REVISION_CONFLICT)

    next_revision = current_revision + 1
    draft_obj = await asyncio.to_thread(
        storage.write_json,
        project_id,
        _draft_rel(node_id),
        payload.document,
        storage_folder=folder,
    )

    updated_at = cst_iso_now()
    record = DrawingDraftResponse(
        projectId=project_id,
        nodeId=node_id,
        document=payload.document,
        revision=next_revision,
        ossKey=draft_obj.oss_key,
        updatedAt=updated_at,
    )
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _meta_rel(node_id),
        {
            "projectId": project_id,
            "nodeId": node_id,
            "revision": next_revision,
            "ossKey": draft_obj.oss_key,
            "updatedAt": updated_at,
        },
        storage_folder=folder,
    )
    return record


@router.delete("/projects/{project_id}/nodes/{node_id}")
async def delete_drawing_draft(
    project_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await require_project_access(db, user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _meta_rel(node_id),
        {"projectId": project_id, "nodeId": node_id, "revision": 0, "ossKey": "", "updatedAt": ""},
        storage_folder=folder,
    )
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _draft_rel(node_id),
        {},
        storage_folder=folder,
    )
    return {"ok": True}
