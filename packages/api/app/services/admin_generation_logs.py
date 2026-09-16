"""管理端生成调用日志查询与导出服务。

基于 ``generation_call_logs`` 表提供筛选、分页、行组装及历史数据清理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.generation_call_log import GenerationCallLog
from ..models.project import Project
from ..models.user import User


@dataclass(frozen=True, slots=True)
class AdminGenerationLogFilters:
    """管理端生成调用日志列表的筛选与分页参数。"""

    page: int = 1
    page_size: int = 20
    job_id: int | None = None
    project_id: int | None = None
    actor_user_id: int | None = None
    model: str | None = None
    category: str | None = None
    phase: str | None = None
    submit_source: str | None = None
    outcome: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


def parse_admin_generation_log_filters(
    *,
    page: int,
    page_size: int,
    job_id: int | None,
    project_id: str | None,
    actor_user_id: str | None,
    model: str | None,
    category: str | None,
    phase: str | None,
    submit_source: str | None,
    outcome: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> AdminGenerationLogFilters:
    """将 API 查询参数解析并规范化为 ``AdminGenerationLogFilters``。"""
    from ..core.entity_ids import parse_entity_id

    def _pid(value: str | None) -> int | None:
        if not value or not str(value).strip():
            return None
        return parse_entity_id(value.strip())

    return AdminGenerationLogFilters(
        page=max(1, page),
        page_size=min(max(1, page_size), 100),
        job_id=job_id,
        project_id=_pid(project_id),
        actor_user_id=_pid(actor_user_id),
        model=(model or "").strip() or None,
        category=(category or "").strip() or None,
        phase=(phase or "").strip() or None,
        submit_source=(submit_source or "").strip() or None,
        outcome=(outcome or "").strip() or None,
        created_from=created_from,
        created_to=created_to,
    )


def apply_admin_generation_log_filters(
    stmt: Select[Any],
    filters: AdminGenerationLogFilters,
) -> Select[Any]:
    """将筛选条件应用到 SQLAlchemy 查询语句。"""
    if filters.job_id is not None:
        stmt = stmt.where(GenerationCallLog.job_id == filters.job_id)
    if filters.project_id is not None:
        stmt = stmt.where(GenerationCallLog.project_id == filters.project_id)
    if filters.actor_user_id is not None:
        stmt = stmt.where(GenerationCallLog.actor_user_id == filters.actor_user_id)
    if filters.model:
        stmt = stmt.where(GenerationCallLog.model == filters.model)
    if filters.category:
        stmt = stmt.where(GenerationCallLog.category == filters.category)
    if filters.phase:
        stmt = stmt.where(GenerationCallLog.phase == filters.phase)
    if filters.submit_source:
        stmt = stmt.where(GenerationCallLog.submit_source == filters.submit_source)
    if filters.outcome:
        stmt = stmt.where(GenerationCallLog.outcome == filters.outcome)
    if filters.created_from is not None:
        stmt = stmt.where(GenerationCallLog.created_at >= filters.created_from)
    if filters.created_to is not None:
        stmt = stmt.where(GenerationCallLog.created_at < filters.created_to)
    return stmt


async def list_admin_generation_logs(
    db: AsyncSession,
    filters: AdminGenerationLogFilters,
) -> tuple[list[GenerationCallLog], int]:
    """分页查询生成调用日志，返回记录列表与总数。"""
    base = select(GenerationCallLog)
    base = apply_admin_generation_log_filters(base, filters)
    count_stmt = select(func.count()).select_from(base.subquery())
    total = int((await db.execute(count_stmt)).scalar_one() or 0)
    offset = (filters.page - 1) * filters.page_size
    rows_stmt = (
        apply_admin_generation_log_filters(select(GenerationCallLog), filters)
        .order_by(GenerationCallLog.id.desc())
        .offset(offset)
        .limit(filters.page_size)
    )
    result = await db.execute(rows_stmt)
    return list(result.scalars().all()), total


async def purge_generation_call_logs_before(
    db: AsyncSession,
    cutoff: datetime,
) -> int:
    """删除指定截止时间之前的生成调用日志，返回删除行数。"""
    from sqlalchemy import delete

    result = await db.execute(
        delete(GenerationCallLog).where(GenerationCallLog.created_at < cutoff)
    )
    return int(result.rowcount or 0)


def build_admin_generation_log_row(
    row: GenerationCallLog,
    *,
    users: dict[int, User],
    projects: dict[int, Project],
) -> dict[str, Any]:
    """将单条日志 ORM 记录组装为管理端 API 响应字典。"""
    actor = users.get(int(row.actor_user_id)) if row.actor_user_id else None
    billing = users.get(int(row.billing_user_id)) if row.billing_user_id else None
    project = projects.get(int(row.project_id)) if row.project_id else None
    return {
        "id": str(row.id),
        "jobId": str(row.job_id) if row.job_id is not None else None,
        "phase": row.phase,
        "submitSource": row.submit_source,
        "lane": row.lane,
        "category": row.category,
        "projectId": str(row.project_id) if row.project_id is not None else None,
        "projectTitle": project.title if project else None,
        "nodeId": row.node_id,
        "workflowId": row.workflow_id,
        "actorUserId": str(row.actor_user_id) if row.actor_user_id is not None else None,
        "actorDisplayName": actor.display_name if actor else None,
        "billingUserId": str(row.billing_user_id) if row.billing_user_id is not None else None,
        "billingDisplayName": billing.display_name if billing else None,
        "model": row.model,
        "outcome": row.outcome,
        "requestPayload": row.request_payload,
        "responsePayload": row.response_payload,
        "errorMessage": row.error_message,
        "idempotencyKey": row.idempotency_key,
        "dedupeKey": row.dedupe_key,
        "createdAt": row.created_at,
    }


async def load_users_by_ids(db: AsyncSession, ids: set[int]) -> dict[int, User]:
    """批量按 ID 加载用户，返回 ``id -> User`` 映射。"""
    if not ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(ids)))
    return {int(user.id): user for user in result.scalars().all()}


async def load_projects_by_ids(db: AsyncSession, ids: set[int]) -> dict[int, Project]:
    """批量按 ID 加载项目，返回 ``id -> Project`` 映射。"""
    if not ids:
        return {}
    result = await db.execute(select(Project).where(Project.id.in_(ids)))
    return {int(project.id): project for project in result.scalars().all()}


async def build_admin_generation_log_rows(
    db: AsyncSession,
    rows: list[GenerationCallLog],
) -> list[dict[str, Any]]:
    """批量组装管理端日志行（自动关联用户与项目显示名）。"""
    user_ids: set[int] = set()
    project_ids: set[int] = set()
    for row in rows:
        if row.actor_user_id:
            user_ids.add(int(row.actor_user_id))
        if row.billing_user_id:
            user_ids.add(int(row.billing_user_id))
        if row.project_id:
            project_ids.add(int(row.project_id))
    users = await load_users_by_ids(db, user_ids)
    projects = await load_projects_by_ids(db, project_ids)
    return [
        build_admin_generation_log_row(row, users=users, projects=projects)
        for row in rows
    ]
