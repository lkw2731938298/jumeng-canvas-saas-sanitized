from ...core.datetime_util import cst_iso_now, now_cst_naive
import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...integrations.oss.canvas_storage import get_canvas_storage
from ...models.database import get_db
from ...models.user import User
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder

router = APIRouter()


class DirectorScenePayload(BaseModel):
    scene: dict[str, Any] = Field(default_factory=dict)


class DirectorSceneResponse(BaseModel):
    projectId: str
    nodeId: str
    scene: dict[str, Any]
    ossKey: str
    updatedAt: str


def _scene_rel(node_id: str) -> str:
    return f"director/{node_id}.json"


@router.get("/projects/{project_id}/nodes/{node_id}", response_model=DirectorSceneResponse)
async def get_director_scene(
    project_id: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = await require_project_access(db, current_user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    data = await asyncio.to_thread(
        storage.read_json, project_id, _scene_rel(node_id), storage_folder=folder
    )
    if not isinstance(data, dict):
        fail(ErrorCode.DIRECTOR_SCENE_NOT_FOUND)

    oss_key = data.get("ossKey") or storage.project_key(
        project_id, _scene_rel(node_id), storage_folder=folder
    )
    scene = data.get("scene")
    if not isinstance(scene, dict):
        scene = data if "objects" in data else {}

    return DirectorSceneResponse(
        projectId=project_id,
        nodeId=node_id,
        scene=scene,
        ossKey=oss_key,
        updatedAt=data.get("updatedAt", ""),
    )


@router.put("/projects/{project_id}/nodes/{node_id}", response_model=DirectorSceneResponse)
async def save_director_scene(
    project_id: str,
    node_id: str,
    payload: DirectorScenePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = await require_project_access(db, user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    oss_key = storage.project_key(project_id, _scene_rel(node_id), storage_folder=folder)
    record = DirectorSceneResponse(
        projectId=project_id,
        nodeId=node_id,
        scene=payload.scene,
        ossKey=oss_key,
        updatedAt=cst_iso_now(),
    )
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _scene_rel(node_id),
        record.model_dump(),
        storage_folder=folder,
    )
    return record
