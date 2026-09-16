import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.entity_ids import require_entity_id

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_PROJECTS
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....integrations.oss.canvas_storage import get_canvas_storage, resolve_flow_json
from ....models.database import get_db
from ....models.project import Project, Workflow
from ....models.user import User
from ....services.project_scope import project_storage_folder
from ....schemas.admin import (
    AdminProjectAssetStatsOut,
    AdminProjectDetailOut,
    AdminProjectListOut,
    AdminProjectNodeOut,
    AdminProjectOut,
)

router = APIRouter()

ASSETS_INDEX = "assets/index.json"


def _project_out(project: Project, user: User) -> AdminProjectOut:
    return AdminProjectOut(
        id=str(project.id),
        project_no=project.project_no or "",
        title=project.title,
        owner_id=str(project.owner_id),
        owner_display_name=user.display_name or "",
        owner_phone=user.phone,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _parse_nodes(flow_json: str) -> list[AdminProjectNodeOut]:
    try:
        data = json.loads(flow_json)
    except json.JSONDecodeError:
        return []
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list):
        return []

    result: list[AdminProjectNodeOut] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        node_type = str(node.get("type", "unknown"))
        data_obj = node.get("data") if isinstance(node.get("data"), dict) else {}
        label = str(data_obj.get("label") or node_type)
        result.append(AdminProjectNodeOut(id=node_id, type=node_type, label=label))
    return result


def _asset_stats(project: Project) -> AdminProjectAssetStatsOut:
    storage = get_canvas_storage()
    folder = project_storage_folder(project)
    items = storage.read_json(str(project.id), ASSETS_INDEX, storage_folder=folder)
    if not isinstance(items, list):
        return AdminProjectAssetStatsOut(image=0, video=0, audio=0, total=0)

    image = sum(1 for a in items if isinstance(a, dict) and a.get("category") == "image")
    video = sum(1 for a in items if isinstance(a, dict) and a.get("category") == "video")
    audio = sum(1 for a in items if isinstance(a, dict) and a.get("category") == "audio")
    return AdminProjectAssetStatsOut(
        image=image,
        video=video,
        audio=audio,
        total=len(items),
    )


@router.get("/projects", response_model=AdminProjectListOut)
async def list_admin_projects(
    search: Optional[str] = Query(None, description="模糊匹配项目名/ID/用户名/手机号"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PROJECTS)),
):
    query = (
        select(Project, User)
        .join(User, Project.owner_id == User.id)
        .filter(Project.isdel.is_(False))
        .order_by(Project.updated_at.desc())
    )
    count_query = (
        select(func.count(Project.id))
        .select_from(Project)
        .join(User, Project.owner_id == User.id)
        .filter(Project.isdel.is_(False))
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        filters = or_(
            Project.title.ilike(term),
            Project.project_no.ilike(term),
            cast(Project.id, String).ilike(term),
            User.display_name.ilike(term),
            User.phone.ilike(term),
        )
        query = query.filter(filters)
        count_query = count_query.filter(filters)

    total = (await db.execute(count_query)).scalar_one()
    offset = (page - 1) * page_size
    rows = (await db.execute(query.offset(offset).limit(page_size))).all()

    return AdminProjectListOut(
        items=[_project_out(project, user) for project, user in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/projects/{project_id}", response_model=AdminProjectDetailOut)
async def get_admin_project_detail(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PROJECTS)),
):
    project_id_int = require_entity_id(project_id, message="项目 ID 无效")

    row = (
        await db.execute(
            select(Project, User)
            .join(User, Project.owner_id == User.id)
            .filter(Project.id == project_id_int, Project.isdel.is_(False))
        )
    ).first()
    if not row:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    project, user = row

    wf_result = await db.execute(
        select(Workflow)
        .filter(Workflow.project_id == project.id)
        .order_by(Workflow.updated_at.desc())
        .limit(1)
    )
    latest_wf = wf_result.scalar_one_or_none()

    workflow_count = (
        await db.execute(select(func.count(Workflow.id)).filter(Workflow.project_id == project.id))
    ).scalar_one()

    nodes: list[AdminProjectNodeOut] = []
    if latest_wf:
        storage = get_canvas_storage()
        flow = await asyncio.to_thread(resolve_flow_json, latest_wf.flow_json, storage)
        nodes = _parse_nodes(flow)

    asset_stats = await asyncio.to_thread(_asset_stats, project)

    return AdminProjectDetailOut(
        id=str(project.id),
        project_no=project.project_no or "",
        title=project.title,
        description=project.description,
        owner_id=str(project.owner_id),
        owner_display_name=user.display_name or "",
        owner_phone=user.phone,
        created_at=project.created_at,
        updated_at=project.updated_at,
        workflow_count=workflow_count or 0,
        latest_workflow_id=str(latest_wf.id) if latest_wf else None,
        nodes=nodes,
        asset_stats=asset_stats,
    )
