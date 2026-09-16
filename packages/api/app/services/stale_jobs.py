"""恢复 API 重启或客户端断连后仍停留在 running 的生成任务。"""

from __future__ import annotations

from ..core.datetime_util import cst_iso_now, now_cst_naive
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.job_status import JobStatus
from ..models.job import GenerationJob
from .generation_jobs import extract_asset_id, job_is_execution_timeout_parked
from .restart_job_recovery import (
    apply_attempts_exhausted_park,
    job_is_upstream_awaiting_admin,
    park_exhausted_job,
    provider_task_id_from_job,
    reconcile_job_after_restart,
)

logger = logging.getLogger(__name__)

DEFAULT_STALE_AFTER_MINUTES = 30


def _job_has_registered_output(job: GenerationJob) -> bool:
    if job.asset_id:
        return True
    return bool(extract_asset_id(job.output_assets))


def _requeue_or_fail(job: GenerationJob, *, message: str, now: datetime) -> str:
    """stale/启动自动恢复：仅重置执行状态，绝不清空 provider_task_id 等上游标记。

    保留上游标记后，Worker 重跑会命中 _resume_media_job_from_upstream 只续轮询，
    从而保证同一 job 不会向上游二次提交（不重复扣费）。
    """
    if _job_has_registered_output(job):
        job.status = JobStatus.SUCCEEDED.value
        job.error_message = None
        job.completed_at = job.completed_at or now
        job.worker_claim_id = None
        return "already_succeeded"

    max_attempts = int(job.max_attempts or 3)
    attempt_count = int(job.attempt_count or 0)
    if attempt_count < max_attempts:
        if provider_task_id_from_job(job):
            job.status = JobStatus.POLLING.value
        else:
            job.status = JobStatus.PENDING.value
        job.started_at = None
        job.worker_claim_id = None
        job.error_message = None
        return "requeued"
    apply_attempts_exhausted_park(job, error_message="任务重试次数已用尽，请联系管理员处理")
    job.worker_claim_id = None
    job.started_at = None
    return "parked"


async def recover_exhausted_pending_jobs(db: AsyncSession) -> int:
    """重试已用尽但仍为 pending 的任务：停车等管理员，不失败不退款。"""
    result = await db.execute(
        select(GenerationJob).filter(
            GenerationJob.status == JobStatus.PENDING.value,
            GenerationJob.attempt_count >= GenerationJob.max_attempts,
        )
    )
    jobs = result.scalars().all()
    if not jobs:
        return 0

    for job in jobs:
        if job_is_execution_timeout_parked(job) or job_is_upstream_awaiting_admin(job):
            continue
        await park_exhausted_job(
            db,
            job,
            note="重试次数已用尽，自动同步上游后停车",
        )

    await db.flush()
    logger.warning("Parked %s exhausted pending generation job(s) for admin", len(jobs))
    return len(jobs)


async def recover_exhausted_running_jobs(
    db: AsyncSession,
    *,
    stale_after_minutes: int = 35,
) -> int:
    """超时且重试已用尽的 running 任务：停车等管理员，不失败不退款。"""
    cutoff = now_cst_naive() - timedelta(minutes=stale_after_minutes)
    result = await db.execute(
        select(GenerationJob).filter(
            GenerationJob.status == JobStatus.RUNNING.value,
            GenerationJob.attempt_count >= GenerationJob.max_attempts,
            GenerationJob.started_at.is_not(None),
            GenerationJob.started_at < cutoff,
        )
    )
    jobs = result.scalars().all()
    if not jobs:
        return 0

    parked = 0
    for job in jobs:
        if job_is_execution_timeout_parked(job) or job_is_upstream_awaiting_admin(job):
            continue
        await park_exhausted_job(
            db,
            job,
            note="重试次数已用尽，自动同步上游后停车",
        )
        parked += 1

    await db.flush()
    if parked:
        logger.warning("Parked %s exhausted running generation job(s) for admin", parked)
    return parked


async def recover_interrupted_jobs_on_startup(db: AsyncSession) -> int:
    """重启后将 running 任务对账：有 provider_task_id 查上游；无 id 有锁则停车。"""
    result = await db.execute(
        select(GenerationJob).filter(GenerationJob.status == JobStatus.RUNNING.value)
    )
    jobs = result.scalars().all()
    if not jobs:
        return 0

    message = "服务重启或连接中断，任务已重新对账"
    counts: dict[str, int] = {}
    for job in jobs:
        if job_is_execution_timeout_parked(job):
            continue
        outcome = await reconcile_job_after_restart(db, job, note=message)
        counts[outcome] = counts.get(outcome, 0) + 1

    await db.flush()
    logger.warning(
        "Startup job recovery: total=%s outcomes=%s",
        len(jobs),
        counts,
    )
    return len(jobs)


async def recover_stale_running_jobs(
    db: AsyncSession,
    *,
    stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES,
) -> int:
    """扫描超时或异常的 running 任务，重新入队或标记失败。

    已因 Worker 执行超时停车（保持 running、预扣不动）的任务跳过，等待管理员处理。
    """
    cutoff = now_cst_naive() - timedelta(minutes=stale_after_minutes)
    result = await db.execute(
        select(GenerationJob).filter(
            GenerationJob.status.in_(
                (JobStatus.RUNNING.value, JobStatus.ABNORMAL.value)
            ),
            GenerationJob.started_at.is_not(None),
            GenerationJob.started_at < cutoff,
        )
    )
    jobs = result.scalars().all()
    if not jobs:
        return 0

    message = "任务超时或服务中断，已重新入队"
    recovered = 0
    for job in jobs:
        if job_is_execution_timeout_parked(job) or job_is_upstream_awaiting_admin(job):
            continue
        outcome = await reconcile_job_after_restart(db, job, note=message)
        if outcome in ("requeued", "synced", "already_succeeded"):
            job.anomaly_reason = None
            job.anomaly_detected_at = None
            job.anomaly_detail = None
        recovered += 1

    await db.flush()
    if recovered:
        logger.warning("Recovered %s stale running generation job(s)", recovered)
    return recovered


__all__ = [
    "recover_interrupted_jobs_on_startup",
    "recover_stale_running_jobs",
    "recover_exhausted_running_jobs",
    "recover_exhausted_pending_jobs",
]
