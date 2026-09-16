"""协作项目算力消耗管控与生成审批服务。

管理协作者每日/总消耗上限、高额生成审批阈值，
以及待审批任务的查询、批准（预扣创建者算力）与拒绝。
"""

from __future__ import annotations

from ..core.entity_ids import parse_entity_id, require_entity_id
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from ..core.credit_amount import normalize_credit_amount
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..core.job_status import JobStatus
from ..models.job import GenerationJob
from ..models.project import Project
from ..models.user import User
from .credit_flow import credits_enabled, get_user_credit_balance, reserve_for_job
from .credit_operation_lock import generation_submit_lock
from .credit_pricing import CreditQuote, quote_generation_cost
from .project_access import require_project_owner


_SPEND_STATUSES = (
    JobStatus.AWAITING_APPROVAL.value,
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
    JobStatus.POLLING.value,
    JobStatus.SUCCEEDED.value,
)


def _cst_day_start() -> datetime:
    now = now_cst_naive()
    return datetime(now.year, now.month, now.day)


def is_collaborator_actor(actor: User, billing_user: User) -> bool:
    """判断实际操作者是否为协作者（与扣费用户非同一人）。"""
    return str(actor.id) != str(billing_user.id)


@dataclass(frozen=True, slots=True)
class CollaborationSpendSnapshot:
    """项目协作消耗与审批状态的只读快照。"""

    daily_cap: int | None
    total_cap: int | None
    approval_threshold: int | None
    daily_spent: float
    total_spent: float
    pending_approval_count: int


async def sum_collaborator_spend(
    db: AsyncSession,
    project_id: int,
    owner_id: int,
    *,
    daily_only: bool = False,
) -> float:
    """汇总协作者在指定项目上的算力消耗（含待审批、进行中及已成功任务）。"""
    query = (
        select(func.coalesce(func.sum(GenerationJob.credit_cost), 0))
        .where(
            GenerationJob.project_id == project_id,
            GenerationJob.actor_user_id.isnot(None),
            GenerationJob.actor_user_id != owner_id,
            GenerationJob.status.in_(_SPEND_STATUSES),
        )
    )
    if daily_only:
        query = query.where(GenerationJob.created_at >= _cst_day_start())
    result = await db.execute(query)
    return normalize_credit_amount(result.scalar_one() or 0)


async def count_pending_approvals(db: AsyncSession, project_id: int) -> int:
    """统计项目内处于 ``awaiting_approval`` 状态的生成任务数。"""
    result = await db.execute(
        select(func.count())
        .select_from(GenerationJob)
        .where(
            GenerationJob.project_id == project_id,
            GenerationJob.status == JobStatus.AWAITING_APPROVAL.value,
        )
    )
    return int(result.scalar_one() or 0)


async def get_collaboration_spend_snapshot(
    db: AsyncSession,
    project: Project,
) -> CollaborationSpendSnapshot:
    """获取项目协作消耗上限、已消耗额及待审批数量的快照。"""
    owner_id = project.owner_id
    daily_spent = await sum_collaborator_spend(db, project.id, owner_id, daily_only=True)
    total_spent = await sum_collaborator_spend(db, project.id, owner_id, daily_only=False)
    pending = await count_pending_approvals(db, project.id)
    return CollaborationSpendSnapshot(
        daily_cap=project.collaborator_daily_cap,
        total_cap=project.collaborator_total_cap,
        approval_threshold=project.collaborator_approval_threshold,
        daily_spent=daily_spent,
        total_spent=total_spent,
        pending_approval_count=pending,
    )


async def assert_collaborator_spend_allowed(
    db: AsyncSession,
    *,
    project: Project,
    actor: User,
    billing_user: User,
    amount: float,
) -> None:
    """校验协作者本次消耗未超出项目每日/总上限，超限则抛出业务错误。"""
    if not is_collaborator_actor(actor, billing_user) or amount <= 0:
        return

    amount = normalize_credit_amount(amount)
    snapshot = await get_collaboration_spend_snapshot(db, project)
    if snapshot.daily_cap is not None and snapshot.daily_spent + amount > snapshot.daily_cap:
        fail(
            ErrorCode.COLLABORATOR_DAILY_CAP_EXCEEDED,
            message="已超过本项目今日协作消耗上限",
            content={
                "dailyCap": snapshot.daily_cap,
                "dailySpent": snapshot.daily_spent,
                "required": amount,
            },
        )
    if snapshot.total_cap is not None and snapshot.total_spent + amount > snapshot.total_cap:
        fail(
            ErrorCode.COLLABORATOR_TOTAL_CAP_EXCEEDED,
            message="已超过本项目协作消耗总上限",
            content={
                "totalCap": snapshot.total_cap,
                "totalSpent": snapshot.total_spent,
                "required": amount,
            },
        )


def collaborator_needs_approval(
    project: Project,
    *,
    actor: User,
    billing_user: User,
    amount: float,
) -> bool:
    """判断协作者本次生成是否需创建者审批（按项目审批阈值）。"""
    if not is_collaborator_actor(actor, billing_user):
        return False
    amount = normalize_credit_amount(amount)
    if amount <= 0:
        return False
    threshold = project.collaborator_approval_threshold
    if threshold is None or normalize_credit_amount(threshold) <= 0:
        return False
    return amount >= normalize_credit_amount(threshold)


async def list_pending_approval_jobs(
    db: AsyncSession,
    project_id: int,
) -> list[GenerationJob]:
    """列出项目内所有待审批的生成任务（按创建时间升序）。"""
    result = await db.execute(
        select(GenerationJob)
        .where(
            GenerationJob.project_id == project_id,
            GenerationJob.status == JobStatus.AWAITING_APPROVAL.value,
        )
        .order_by(GenerationJob.created_at.asc())
    )
    return list(result.scalars().all())


def _approval_row(
    job: GenerationJob,
    actor_name: str,
    *,
    model_display_name: str | None = None,
) -> dict[str, Any]:
    params = job.input_params if isinstance(job.input_params, dict) else {}
    return {
        "jobId": str(job.id),
        "nodeId": job.node_id,
        "actorUserId": str(job.actor_user_id) if job.actor_user_id else None,
        "actorDisplayName": actor_name,
        "model": job.model,
        "modelDisplayName": model_display_name,
        "category": params.get("category"),
        "creditCost": normalize_credit_amount(job.credit_cost, default=0),
        "createdAt": job.created_at,
    }


async def list_pending_approvals_out(
    db: AsyncSession,
    project: Project,
) -> list[dict[str, Any]]:
    """将待审批任务序列化为 API 响应结构（含协作者显示名）。"""
    jobs = await list_pending_approval_jobs(db, project.id)
    if not jobs:
        return []
    actor_ids = {job.actor_user_id for job in jobs if job.actor_user_id}
    names: dict[str, str] = {}
    if actor_ids:
        from ..models.user import User as UserModel

        rows = await db.execute(select(UserModel).where(UserModel.id.in_(actor_ids)))
        for user in rows.scalars().all():
            names[str(user.id)] = user.display_name or ""
    model_names = {str(job.model).strip() for job in jobs if job.model}
    from .credit_transaction_query import map_model_display_names

    display_map = (
        await map_model_display_names(db, model_names, fallback_to_internal=False)
        if model_names
        else {}
    )
    return [
        _approval_row(
            job,
            names.get(str(job.actor_user_id), ""),
            model_display_name=display_map.get(str(job.model or "").strip()) or None,
        )
        for job in jobs
    ]


async def approve_collaborator_generation(
    db: AsyncSession,
    *,
    owner: User,
    project_id: str,
    job_id: str,
) -> GenerationJob:
    """创建者批准协作者生成申请：校验余额与消耗上限后预扣算力并入队。"""
    project = await require_project_owner(db, owner, project_id)
    billing_user = owner

    job_id_int = require_entity_id(str(job_id).strip(), message="任务 ID 无效")

    result = await db.execute(
        select(GenerationJob)
        .where(GenerationJob.id == job_id_int, GenerationJob.project_id == project.id)
        .with_for_update()
    )
    job = result.scalar_one_or_none()
    if not job:
        fail(ErrorCode.JOB_NOT_FOUND)
    if job.status != JobStatus.AWAITING_APPROVAL.value:
        fail(ErrorCode.GENERATION_NOT_AWAITING_APPROVAL)

    amount = normalize_credit_amount(job.credit_cost, default=0)
    if credits_enabled() and amount > 0:
        balance = await get_user_credit_balance(billing_user, db, model_name=job.model)
        if balance is not None and balance < amount:
            fail(
                ErrorCode.INSUFFICIENT_CREDITS,
                message=f"算力不足，批准需要 {amount}，当前可用 {balance}",
                content={"required": amount, "available": balance},
            )

    if amount > 0:
        actor_result = await db.execute(select(User).where(User.id == job.actor_user_id))
        actor = actor_result.scalar_one_or_none()
        if actor:
            await assert_collaborator_spend_allowed(
                db,
                project=project,
                actor=actor,
                billing_user=billing_user,
                amount=amount,
            )

    quote = CreditQuote(
        model=str(job.model or ""),
        total=amount,
        base=amount,
        breakdown=[],
        pricing_version=int(job.pricing_version or 0),
        option_snapshot=(
            job.input_params.get("optionSnapshot", {})
            if isinstance(job.input_params, dict)
            else {}
        ),
    )
    if credits_enabled() and amount > 0:
        # 协作者生成审批通过后的预扣，复用与 API 提交相同的用户级入队锁
        async with generation_submit_lock(billing_user.id):
            await reserve_for_job(
                db,
                user=billing_user,
                job=job,
                quote=quote,
                reference=f"canvas:{project_id}:{job.node_id}:approved",
            )
            from .credit_operation_lock import seal_generation_submit

            await seal_generation_submit(billing_user.id)

    job.status = JobStatus.PENDING.value
    await db.flush()
    return job


async def reject_collaborator_generation(
    db: AsyncSession,
    *,
    owner: User,
    project_id: str,
    job_id: str,
) -> GenerationJob:
    """创建者拒绝协作者生成申请，任务标记为失败且不扣费。"""
    project = await require_project_owner(db, owner, project_id)

    job_id_int = require_entity_id(str(job_id).strip(), message="任务 ID 无效")

    result = await db.execute(
        select(GenerationJob)
        .where(GenerationJob.id == job_id_int, GenerationJob.project_id == project.id)
        .with_for_update()
    )
    job = result.scalar_one_or_none()
    if not job:
        fail(ErrorCode.JOB_NOT_FOUND)
    if job.status != JobStatus.AWAITING_APPROVAL.value:
        fail(ErrorCode.GENERATION_NOT_AWAITING_APPROVAL)

    job.status = JobStatus.FAILED.value
    job.error_message = "创建者已拒绝该生成申请"
    job.credit_status = "skipped"
    await db.flush()
    return job


async def update_collaboration_settings(
    db: AsyncSession,
    *,
    owner: User,
    project_id: str,
    daily_cap: int | None = None,
    total_cap: int | None = None,
    approval_threshold: int | None = None,
    clear_daily_cap: bool = False,
    clear_total_cap: bool = False,
    clear_approval_threshold: bool = False,
) -> Project:
    """更新项目协作消耗上限与审批阈值（``0`` 或显式清除表示不限制）。"""
    project = await require_project_owner(db, owner, project_id)

    def _validate_cap(value: int | None, label: str) -> None:
        if value is None:
            return
        if value < 0:
            fail(ErrorCode.BAD_REQUEST, message=f"{label}不能为负数")

    _validate_cap(daily_cap, "每日协作消耗上限")
    _validate_cap(total_cap, "协作消耗总上限")
    _validate_cap(approval_threshold, "审批阈值")

    if clear_daily_cap:
        project.collaborator_daily_cap = None
    elif daily_cap is not None:
        project.collaborator_daily_cap = daily_cap if daily_cap > 0 else None

    if clear_total_cap:
        project.collaborator_total_cap = None
    elif total_cap is not None:
        project.collaborator_total_cap = total_cap if total_cap > 0 else None

    if clear_approval_threshold:
        project.collaborator_approval_threshold = None
    elif approval_threshold is not None:
        project.collaborator_approval_threshold = approval_threshold if approval_threshold > 0 else None

    project.updated_at = now_cst_naive()
    await db.flush()
    return project
