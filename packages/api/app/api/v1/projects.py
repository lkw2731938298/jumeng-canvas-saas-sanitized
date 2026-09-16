"""项目接口：项目 CRUD、封面、成员与邀请、在线状态、协作算力设置及生成审批。"""

from ...core.datetime_util import now_cst_naive, to_cst_iso
import asyncio
import time
import random
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from ...models.database import get_db
from ...models.user import User
from ...models.project import Project, Workflow
from ...schemas.api import (
    ProjectBatchIdsIn,
    ProjectBatchResultOut,
    ProjectBillingBalanceOut,
    ProjectCollaborationSettingsIn,
    ProjectCollaborationSettingsOut,
    ProjectIn,
    ProjectMemberInviteIn,
    ProjectMemberLookupOut,
    ProjectMemberOut,
    ProjectOut,
    ProjectPendingApprovalOut,
    ProjectPresenceOut,
    ProjectPresenceUserOut,
    ProjectUpdateIn,
)
from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import AppError, fail
from ...integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage
from ...services.credit_flow import credits_enabled, get_user_credit_balance
from ...services.project_cover import assign_project_cover, resolve_project_cover_url
from ...services.project_purge import hide_project, purge_project, restore_project
from ...services.project_access import (
    ProjectRole,
    get_project_owner_user,
    require_project_membership,
    require_project_owner,
    require_trashed_project_owner,
)
from ...services.project_members import (
    accept_project_invite,
    count_pending_invites,
    decline_project_invite,
    invite_project_member,
    leave_project,
    list_pending_invites_for_user,
    list_project_members,
    list_shared_projects,
    lookup_user_by_phone,
    record_project_access,
    remove_project_member,
)
from ...services.project_presence import (
    clear_project_presence,
    list_project_presence,
    touch_project_presence,
)
from ...services.project_scope import new_storage_folder, project_storage_folder
from ...services.project_create_limits import assert_project_create_allowed
from ...services.project_collaboration_spend import (
    approve_collaborator_generation,
    get_collaboration_spend_snapshot,
    list_pending_approvals_out,
    reject_collaborator_generation,
    update_collaboration_settings,
)
from ...services.storage_urls import oss_key_from_browser_url

router = APIRouter()

COVER_IMAGE_MIME = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
}
COVER_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


def _gen_project_no() -> str:
    ts = int(time.time() * 1000) % 100000000
    suffix = random.randint(0, 99)
    return f"PRJ-{ts:08d}{suffix:02d}"


def _ensure_project_no(project: Project, db: AsyncSession):
    if not project.project_no:
        project.project_no = _gen_project_no()


def _project_out(
    project: Project,
    workflow_count: int = 0,
    *,
    role: str | None = None,
    owner_display_name: str | None = None,
) -> ProjectOut:
    return ProjectOut(
        id=str(project.id),
        project_no=project.project_no,
        title=project.title,
        description=project.description,
        cover_url=resolve_project_cover_url(project),
        workflow_count=workflow_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
        role=role,
        owner_id=str(project.owner_id) if role == ProjectRole.EDITOR.value else None,
        owner_display_name=owner_display_name,
    )


async def _workflow_counts(db: AsyncSession, project_ids: list) -> dict[str, int]:
    if not project_ids:
        return {}
    count_result = await db.execute(
        select(Workflow.project_id, func.count(Workflow.id))
        .filter(Workflow.project_id.in_(project_ids))
        .group_by(Workflow.project_id)
    )
    return {str(row[0]): row[1] for row in count_result.all()}


def _resolve_cover_content_type(content_type: str, filename: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized in COVER_IMAGE_MIME:
        return normalized
    if ext in COVER_EXT:
        return COVER_EXT[ext]
    return normalized


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户拥有的全部项目（按更新时间倒序）。"""
    result = await db.execute(
        select(Project)
        .filter(Project.owner_id == current_user.id, Project.isdel.is_(False))
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()

    for p in projects:
        _ensure_project_no(p, db)
    await db.flush()

    counts = await _workflow_counts(db, [p.id for p in projects])
    return [
        _project_out(p, counts.get(str(p.id), 0), role=ProjectRole.OWNER.value)
        for p in projects
    ]


@router.get("/trash", response_model=list[ProjectOut])
async def list_trash_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户回收站中的项目（isdel=1，按移入时间倒序）。"""
    result = await db.execute(
        select(Project)
        .filter(Project.owner_id == current_user.id, Project.isdel.is_(True))
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()
    for p in projects:
        _ensure_project_no(p, db)
    await db.flush()
    counts = await _workflow_counts(db, [p.id for p in projects])
    return [
        _project_out(p, counts.get(str(p.id), 0), role=ProjectRole.OWNER.value)
        for p in projects
    ]


@router.post("/batch/hide", response_model=ProjectBatchResultOut)
async def batch_hide_projects(
    body: ProjectBatchIdsIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量移入回收站（软删除）。"""
    ok_count = 0
    failed: list[str] = []
    for pid in body.project_ids:
        try:
            project = await require_project_owner(db, current_user, pid)
            await hide_project(db, project)
            ok_count += 1
        except AppError:
            failed.append(pid)
    await db.commit()
    return ProjectBatchResultOut(ok_count=ok_count, failed_ids=failed)


@router.post("/batch/restore", response_model=ProjectBatchResultOut)
async def batch_restore_projects(
    body: ProjectBatchIdsIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量从回收站恢复。"""
    ok_count = 0
    failed: list[str] = []
    for pid in body.project_ids:
        try:
            project = await require_trashed_project_owner(db, current_user, pid)
            await restore_project(db, project)
            ok_count += 1
        except AppError:
            failed.append(pid)
    await db.commit()
    return ProjectBatchResultOut(ok_count=ok_count, failed_ids=failed)


@router.post("/batch/purge", response_model=ProjectBatchResultOut)
async def batch_purge_projects(
    body: ProjectBatchIdsIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量永久删除（仅回收站内项目；会清理 OSS/任务等）。"""
    ok_count = 0
    failed: list[str] = []
    for pid in body.project_ids:
        try:
            project = await require_trashed_project_owner(db, current_user, pid)
            await purge_project(db, project)
            ok_count += 1
        except AppError:
            failed.append(pid)
    await db.commit()
    return ProjectBatchResultOut(ok_count=ok_count, failed_ids=failed)


@router.get("/shared", response_model=list[ProjectOut])
async def list_shared_projects_route(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户作为协作者被共享的项目。"""
    rows = await list_shared_projects(db, current_user)
    for project, _member, _owner in rows:
        _ensure_project_no(project, db)
    await db.flush()

    counts = await _workflow_counts(db, [project.id for project, _, _ in rows])
    return [
        _project_out(
            project,
            counts.get(str(project.id), 0),
            role=ProjectRole.EDITOR.value,
            owner_display_name=owner.display_name or "",
        )
        for project, _member, owner in rows
    ]


def _pending_invite_rows(
    rows: list[tuple],
) -> list[dict]:
    out: list[dict] = []
    for member, project, inviter_name in rows:
        cover_url: str | None = None
        try:
            cover_url = resolve_project_cover_url(project)
        except Exception:
            cover_url = project.cover_url or None
        out.append(
            {
                "projectId": str(project.id),
                "projectNo": project.project_no,
                "title": project.title or "",
                "coverUrl": cover_url,
                "inviterDisplayName": inviter_name,
                "invitedAt": to_cst_iso(member.invited_at),
            }
        )
    return out


@router.get("/invites")
@router.get("/pending-invites")
async def list_project_invites(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户待处理的项目协作邀请。"""
    rows = await list_pending_invites_for_user(db, current_user)
    return _pending_invite_rows(rows)


@router.get("/invites/count")
async def get_project_invites_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户待处理邀请的数量。"""
    count = await count_pending_invites(db, current_user)
    return {"count": count}


@router.post("/invites/{project_id}/accept", response_model=ProjectOut)
async def accept_project_invite_route(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """接受项目协作邀请并返回该项目信息。"""
    project = await accept_project_invite(db, user=current_user, project_id=project_id)
    await db.commit()
    _ensure_project_no(project, db)
    await db.flush()
    owner = await get_project_owner_user(db, project)
    counts = await _workflow_counts(db, [project.id])
    return _project_out(
        project,
        counts.get(str(project.id), 0),
        role=ProjectRole.EDITOR.value,
        owner_display_name=owner.display_name or "",
    )


@router.post("/invites/{project_id}/decline")
async def decline_project_invite_route(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """拒绝项目协作邀请。"""
    await decline_project_invite(db, user=current_user, project_id=project_id)
    await db.commit()
    return {"ok": True}


@router.post("", response_model=ProjectOut)
async def create_project(
    req: ProjectIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为当前用户创建新项目并分配项目编号与存储目录。"""
    # 5 秒冷却 + 每日 30 个上限
    await assert_project_create_allowed(db, int(current_user.id))
    project = Project(
        owner_id=current_user.id,
        project_no=_gen_project_no(),
        title=req.title,
        description=req.description,
        storage_folder=new_storage_folder(),
    )
    db.add(project)
    await db.flush()
    return _project_out(project, role=ProjectRole.OWNER.value)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个项目详情（校验成员权限，附带工作流数量）。"""
    membership = await require_project_membership(db, current_user, project_id)
    project = membership.project
    _ensure_project_no(project, db)
    await db.flush()

    owner_name = None
    if membership.role == ProjectRole.EDITOR:
        owner = await get_project_owner_user(db, project)
        owner_name = owner.display_name or ""

    count_result = await db.execute(
        select(func.count(Workflow.id)).filter(Workflow.project_id == project.id)
    )
    workflow_count = count_result.scalar_one() or 0
    return _project_out(
        project,
        workflow_count,
        role=membership.role.value,
        owner_display_name=owner_name,
    )


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: str, req: ProjectUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新项目基本信息（标题、描述、封面），仅项目所有者可操作。"""
    project = await require_project_owner(db, current_user, project_id)
    if req.title:
        project.title = req.title
    if req.description is not None:
        project.description = req.description
    if req.cover_url is not None:
        if req.cover_url.strip():
            key = oss_key_from_browser_url(req.cover_url)
            if key:
                assign_project_cover(project, oss_key=key)
            else:
                from ...services.storage_urls import persistable_media_url

                project.cover_url = persistable_media_url(req.cover_url) or req.cover_url
        else:
            project.cover_url = None
            project.cover_oss_key = None
    project.updated_at = now_cst_naive()
    await db.flush()
    return _project_out(project, role=ProjectRole.OWNER.value)


@router.post("/{project_id}/cover", response_model=ProjectOut)
async def upload_project_cover(
    project_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传项目封面图片到 OSS 并更新项目封面，仅项目所有者可操作。"""
    project = await require_project_owner(db, current_user, project_id)
    content_type = _resolve_cover_content_type(file.content_type or "", file.filename)
    if content_type not in COVER_IMAGE_MIME:
        fail(ErrorCode.COVER_INVALID_TYPE)

    ext = Path(file.filename or "").suffix.lower()
    if ext not in COVER_EXT:
        ext = ".jpg" if content_type == "image/jpeg" else ".png"

    data = await file.read()
    if not data:
        fail(ErrorCode.COVER_EMPTY)

    storage = get_canvas_storage()
    folder = project_storage_folder(project)
    rel_path = f"cover/cover{ext}"
    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel_path,
            data,
            content_type,
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=str(exc))

    assign_project_cover(project, oss_key=stored.oss_key)
    await db.flush()
    return _project_out(project, role=ProjectRole.OWNER.value)


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """隐藏项目（软删除 isdel），仅项目所有者可操作；不物理清理数据。"""
    project = await require_project_owner(db, current_user, project_id)
    await hide_project(db, project)
    await db.commit()
    return {"ok": True}


@router.post("/{project_id}/restore", response_model=ProjectOut)
async def restore_project_route(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从回收站恢复项目。"""
    project = await require_trashed_project_owner(db, current_user, project_id)
    await restore_project(db, project)
    await db.commit()
    return _project_out(project, role=ProjectRole.OWNER.value)


@router.delete("/{project_id}/permanent")
async def purge_project_route(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """永久删除回收站中的项目（清理 OSS / 任务 / 工作流后删行）。"""
    project = await require_trashed_project_owner(db, current_user, project_id)
    await purge_project(db, project)
    await db.commit()
    return {"ok": True}


@router.get("/{project_id}/members", response_model=list[ProjectMemberOut])
async def get_project_members(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出项目成员，仅项目所有者可查看。"""
    rows = await list_project_members(db, owner=current_user, project_id=project_id)
    return [ProjectMemberOut.model_validate(row) for row in rows]


@router.get("/{project_id}/members/lookup", response_model=ProjectMemberLookupOut)
async def lookup_project_member_candidate(
    project_id: str,
    phone: str = Query(..., min_length=11, max_length=11),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按手机号查找可邀请为协作者的候选用户。"""
    row = await lookup_user_by_phone(
        db, requester=current_user, project_id=project_id, phone=phone
    )
    return ProjectMemberLookupOut.model_validate(row)


@router.post("/{project_id}/members", response_model=ProjectMemberOut)
async def add_project_member(
    project_id: str,
    body: ProjectMemberInviteIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """邀请指定用户加入项目作为协作者，仅项目所有者可操作。"""
    row = await invite_project_member(
        db, owner=current_user, project_id=project_id, user_id=body.user_id
    )
    await db.commit()
    return ProjectMemberOut.model_validate(row)


@router.delete("/{project_id}/members/{member_user_id}")
async def delete_project_member(
    project_id: str,
    member_user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从项目中移除指定协作成员，仅项目所有者可操作。"""
    await remove_project_member(
        db, owner=current_user, project_id=project_id, member_user_id=member_user_id
    )
    await db.commit()
    return {"ok": True}


@router.post("/{project_id}/members/leave")
async def leave_shared_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前协作者主动退出共享项目。"""
    await leave_project(db, user=current_user, project_id=project_id)
    await db.commit()
    return {"ok": True}


@router.post("/{project_id}/access")
async def touch_project_access(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """记录当前用户对项目的最近访问时间。"""
    await record_project_access(db, user=current_user, project_id=project_id)
    await db.commit()
    return {"ok": True}


@router.get("/{project_id}/billing-balance", response_model=ProjectBillingBalanceOut)
async def get_project_billing_balance(
    project_id: str,
    model: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询项目计费余额（以项目所有者的算力为准，供协作者展示）。"""
    membership = await require_project_membership(db, current_user, project_id)
    owner = await get_project_owner_user(db, membership.project)
    enabled = credits_enabled()
    if not enabled:
        return ProjectBillingBalanceOut(
            creditsEnabled=False,
            balance=None,
            availableForModel=None,
            ownerDisplayName=owner.display_name or "",
            isCollaborator=membership.role == ProjectRole.EDITOR,
        )

    balance = await get_user_credit_balance(owner, db, model_name=model)
    return ProjectBillingBalanceOut(
        creditsEnabled=True,
        balance=balance,
        availableForModel=balance if model else None,
        ownerDisplayName=owner.display_name or "",
        isCollaborator=membership.role == ProjectRole.EDITOR,
    )


@router.get("/{project_id}/presence", response_model=ProjectPresenceOut)
async def get_project_presence(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目内其他在线协作者的实时在线状态。"""
    await require_project_membership(db, current_user, project_id)
    rows = await list_project_presence(project_id, exclude_user_id=str(current_user.id))
    return ProjectPresenceOut(
        users=[ProjectPresenceUserOut.model_validate(row) for row in rows]
    )


@router.post("/{project_id}/presence")
async def post_project_presence(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上报当前用户在项目中的在线心跳。"""
    await require_project_membership(db, current_user, project_id)
    await touch_project_presence(project_id, current_user)
    return {"ok": True}


@router.delete("/{project_id}/presence")
async def delete_project_presence(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """清除当前用户在项目中的在线状态（离开画布时调用）。"""
    await require_project_membership(db, current_user, project_id)
    await clear_project_presence(project_id, str(current_user.id))
    return {"ok": True}


def _collaboration_settings_out(project: Project, snapshot) -> ProjectCollaborationSettingsOut:
    return ProjectCollaborationSettingsOut(
        collaboratorDailyCap=snapshot.daily_cap,
        collaboratorTotalCap=snapshot.total_cap,
        collaboratorApprovalThreshold=snapshot.approval_threshold,
        dailySpent=snapshot.daily_spent,
        totalSpent=snapshot.total_spent,
        pendingApprovalCount=snapshot.pending_approval_count,
    )


@router.get("/{project_id}/collaboration-settings", response_model=ProjectCollaborationSettingsOut)
async def get_collaboration_settings(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取项目协作算力设置及当前消耗快照。"""
    membership = await require_project_membership(db, current_user, project_id)
    snapshot = await get_collaboration_spend_snapshot(db, membership.project)
    return _collaboration_settings_out(membership.project, snapshot)


@router.put("/{project_id}/collaboration-settings", response_model=ProjectCollaborationSettingsOut)
async def put_collaboration_settings(
    project_id: str,
    body: ProjectCollaborationSettingsIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新项目协作算力设置（每日/总额上限与审批阈值），仅所有者可操作。"""
    project = await update_collaboration_settings(
        db,
        owner=current_user,
        project_id=project_id,
        daily_cap=body.collaborator_daily_cap,
        total_cap=body.collaborator_total_cap,
        approval_threshold=body.collaborator_approval_threshold,
    )
    await db.commit()
    snapshot = await get_collaboration_spend_snapshot(db, project)
    return _collaboration_settings_out(project, snapshot)


@router.get("/{project_id}/pending-approvals", response_model=list[ProjectPendingApprovalOut])
async def get_pending_approvals(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出项目内待审批的协作者生成任务，仅项目所有者可查看。"""
    project = await require_project_owner(db, current_user, project_id)
    rows = await list_pending_approvals_out(db, project)
    return [ProjectPendingApprovalOut.model_validate(row) for row in rows]


@router.post("/{project_id}/approvals/{job_id}/approve")
async def approve_pending_generation(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批通过协作者的待审生成任务，仅项目所有者可操作。"""
    job = await approve_collaborator_generation(
        db, owner=current_user, project_id=project_id, job_id=job_id
    )
    await db.commit()
    return {"ok": True, "jobId": str(job.id), "status": job.status}


@router.post("/{project_id}/approvals/{job_id}/reject")
async def reject_pending_generation(
    project_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """驳回协作者的待审生成任务，仅项目所有者可操作。"""
    job = await reject_collaborator_generation(
        db, owner=current_user, project_id=project_id, job_id=job_id
    )
    await db.commit()
    return {"ok": True, "jobId": str(job.id), "status": job.status}
