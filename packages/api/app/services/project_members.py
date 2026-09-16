"""项目协作成员管理服务。

提供成员邀请、接受/拒绝、移除、离开及访问记录等能力；
所有写操作经 Redis 互斥锁保护，成员槽位上限由 ``MAX_PROJECT_MEMBERS`` 约束。
"""

from __future__ import annotations

from ..core.entity_ids import require_entity_id
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from ..core.guid_sql import guid_eq
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.project import Project
from ..models.project_member import (
    MEMBER_ROLE_EDITOR,
    MEMBER_STATUS_ACTIVE,
    MEMBER_STATUS_PENDING,
    MEMBER_STATUS_REMOVED,
    ProjectMember,
)
from ..models.user import User
from .auth_sms import mask_phone, require_valid_phone
from .cache import check_rate_limit
from .project_access import (
    ProjectRole,
    is_project_visible,
    require_project_membership,
    require_project_owner,
)
from .redis_mutex import redis_mutex

MAX_PROJECT_MEMBERS = 10

_MEMBER_SLOT_STATUSES = (MEMBER_STATUS_ACTIVE, MEMBER_STATUS_PENDING)


def _member_row_out(row: ProjectMember, user: User) -> dict[str, Any]:
    return {
        "userId": str(user.id),
        "displayName": user.display_name or "",
        "phoneMasked": mask_phone(user.phone or "") if user.phone else "",
        "status": row.status,
        "invitedAt": row.invited_at,
        "firstAccessedAt": row.first_accessed_at,
        "lastAccessedAt": row.last_accessed_at,
    }


async def list_project_members(
    db: AsyncSession,
    *,
    owner: User,
    project_id: str,
) -> list[dict[str, Any]]:
    """列出项目当前活跃或待接受的协作成员（仅项目创建者可调用）。"""
    project = await require_project_owner(db, owner, project_id)
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, guid_eq(ProjectMember.user_id, User.id))
        .filter(
            ProjectMember.project_id == project.id,
            ProjectMember.status.in_(_MEMBER_SLOT_STATUSES),
        )
        .order_by(ProjectMember.invited_at.asc())
    )
    return [_member_row_out(member, user) for member, user in result.all()]


async def lookup_user_by_phone(
    db: AsyncSession,
    *,
    requester: User,
    project_id: str,
    phone: str,
) -> dict[str, Any]:
    """按手机号查找可邀请用户，校验是否已是成员或已有待处理邀请。"""
    await require_project_owner(db, requester, project_id)
    allowed = await check_rate_limit(
        f"project:member:lookup:{requester.id}",
        limit=20,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    normalized = require_valid_phone(phone)
    result = await db.execute(select(User).filter(User.phone == normalized, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if not user:
        fail(ErrorCode.USER_NOT_FOUND, message="未找到该手机号对应的用户")

    project = await require_project_owner(db, requester, project_id)
    if user.id == project.owner_id:
        fail(ErrorCode.CANNOT_INVITE_OWNER, message="项目创建者无需邀请")
    if user.id == requester.id:
        fail(ErrorCode.CANNOT_INVITE_SELF, message="不能邀请自己")

    existing = await db.execute(
        select(ProjectMember).filter(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
            ProjectMember.status.in_(_MEMBER_SLOT_STATUSES),
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        if row.status == MEMBER_STATUS_ACTIVE:
            fail(ErrorCode.MEMBER_ALREADY_EXISTS, message="该用户已是协作成员")
        fail(ErrorCode.MEMBER_INVITE_PENDING, message="已向该用户发送邀请，等待对方接受")

    return {
        "userId": str(user.id),
        "displayName": user.display_name or "",
        "phoneMasked": mask_phone(normalized),
    }


async def invite_project_member(
    db: AsyncSession,
    *,
    owner: User,
    project_id: str,
    user_id: str,
) -> dict[str, Any]:
    """向指定用户发送协作邀请，创建或复用 ``pending`` 成员记录。"""
    project = await require_project_owner(db, owner, project_id)

    async with redis_mutex(
        f"lock:project:member:{project.id}",
        ttl_sec=15,
        busy_code=ErrorCode.RATE_LIMITED,
        busy_message="协作成员变更处理中，请稍后再试",
    ):
        target_id = require_entity_id(str(user_id).strip(), message="用户 ID 无效")

        if target_id == owner.id:
            fail(ErrorCode.CANNOT_INVITE_SELF)
        if target_id == project.owner_id:
            fail(ErrorCode.CANNOT_INVITE_OWNER)

        target_result = await db.execute(
            select(User).filter(User.id == target_id, User.is_active.is_(True))
        )
        target = target_result.scalar_one_or_none()
        if not target:
            fail(ErrorCode.USER_NOT_FOUND)

        slot_count = (
            await db.execute(
                select(func.count())
                .select_from(ProjectMember)
                .filter(
                    ProjectMember.project_id == project.id,
                    ProjectMember.status.in_(_MEMBER_SLOT_STATUSES),
                )
            )
        ).scalar_one()
        if slot_count >= MAX_PROJECT_MEMBERS:
            fail(
                ErrorCode.MEMBER_LIMIT_REACHED,
                message=f"协作成员已达上限（{MAX_PROJECT_MEMBERS} 人）",
                content={"max": MAX_PROJECT_MEMBERS},
            )

        existing = await db.execute(
            select(ProjectMember).filter(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_id,
            )
        )
        row = existing.scalar_one_or_none()
        now = now_cst_naive()
        if row:
            if row.status == MEMBER_STATUS_ACTIVE:
                fail(ErrorCode.MEMBER_ALREADY_EXISTS)
            if row.status == MEMBER_STATUS_PENDING:
                fail(ErrorCode.MEMBER_INVITE_PENDING)
            row.status = MEMBER_STATUS_PENDING
            row.role = MEMBER_ROLE_EDITOR
            row.invited_by = owner.id
            row.invited_at = now
            row.first_accessed_at = None
            row.last_accessed_at = None
            row.updated_at = now
            member = row
        else:
            member = ProjectMember(
                project_id=project.id,
                user_id=target_id,
                role=MEMBER_ROLE_EDITOR,
                invited_by=owner.id,
                invited_at=now,
                status=MEMBER_STATUS_PENDING,
                created_at=now,
                updated_at=now,
            )
            db.add(member)

        await db.flush()
        return _member_row_out(member, target)


async def remove_project_member(
    db: AsyncSession,
    *,
    owner: User,
    project_id: str,
    member_user_id: str,
) -> None:
    """项目创建者移除协作成员（含待接受邀请），状态置为 ``removed``。"""
    project = await require_project_owner(db, owner, project_id)

    async with redis_mutex(
        f"lock:project:member:{project.id}",
        ttl_sec=15,
        busy_code=ErrorCode.RATE_LIMITED,
        busy_message="协作成员变更处理中，请稍后再试",
    ):
        member_user_id_int = require_entity_id(str(member_user_id).strip(), message="用户 ID 无效")

        result = await db.execute(
            select(ProjectMember).filter(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == member_user_id_int,
                ProjectMember.status.in_(_MEMBER_SLOT_STATUSES),
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            fail(ErrorCode.MEMBER_NOT_FOUND, message="协作成员不存在")
        row.status = MEMBER_STATUS_REMOVED
        row.updated_at = now_cst_naive()
        await db.flush()


async def leave_project(
    db: AsyncSession,
    *,
    user: User,
    project_id: str,
) -> None:
    """协作成员主动离开项目；项目创建者不可调用。"""
    membership = await require_project_membership(db, user, project_id)
    if membership.role == ProjectRole.OWNER:
        fail(ErrorCode.ACCESS_DENIED, message="项目创建者不能离开自己的项目")

    async with redis_mutex(
        f"lock:project:member:{membership.project.id}",
        ttl_sec=15,
        busy_code=ErrorCode.RATE_LIMITED,
        busy_message="协作成员变更处理中，请稍后再试",
    ):
        result = await db.execute(
            select(ProjectMember).filter(
                ProjectMember.project_id == membership.project.id,
                ProjectMember.user_id == user.id,
                ProjectMember.status == MEMBER_STATUS_ACTIVE,
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            fail(ErrorCode.MEMBER_NOT_FOUND)
        row.status = MEMBER_STATUS_REMOVED
        row.updated_at = now_cst_naive()
        await db.flush()


async def record_project_access(
    db: AsyncSession,
    *,
    user: User,
    project_id: str,
) -> None:
    """记录协作成员首次/最近访问时间（创建者访问不写入）。"""
    membership = await require_project_membership(db, user, project_id)
    if membership.role == ProjectRole.OWNER:
        return

    result = await db.execute(
        select(ProjectMember).filter(
            ProjectMember.project_id == membership.project.id,
            ProjectMember.user_id == user.id,
            ProjectMember.status == MEMBER_STATUS_ACTIVE,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return

    now = now_cst_naive()
    if row.first_accessed_at is None:
        row.first_accessed_at = now
    row.last_accessed_at = now
    row.updated_at = now
    await db.flush()


async def list_shared_projects(
    db: AsyncSession,
    user: User,
) -> list[tuple[Project, ProjectMember, User]]:
    """返回用户作为活跃协作成员参与的项目列表（含项目、成员行、创建者）。"""
    result = await db.execute(
        select(Project, ProjectMember, User)
        .join(ProjectMember, guid_eq(ProjectMember.project_id, Project.id))
        .join(User, guid_eq(User.id, Project.owner_id))
        .filter(
            ProjectMember.user_id == user.id,
            ProjectMember.status == MEMBER_STATUS_ACTIVE,
            Project.isdel.is_(False),
        )
        .order_by(Project.updated_at.desc())
    )
    return list(result.all())


async def list_pending_invites_for_user(
    db: AsyncSession,
    user: User,
) -> list[tuple[ProjectMember, Project, str]]:
    """返回当前用户待处理的协作邀请，附带邀请人显示名（缺省回退为项目创建者）。"""
    result = await db.execute(
        select(ProjectMember, Project)
        .join(Project, guid_eq(ProjectMember.project_id, Project.id))
        .filter(
            ProjectMember.user_id == user.id,
            ProjectMember.status == MEMBER_STATUS_PENDING,
            Project.isdel.is_(False),
        )
        .order_by(ProjectMember.invited_at.desc())
    )
    rows = list(result.all())
    if not rows:
        return []

    inviter_ids = {m.invited_by for m, _ in rows if m.invited_by}
    owner_ids = {p.owner_id for _, p in rows}
    user_ids = inviter_ids | owner_ids
    names: dict[str, str] = {}
    if user_ids:
        users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        for inviter in users_result.scalars():
            names[str(inviter.id)] = inviter.display_name or ""

    return [
        (
            member,
            project,
            names.get(str(member.invited_by))
            or names.get(str(project.owner_id))
            or "",
        )
        for member, project in rows
    ]


async def count_pending_invites(db: AsyncSession, user: User) -> int:
    """统计用户待接受的协作邀请数量（不含已隐藏项目）。"""
    result = await db.execute(
        select(func.count())
        .select_from(ProjectMember)
        .join(Project, guid_eq(ProjectMember.project_id, Project.id))
        .filter(
            ProjectMember.user_id == user.id,
            ProjectMember.status == MEMBER_STATUS_PENDING,
            Project.isdel.is_(False),
        )
    )
    return int(result.scalar_one() or 0)


async def accept_project_invite(
    db: AsyncSession,
    *,
    user: User,
    project_id: str,
) -> Project:
    """接受协作邀请，将成员状态置为 ``active`` 并记录首次访问时间。"""
    pid = require_entity_id(str(project_id).strip(), message="项目 ID 无效")

    async with redis_mutex(
        f"lock:project:invite:accept:{user.id}:{pid}",
        ttl_sec=15,
        busy_code=ErrorCode.RATE_LIMITED,
        busy_message="邀请处理中，请稍后再试",
    ):
        project_result = await db.execute(select(Project).filter(Project.id == pid))
        project = project_result.scalar_one_or_none()
        if not project or not is_project_visible(project):
            fail(ErrorCode.INVITE_NOT_FOUND)

        member_result = await db.execute(
            select(ProjectMember)
            .filter(
                ProjectMember.project_id == pid,
                ProjectMember.user_id == user.id,
                ProjectMember.status == MEMBER_STATUS_PENDING,
            )
            .with_for_update()
        )
        row = member_result.scalar_one_or_none()
        if not row:
            fail(ErrorCode.INVITE_NOT_FOUND)

        active_count = (
            await db.execute(
                select(func.count())
                .select_from(ProjectMember)
                .filter(
                    ProjectMember.project_id == pid,
                    ProjectMember.status == MEMBER_STATUS_ACTIVE,
                )
            )
        ).scalar_one()
        if active_count >= MAX_PROJECT_MEMBERS:
            fail(
                ErrorCode.MEMBER_LIMIT_REACHED,
                message=f"该项目协作成员已达上限（{MAX_PROJECT_MEMBERS} 人），无法接受邀请",
                content={"max": MAX_PROJECT_MEMBERS},
            )

        now = now_cst_naive()
        row.status = MEMBER_STATUS_ACTIVE
        row.first_accessed_at = now
        row.last_accessed_at = now
        row.updated_at = now
        await db.flush()
        return project


async def decline_project_invite(
    db: AsyncSession,
    *,
    user: User,
    project_id: str,
) -> None:
    """拒绝协作邀请，将待处理成员记录标记为 ``removed``。"""
    pid = require_entity_id(str(project_id).strip(), message="项目 ID 无效")

    async with redis_mutex(
        f"lock:project:invite:decline:{user.id}:{pid}",
        ttl_sec=15,
        busy_code=ErrorCode.RATE_LIMITED,
        busy_message="邀请处理中，请稍后再试",
    ):
        member_result = await db.execute(
            select(ProjectMember)
            .filter(
                ProjectMember.project_id == pid,
                ProjectMember.user_id == user.id,
                ProjectMember.status == MEMBER_STATUS_PENDING,
            )
            .with_for_update()
        )
        row = member_result.scalar_one_or_none()
        if not row:
            fail(ErrorCode.INVITE_NOT_FOUND)

        row.status = MEMBER_STATUS_REMOVED
        row.updated_at = now_cst_naive()
        await db.flush()
