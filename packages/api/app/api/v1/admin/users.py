from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import cast, delete, func, or_, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_USERS, resolved_permissions
from ....core.credit_amount import normalize_credit_amount
from ....core.deps import require_permission
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.auth_event import AuthEvent
from ....models.database import get_db
from ....models.job import GenerationJob
from ....models.project import Project, Workflow
from ....models.user import User, UserSession
from ....schemas.admin import (
    AdminAuthEventBriefOut,
    AdminCreditTransactionListOut,
    AdminCreditTransactionOut,
    AdminUserDetailOut,
    AdminUserListItemOut,
    AdminUserListOut,
    AdminUserPatchIn,
    AdminUserRecentJobOut,
)
from ....services.credit_flow import get_user_credit_balance
from ....services.credit_transaction_query import list_credit_transactions
from ....core.entity_ids import format_user_display_id, resolve_user_by_ref

router = APIRouter()

_SORT_COLUMNS = {
    "created_at": User.created_at,
    "last_login_at": User.last_login_at,
    "compute_power": User.compute_power,
}


def _project_count_subq():
    return (
        select(func.count(Project.id))
        .where(Project.owner_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )


def _job_count_subq():
    return (
        select(func.count(GenerationJob.id))
        .where(GenerationJob.user_id == User.id)
        .correlate(User)
        .scalar_subquery()
    )


async def _resolve_admin_user(db: AsyncSession, user_ref: str) -> User:
    return await resolve_user_by_ref(db, user_ref)


def _guard_super_admin_target(admin: User, target: User, *, mutating_role_or_active: bool) -> None:
    """禁止非超管改动超管；禁止任何人降权/禁用超管。"""
    if not bool(getattr(target, "is_super_admin", False)):
        return
    if mutating_role_or_active:
        fail(ErrorCode.SUPER_ADMIN_PROTECTED)
    if not bool(getattr(admin, "is_super_admin", False)) and admin.id != target.id:
        fail(ErrorCode.SUPER_ADMIN_PROTECTED)


async def _user_list_item(user: User, project_count: int, job_count: int, db: AsyncSession) -> AdminUserListItemOut:
    # 余额可能为一位小数；须 normalize 后再进 Pydantic，避免 int 校验失败导致整表空白
    balance = normalize_credit_amount(await get_user_credit_balance(user, db) or 0)
    return AdminUserListItemOut(
        id=str(user.id),
        user_no=format_user_display_id(user.id),
        display_name=user.display_name or "",
        phone=user.phone,
        role=user.role or "user",
        is_super_admin=bool(getattr(user, "is_super_admin", False)),
        is_active=user.is_active is not False,
        balance=balance,
        project_count=int(project_count or 0),
        job_count=int(job_count or 0),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


async def _build_user_detail(db: AsyncSession, user: User) -> AdminUserDetailOut:
    project_count = (
        await db.execute(select(func.count(Project.id)).filter(Project.owner_id == user.id))
    ).scalar_one()

    job_count = (
        await db.execute(select(func.count(GenerationJob.id)).filter(GenerationJob.user_id == user.id))
    ).scalar_one()

    workflow_count = (
        await db.execute(
            select(func.count(Workflow.id))
            .select_from(Workflow)
            .join(Project, Workflow.project_id == Project.id)
            .filter(Project.owner_id == user.id)
        )
    ).scalar_one()

    session_count = (
        await db.execute(select(func.count(UserSession.id)).filter(UserSession.user_id == user.id))
    ).scalar_one()

    status_rows = (
        await db.execute(
            select(GenerationJob.status, func.count(GenerationJob.id))
            .filter(GenerationJob.user_id == user.id)
            .group_by(GenerationJob.status)
        )
    ).all()
    jobs_by_status = {row[0]: row[1] for row in status_rows}

    recent_result = await db.execute(
        select(GenerationJob)
        .filter(GenerationJob.user_id == user.id)
        .order_by(GenerationJob.created_at.desc())
        .limit(5)
    )
    recent_jobs = [
        AdminUserRecentJobOut(
            id=str(job.id),
            status=job.status,
            job_type=job.job_type,
            lane=job.lane,
            model=job.model,
            credit_cost=normalize_credit_amount(job.credit_cost, default=0),
            created_at=job.created_at,
        )
        for job in recent_result.scalars().all()
    ]

    balance = normalize_credit_amount(await get_user_credit_balance(user, db) or 0)

    if user.phone:
        auth_condition = or_(AuthEvent.user_id == user.id, AuthEvent.phone == user.phone)
    else:
        auth_condition = AuthEvent.user_id == user.id

    recent_auth_result = await db.execute(
        select(AuthEvent)
        .filter(auth_condition)
        .order_by(AuthEvent.created_at.desc())
        .limit(8)
    )
    recent_auth_events = [
        AdminAuthEventBriefOut(
            id=str(row.id),
            action=row.action,
            result=row.result,
            ip=row.ip or "",
            created_at=row.created_at,
        )
        for row in recent_auth_result.scalars().all()
    ]

    return AdminUserDetailOut(
        id=str(user.id),
        user_no=format_user_display_id(user.id),
        source_user_id=user.source_user_id or "",
        display_name=user.display_name or "",
        phone=user.phone,
        role=user.role or "user",
        is_super_admin=bool(getattr(user, "is_super_admin", False)),
        permissions=resolved_permissions(user) if (user.role or "") == "admin" else [],
        is_active=user.is_active is not False,
        avatar_url=user.avatar_url,
        balance=balance,
        project_count=project_count,
        job_count=job_count,
        workflow_count=workflow_count,
        session_count=session_count,
        jobs_by_status=jobs_by_status,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        last_login_ip=user.last_login_ip or "",
        recent_auth_events=recent_auth_events,
        recent_jobs=recent_jobs,
    )


def _guard_self_admin_mutation(admin: User, target: User, patch: AdminUserPatchIn) -> None:
    if admin.id != target.id:
        return
    if patch.role is not None and patch.role != "admin":
        fail(ErrorCode.CANNOT_MODIFY_SELF, message="不能修改自己的管理员角色")
    if patch.is_active is False:
        fail(ErrorCode.CANNOT_MODIFY_SELF, message="不能禁用自己的账号")


@router.get("/users", response_model=AdminUserListOut)
async def list_admin_users(
    search: Optional[str] = Query(None, description="模糊匹配昵称/手机号/用户 ID"),
    role: Optional[str] = Query(None, description="user 或 admin"),
    is_active: Optional[bool] = Query(None, description="按账号状态筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort: Literal["created_at", "last_login_at", "compute_power"] = Query("created_at"),
    order: Literal["asc", "desc"] = Query("desc"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    project_count = _project_count_subq().label("project_count")
    job_count = _job_count_subq().label("job_count")

    query = select(User, project_count, job_count)
    count_query = select(func.count(User.id))

    if search and search.strip():
        term = f"%{search.strip()}%"
        filters = or_(
            User.display_name.ilike(term),
            User.phone.ilike(term),
            cast(User.id, String).ilike(term),
        )
        query = query.filter(filters)
        count_query = count_query.filter(filters)

    if role and role.strip() in ("user", "admin"):
        query = query.filter(User.role == role.strip())
        count_query = count_query.filter(User.role == role.strip())

    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
        count_query = count_query.filter(User.is_active.is_(is_active))

    sort_col = _SORT_COLUMNS.get(sort, User.created_at)
    query = query.order_by(sort_col.asc() if order == "asc" else sort_col.desc())

    total = (await db.execute(count_query)).scalar_one()
    offset = (page - 1) * page_size
    rows = (await db.execute(query.offset(offset).limit(page_size))).all()

    items = [
        await _user_list_item(user, proj_cnt, job_cnt, db)
        for user, proj_cnt, job_cnt in rows
    ]

    return AdminUserListOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
async def get_admin_user_detail(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    user = await _resolve_admin_user(db, user_id)
    return await _build_user_detail(db, user)


@router.patch("/users/{user_id}", response_model=AdminUserDetailOut)
async def patch_admin_user(
    user_id: str,
    body: AdminUserPatchIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission(PERM_USERS)),
):
    user = await _resolve_admin_user(db, user_id)
    _guard_self_admin_mutation(admin, user, body)

    mutating_protected = body.role is not None or body.is_active is not None
    _guard_super_admin_target(admin, user, mutating_role_or_active=mutating_protected)

    if body.display_name is not None:
        user.display_name = body.display_name.strip()

    if body.role is not None:
        if body.role not in ("user", "admin"):
            fail(ErrorCode.INVALID_ROLE)
        # 新升管理员默认无权限，需持有「管理员权限设置」的账号另行勾选
        if body.role == "admin" and user.role != "admin":
            user.admin_permissions = []
        if body.role == "user":
            user.admin_permissions = None
            user.is_super_admin = False
        user.role = body.role

    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            await db.execute(delete(UserSession).where(UserSession.user_id == user.id))

    await db.flush()
    return await _build_user_detail(db, user)


class AdminRevokeSessionsOut(BaseModel):
    revoked: int


@router.post("/users/{user_id}/revoke-sessions", response_model=AdminRevokeSessionsOut)
async def revoke_admin_user_sessions(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    user = await _resolve_admin_user(db, user_id)

    result = await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await db.flush()
    return AdminRevokeSessionsOut(revoked=int(result.rowcount or 0))


@router.get("/users/{user_id}/credit-history", response_model=AdminCreditTransactionListOut)
async def list_admin_user_credit_history(
    user_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    user = await _resolve_admin_user(db, user_id)

    items_raw, total = await list_credit_transactions(
        db,
        user_id=user.id,
        page=page,
        page_size=page_size,
    )
    items = [AdminCreditTransactionOut(**row) for row in items_raw]

    return AdminCreditTransactionListOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
