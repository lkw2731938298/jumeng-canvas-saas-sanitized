from ...core.datetime_util import cst_iso_now, now_cst_naive
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...integrations.oss.canvas_storage import get_canvas_storage
from ...models.database import get_db
from ...models.user import User
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder
from ...services.storage_urls import public_url_for_key

router = APIRouter()


class NodeTextPayload(BaseModel):
    content: str = ""
    model: str = "deepseek_v4_flash"


class NodeTextResponse(BaseModel):
    projectId: str
    nodeId: str
    content: str
    model: str
    ossKey: str
    fileUrl: str
    updatedAt: str


def _meta_rel(node_id: str) -> str:
    return f"text/{node_id}.meta.json"


def _text_rel(node_id: str) -> str:
    return f"text/{node_id}.txt"


@router.get("/projects/{project_id}/nodes/{node_id}", response_model=NodeTextResponse)
async def get_node_text(
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
        return NodeTextResponse(
            projectId=project_id,
            nodeId=node_id,
            content="",
            model="deepseek_v4_flash",
            ossKey="",
            fileUrl="",
            updatedAt="",
        )

    oss_key = meta.get("ossKey") or storage.project_key(
        project_id, _text_rel(node_id), storage_folder=folder
    )
    content_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
    content = content_bytes.decode("utf-8") if content_bytes else meta.get("content", "")

    return NodeTextResponse(
        projectId=project_id,
        nodeId=node_id,
        content=content,
        model=meta.get("model", "deepseek_v4_flash"),
        ossKey=oss_key,
        fileUrl=public_url_for_key(oss_key) if oss_key else "",
        updatedAt=meta.get("updatedAt", ""),
    )


@router.put("/projects/{project_id}/nodes/{node_id}", response_model=NodeTextResponse)
async def save_node_text(
    project_id: str,
    node_id: str,
    payload: NodeTextPayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await require_project_access(db, user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    text_obj = await asyncio.to_thread(
        storage.put,
        project_id,
        _text_rel(node_id),
        payload.content.encode("utf-8"),
        "text/plain; charset=utf-8",
        storage_folder=folder,
    )

    record = NodeTextResponse(
        projectId=project_id,
        nodeId=node_id,
        content=payload.content,
        model=payload.model,
        ossKey=text_obj.oss_key,
        fileUrl=public_url_for_key(text_obj.oss_key),
        updatedAt=cst_iso_now(),
    )
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _meta_rel(node_id),
        record.model_dump(),
        storage_folder=folder,
    )
    return record
