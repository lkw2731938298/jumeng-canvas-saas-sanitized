"""工作流接口：工作流的获取（含最新与恢复）、列表、创建与更新，flow_json 大对象存 OSS。"""

from ...core.datetime_util import now_cst_naive
import json
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ...models.database import get_db
from ...models.user import User
from ...models.project import Workflow
from ...schemas.api import WorkflowIn, WorkflowOut
from ...core.deps import get_current_user
from ...core.entity_ids import require_entity_id
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...integrations.oss.canvas_storage import (
    StorageWriteError,
    get_canvas_storage,
    resolve_flow_json,
    store_flow_json,
)
from ...services.project_access import require_project_access, require_workflow_project_access
from ...services.workflow_recovery import (
    make_flow_pointer,
    pick_best_workflow_candidate,
    resolve_workflow_flow_json,
    scan_workflow_candidates,
)

router = APIRouter()


def _count_nodes(flow_json: str) -> int:
    try:
        data = json.loads(flow_json)
        nodes = data.get("nodes") if isinstance(data, dict) else None
        return len(nodes) if isinstance(nodes, list) else 0
    except json.JSONDecodeError:
        return 0


def _workflow_out(wf: Workflow, resolved_flow: str | None = None) -> WorkflowOut:
    flow = resolved_flow if resolved_flow is not None else resolve_flow_json(wf.flow_json)
    return WorkflowOut(
        id=str(wf.id),
        project_id=str(wf.project_id),
        title=wf.title,
        flow_json=flow,
        version=wf.version,
        revision=int(wf.revision or 1),
        node_count=int(wf.node_count or 0),
        status=wf.status,
        created_at=wf.created_at,
        updated_at=wf.updated_at,
    )


async def _load_workflow_flow(project, wf: Workflow) -> str:
    storage = get_canvas_storage()
    flow, recovered_key = await asyncio.to_thread(
        resolve_workflow_flow_json,
        wf.flow_json,
        project_id=str(project.id),
        storage_folder=project.storage_folder,
        storage=storage,
    )
    if recovered_key and flow:
        wf.flow_json = json.dumps({"_storage": "oss", "ossKey": recovered_key}, ensure_ascii=False)
        wf.node_count = str(_count_nodes(flow))
        wf.updated_at = now_cst_naive()
    return flow


@router.get("/projects/{project_id}/workflows/latest", response_model=WorkflowOut)
async def get_latest_workflow(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目最新工作流；无记录时尝试从 OSS 扫描候选恢复。"""
    project = await require_project_access(db, current_user, project_id)
    result = await db.execute(
        select(Workflow)
        .filter(Workflow.project_id == project.id)
        .order_by(Workflow.updated_at.desc())
        .limit(1)
    )
    wf = result.scalar_one_or_none()
    if not wf:
        candidates = await asyncio.to_thread(
            scan_workflow_candidates, str(project.id), project.storage_folder
        )
        best = pick_best_workflow_candidate(candidates)
        if best and best.node_count > 0:
            wf = Workflow(
                project_id=project.id,
                title="恢复的工作流",
                flow_json=make_flow_pointer(best.oss_key),
                node_count=str(best.node_count),
                revision=1,
                status="draft",
            )
            db.add(wf)
            await db.flush()
        else:
            # 尚无工作流：落空草稿（一键出海 / Agent 新建项目首屏常见）
            # 避免客户端 /workflows/latest 404 红字；后续保存仍走 PUT/POST
            empty_flow = json.dumps({"nodes": [], "edges": []}, ensure_ascii=False)
            wf = Workflow(
                project_id=project.id,
                title="未命名工作流",
                flow_json="{}",
                node_count="0",
                revision=1,
                status="draft",
            )
            db.add(wf)
            await db.flush()
            try:
                pointer = await asyncio.to_thread(
                    store_flow_json,
                    str(project.id),
                    str(wf.id),
                    empty_flow,
                    None,
                    project.storage_folder,
                )
                wf.flow_json = pointer
                await db.flush()
            except StorageWriteError:
                # OSS 暂不可写时仍返回空流，不 404
                wf.flow_json = empty_flow
                await db.flush()
            return _workflow_out(wf, empty_flow)
    flow = await _load_workflow_flow(project, wf)
    await db.flush()
    return _workflow_out(wf, flow)


@router.get("/projects/{project_id}/workflows", response_model=list[WorkflowOut])
async def list_workflows(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出指定项目的全部工作流（按更新时间倒序）。"""
    project = await require_project_access(db, current_user, project_id)
    result = await db.execute(
        select(Workflow).filter(Workflow.project_id == project.id).order_by(Workflow.updated_at.desc())
    )
    workflows = result.scalars().all()
    return [_workflow_out(w) for w in workflows]


@router.post("/projects/{project_id}/workflows", response_model=WorkflowOut)
async def create_workflow(
    project_id: str,
    req: WorkflowIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """在指定项目下创建新工作流，flow_json 大对象写入 OSS 并存指针。"""
    project = await require_project_access(db, current_user, project_id)

    wf = Workflow(
        project_id=project.id,
        title=req.title,
        flow_json="{}",
        node_count="0",
        revision=1,
        status="draft",
    )
    db.add(wf)
    await db.flush()

    try:
        pointer = await asyncio.to_thread(
            store_flow_json,
            str(project.id),
            str(wf.id),
            req.flow_json,
            None,
            project.storage_folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"工作流存储失败: {exc}")
    node_count = _count_nodes(req.flow_json)
    wf.flow_json = pointer
    wf.node_count = str(node_count)
    await db.flush()
    return _workflow_out(wf, req.flow_json)


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按工作流 ID 获取工作流详情（校验其所属项目访问权）。"""
    result = await db.execute(select(Workflow).filter(Workflow.id == require_entity_id(workflow_id, message="工作流 ID 无效")))
    wf = result.scalar_one_or_none()
    if not wf:
        fail(ErrorCode.WORKFLOW_NOT_FOUND)
    project = await require_workflow_project_access(db, current_user, wf)
    flow = await _load_workflow_flow(project, wf)
    await db.flush()
    return _workflow_out(wf, flow)


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str,
    req: WorkflowIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新工作流（含 revision 乐观锁），flow_json 大对象写入 OSS 并递增修订号。"""
    result = await db.execute(select(Workflow).filter(Workflow.id == require_entity_id(workflow_id, message="工作流 ID 无效")))
    wf = result.scalar_one_or_none()
    if not wf:
        fail(ErrorCode.WORKFLOW_NOT_FOUND)
    await require_workflow_project_access(db, current_user, wf)

    # 防止前端切换项目后仍带旧 workflowId：body.project_id 必须与工作流行一致
    body_project_id = str(getattr(req, "project_id", None) or "").strip()
    if body_project_id and body_project_id != str(wf.project_id):
        fail(
            ErrorCode.FORBIDDEN,
            message="工作流与项目不匹配，请刷新后重试",
            content={
                "workflowProjectId": str(wf.project_id),
                "requestProjectId": body_project_id,
            },
        )

    if req.expected_revision is not None:
        current_revision = int(wf.revision or 1)
        if int(req.expected_revision) != current_revision:
            fail(
                ErrorCode.REVISION_CONFLICT,
                content={"currentRevision": current_revision},
            )

    submitted_flow = req.flow_json
    if req.title:
        wf.title = req.title
    project = await require_workflow_project_access(db, current_user, wf)
    if submitted_flow:
        try:
            wf.flow_json = await asyncio.to_thread(
                store_flow_json,
                str(wf.project_id),
                workflow_id,
                submitted_flow,
                None,
                project.storage_folder,
            )
        except StorageWriteError as exc:
            fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"工作流存储失败: {exc}")
        wf.node_count = str(_count_nodes(submitted_flow))
    wf.revision = int(wf.revision or 1) + 1
    wf.updated_at = now_cst_naive()
    await db.flush()
    return _workflow_out(wf, submitted_flow if submitted_flow else None)
