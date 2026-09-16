"""检测并标记需要管理员关注的异常生成任务。"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
import logging
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_status import CreditStatus
from ..core.job_status import JobStatus
from ..models.job import GenerationJob
from .generation_jobs import job_is_execution_timeout_parked, mark_job_abnormal

logger = logging.getLogger(__name__)

DEFAULT_ABNORMAL_AFTER_MINUTES = 30
CREDIT_MISMATCH_AFTER_MINUTES = 15
BATCH_LIMIT = 100

ANOMALY_REASON_LABEL: dict[str, str] = {
    "STALE_RUNNING": "长时间运行中",
    "STALE_POLLING": "长时间轮询上游",
    "CREDIT_MISMATCH": "算力状态不一致",
    "UPSTREAM_UNPARSEABLE": "上游响应无法解析",
}


async def detect_stale_active_jobs(
    db: AsyncSession,
    *,
    abnormal_after_minutes: int = DEFAULT_ABNORMAL_AFTER_MINUTES,
) -> int:
    """将长时间 running/polling 的任务标记为异常（在自动重试/失败之前）。"""
    cutoff = now_cst_naive() - timedelta(minutes=abnormal_after_minutes)
    result = await db.execute(
        select(GenerationJob)
        .filter(
            GenerationJob.status.in_(
                (JobStatus.RUNNING.value, JobStatus.POLLING.value)
            ),
            GenerationJob.started_at.is_not(None),
            GenerationJob.started_at < cutoff,
        )
        .limit(BATCH_LIMIT)
    )
    jobs = result.scalars().all()
    if not jobs:
        return 0

    now = now_cst_naive()
    marked = 0
    for job in jobs:
        # 执行超时已停车为 running，不再改为 abnormal
        if job_is_execution_timeout_parked(job):
            continue
        reason = (
            "STALE_POLLING"
            if job.status == JobStatus.POLLING.value
            else "STALE_RUNNING"
        )
        label = ANOMALY_REASON_LABEL.get(reason, reason)
        await mark_job_abnormal(
            db,
            job,
            reason=reason,
            detail=f"{label}（已超过 {abnormal_after_minutes} 分钟）",
            now=now,
        )
        marked += 1

    await db.flush()
    if marked:
        logger.warning("Marked %s generation job(s) as abnormal (stale active)", marked)
    return marked


async def detect_credit_mismatch_jobs(
    db: AsyncSession,
    *,
    mismatch_after_minutes: int = CREDIT_MISMATCH_AFTER_MINUTES,
) -> int:
    """标记已终态但算力预扣未结算/未释放的任务为异常。"""
    cutoff = now_cst_naive() - timedelta(minutes=mismatch_after_minutes)
    result = await db.execute(
        select(GenerationJob)
        .filter(
            GenerationJob.status.in_(
                (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value)
            ),
            GenerationJob.credit_reservation_id.isnot(None),
            GenerationJob.completed_at.is_not(None),
            GenerationJob.completed_at < cutoff,
            or_(
                GenerationJob.credit_status == CreditStatus.RESERVED.value,
                GenerationJob.credit_status == CreditStatus.COMMIT_PENDING.value,
                GenerationJob.credit_status == CreditStatus.RELEASE_PENDING.value,
            ),
        )
        .limit(BATCH_LIMIT)
    )
    jobs = result.scalars().all()
    if not jobs:
        return 0

    now = now_cst_naive()
    for job in jobs:
        detail = (
            f"任务已{job.status}但算力状态为 {job.credit_status}，"
            f"超过 {mismatch_after_minutes} 分钟未对账"
        )
        job.anomaly_reason = "CREDIT_MISMATCH"
        job.anomaly_detected_at = now
        job.anomaly_detail = detail[:2000]
        trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
        from ..core.datetime_util import cst_iso, cst_iso_now

        trace["anomaly"] = {
            "code": "CREDIT_MISMATCH",
            "detail": detail[:500],
            "auto": True,
            "detectedAt": cst_iso(now) or cst_iso_now(),
        }
        job.trace_json = trace

    await db.flush()
    logger.warning("Marked %s generation job(s) as abnormal (credit mismatch)", len(jobs))
    return len(jobs)


async def run_job_anomaly_detection(db: AsyncSession) -> dict[str, int]:
    """运行全部异常检测规则，返回各规则命中数量。"""
    stale = await detect_stale_active_jobs(db)
    mismatch = await detect_credit_mismatch_jobs(db)
    return {"stale_active": stale, "credit_mismatch": mismatch}


__all__ = [
    "ANOMALY_REASON_LABEL",
    "DEFAULT_ABNORMAL_AFTER_MINUTES",
    "mark_job_abnormal",
    "run_job_anomaly_detection",
]
