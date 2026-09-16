"""校验当前用户是否有权访问画布项目（所有者 / 协作者）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import require_entity_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.project import Project
from ..models.project_member import MEMBER_STATUS_ACTIVE, ProjectMember
from ..models.user import User


class ProjectRole(str, Enum):
    """项目内角色：所有者或编辑者。"""

    OWNER = "owner"
    EDITOR = "editor"


@dataclass(frozen=True, slots=True)
class ProjectMembership:
    """用户在某项目中的成员关系（项目实体 + 角色）。"""

    project: Project
    role: ProjectRole


def _parse_project_id(project_id: str) -> int:
    return require_entity_id(project_id, message="项目 ID 无效")


def is_project_visible(project: Project) -> bool:
    """未软删除的项目才对用户端可见。"""
    return not bool(project.isdel)


async def resolve_project_membership(
    db: AsyncSession,
    user: User,
    project_id: str,
) -> ProjectMembership | None:
    """解析用户对项目的成员关系；无权限时返回 None（不抛错）。"""
    pid = _parse_project_id(project_id)

    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project or not is_project_visible(project):
        return None

    if project.owner_id == user.id:
        return ProjectMembership(project=project, role=ProjectRole.OWNER)

    member_result = await db.execute(
        select(ProjectMember).filter(
            ProjectMember.project_id == pid,
            ProjectMember.user_id == user.id,
            ProjectMember.status == MEMBER_STATUS_ACTIVE,
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        return None

    return ProjectMembership(project=project, role=ProjectRole.EDITOR)


async def require_project_membership(
    db: AsyncSession,
    user: User,
    project_id: str,
    *,
    min_role: ProjectRole = ProjectRole.EDITOR,
) -> ProjectMembership:
    """要求用户具备项目成员资格（可选要求所有者），否则 fail。"""
    membership = await resolve_project_membership(db, user, project_id)
    if not membership:
        fail(ErrorCode.PROJECT_NOT_FOUND)
    if min_role == ProjectRole.OWNER and membership.role != ProjectRole.OWNER:
        fail(ErrorCode.ACCESS_DENIED)
    return membership


async def require_project_access(
    db: AsyncSession,
    user: User,
    project_id: str,
) -> Project:
    """要求用户可访问项目（所有者或编辑者），返回 Project。"""
    membership = await require_project_membership(db, user, project_id)
    return membership.project


async def require_project_owner(
    db: AsyncSession,
    user: User,
    project_id: str,
) -> Project:
    """要求用户为项目所有者，否则 fail。"""
    membership = await require_project_membership(
        db, user, project_id, min_role=ProjectRole.OWNER
    )
    return membership.project


async def require_trashed_project_owner(
    db: AsyncSession,
    user: User,
    project_id: str,
) -> Project:
    """要求用户为已软删除（回收站）项目的所有者；用于恢复 / 永久删除。"""
    pid = _parse_project_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project or project.owner_id != user.id or not bool(project.isdel):
        fail(ErrorCode.PROJECT_NOT_FOUND)
    return project


async def require_workflow_project_access(
    db: AsyncSession,
    user: User,
    workflow,
) -> Project:
    """校验用户对工作流所属项目的访问权。"""
    return await require_project_access(db, user, str(workflow.project_id))


async def get_project_owner_user(db: AsyncSession, project: Project) -> User:
    """加载项目所有者 User 实体。"""
    result = await db.execute(select(User).filter(User.id == project.owner_id))
    owner = result.scalar_one_or_none()
    if not owner:
        fail(ErrorCode.PROJECT_NOT_FOUND)
    return owner
