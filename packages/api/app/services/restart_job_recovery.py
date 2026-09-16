"""重启后任务恢复：只读 provider_task_id；有则查上游；无 id 有锁则停车等管理员；不清 Redis 锁。"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import cst_iso_now, now_cst_naive
from ..core.entity_ids import parse_entity_id
from ..core.job_status import JobStatus
from ..models.database import async_session
from ..models.job import GenerationJob
from .credit_operation_lock import model_submit_lock_key
from .generation_jobs import extract_asset_id, job_is_execution_timeout_parked
from .job_admin_actions import _action_sync_upstream
from .redis_client import get_redis
from .worker_job_lock import worker_execute_lock_key

logger = logging.getLogger(__name__)

RESTART_LOCK_WITHOUT_TASK_MESSAGE = "服务重启后任务状态不明，请联系管理员处理"
ATTEMPTS_EXHAUSTED_MESSAGE = "任务重试次数已用尽，请联系管理员处理"


def provider_task_id_from_job(job: GenerationJob) -> str | None:
    """仅读 DB 列 provider_task_id（重启对账不以兼容字段为准）。"""
    val = str(job.provider_task_id or "").strip()
    return val or None


def job_is_upstream_awaiting_admin(job: GenerationJob) -> bool:
    trace = job.trace_json if isinstance(job.trace_json, dict) else {}
    parked = trace.get("upstreamAwaitingAdmin")
    return isinstance(parked, dict) and bool(parked.get("awaitingAdmin"))


def apply_upstream_awaiting_admin_park(
    job: GenerationJob,
    *,
    error_message: str,
    reason: str = "upstream_in_flight",
) -> None:
    """停车等管理员：polling、预扣不动、Worker 不再 claim。"""
    job.status = JobStatus.POLLING.value
    job.error_message = error_message[:2000]
    job.worker_claim_id = None
    job.completed_at = None
    job.started_at = None
    trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
    trace["upstreamAwaitingAdmin"] = {
        "awaitingAdmin": True,
        "reason": reason,
        "detail": error_message[:500],
        "parkedAt": cst_iso_now(),
        "providerTaskId": provider_task_id_from_job(job),
    }
    job.trace_json = trace


def admin_parked_sql_clause(*, trace_col: str = "trace_json") -> str:
    """SQL 片段：排除已停车等管理员的任务。"""
    return f"""(
  {trace_col} IS NULL
  OR IFNULL(JSON_UNQUOTE(JSON_EXTRACT({trace_col}, '$.upstreamAwaitingAdmin.awaitingAdmin')), 'false') != 'true'
)"""


def _job_has_registered_output(job: GenerationJob) -> bool:
    if job.asset_id:
        return True
    return bool(extract_asset_id(job.output_assets))


async def is_worker_execute_lock_held(job_id: int | str) -> bool:
    """只读 EXISTS，不删除 lock:worker:execute。"""
    client = await get_redis()
    if client is None:
        return False
    key = worker_execute_lock_key(job_id)
    return bool(await client.exists(key))


async def is_upstream_submit_lock_held(job_id: int | str) -> bool:
    """只读 EXISTS，不删除 lock:model:submit。"""
    client = await get_redis()
    if client is None:
        return False
    return bool(await client.exists(model_submit_lock_key(int(job_id))))


async def restart_uncertain_lock_active(job_id: int | str) -> bool:
    """无 provider_task_id 时：execute 或 submit 锁仍在 → 状态不明，等管理员。"""
    return await is_worker_execute_lock_held(job_id) or await is_upstream_submit_lock_held(job_id)


def apply_attempts_exhausted_park(
    job: GenerationJob,
    *,
    error_message: str | None = None,
) -> None:
    """重试次数用尽：停车等管理员，不标记失败、不退算力。"""
    apply_upstream_awaiting_admin_park(
        job,
        error_message=error_message or ATTEMPTS_EXHAUSTED_MESSAGE,
        reason="attempts_exhausted",
    )


async def park_exhausted_job(
    db: AsyncSession,
    job: GenerationJob,
    *,
    note: str,
) -> None:
    """重试用尽：有 provider_task_id 时再查一次上游，然后停车。"""
    if provider_task_id_from_job(job):
        try:
            await _action_sync_upstream(db, job, note)
        except Exception as exc:
            logger.warning("Upstream sync before attempts-exhausted park failed for job %s: %s", job.id, exc)
    apply_attempts_exhausted_park(job)
    await db.flush()


async def reconcile_job_after_restart(
    db: AsyncSession,
    job: GenerationJob,
    *,
    note: str,
) -> str:
    """重启/撞锁后的单任务恢复。返回 already_succeeded | synced | parked | requeued。"""
    if job_is_execution_timeout_parked(job) or job_is_upstream_awaiting_admin(job):
        return "skipped"

    attempts_exhausted = int(job.attempt_count or 0) >= int(job.max_attempts or 3)

    if _job_has_registered_output(job):
        job.status = JobStatus.SUCCEEDED.value
        job.error_message = None
        job.worker_claim_id = None
        job.completed_at = job.completed_at or now_cst_naive()
        await db.flush()
        return "already_succeeded"

    task_id = provider_task_id_from_job(job)
    job.worker_claim_id = None
    job.started_at = None

    if task_id:
        job.status = JobStatus.POLLING.value
        job.error_message = None
        await db.flush()
        try:
            await _action_sync_upstream(db, job, note)
        except Exception as exc:
            logger.warning("Upstream sync after restart failed for job %s: %s", job.id, exc)
        if attempts_exhausted:
            apply_attempts_exhausted_park(job)
            await db.flush()
            return "parked"
        return "synced"

    if await restart_uncertain_lock_active(int(job.id)):
        apply_upstream_awaiting_admin_park(
            job,
            error_message=RESTART_LOCK_WITHOUT_TASK_MESSAGE,
            reason="restart_lock_without_task_id",
        )
        await db.flush()
        return "parked"

    if attempts_exhausted:
        apply_attempts_exhausted_park(job)
        await db.flush()
        return "parked"

    job.status = JobStatus.PENDING.value
    job.error_message = None
    await db.flush()
    return "requeued"


async def revert_worker_claim_conflict(job_id: str, claim_token: str, *, note: str) -> None:
    """Worker 撞 execute 锁或进程内重复认领：对账恢复，不写 DUPLICATE_IN_PROCESS_CLAIM。"""
    async with async_session() as session:
        job = await session.get(GenerationJob, parse_entity_id(job_id))
        if not job:
            return
        if job.status != JobStatus.RUNNING.value:
            return
        if str(job.worker_claim_id or "") != claim_token:
            return
        job.attempt_count = max(int(job.attempt_count or 0) - 1, 0)
        await reconcile_job_after_restart(session, job, note=note)
        await session.commit()


async def recover_duplicate_claim_jobs(db: AsyncSession) -> int:
    """将 DUPLICATE_IN_PROCESS_CLAIM 任务按 provider_task_id / 锁状态重新对账。"""
    result = await db.execute(
        select(GenerationJob).filter(
            GenerationJob.error_message == "DUPLICATE_IN_PROCESS_CLAIM",
            or_(
                GenerationJob.status == JobStatus.PENDING.value,
                GenerationJob.status == JobStatus.POLLING.value,
                GenerationJob.status == JobStatus.RUNNING.value,
            ),
        )
    )
    jobs = list(result.scalars().all())
    if not jobs:
        return 0

    for job in jobs:
        await reconcile_job_after_restart(db, job, note="DUPLICATE 认领冲突后自动恢复")

    await db.flush()
    logger.warning("Reconciled %s DUPLICATE_IN_PROCESS_CLAIM job(s)", len(jobs))
    return len(jobs)


__all__ = [
    "ATTEMPTS_EXHAUSTED_MESSAGE",
    "RESTART_LOCK_WITHOUT_TASK_MESSAGE",
    "admin_parked_sql_clause",
    "apply_attempts_exhausted_park",
    "apply_upstream_awaiting_admin_park",
    "is_upstream_submit_lock_held",
    "is_worker_execute_lock_held",
    "job_is_upstream_awaiting_admin",
    "park_exhausted_job",
    "provider_task_id_from_job",
    "reconcile_job_after_restart",
    "recover_duplicate_claim_jobs",
    "restart_uncertain_lock_active",
    "revert_worker_claim_conflict",
]
