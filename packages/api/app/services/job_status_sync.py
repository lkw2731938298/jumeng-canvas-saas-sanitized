"""生成任务状态批量同步服务。

回收中断/超时任务，并将进行中任务与上游提供商状态对齐。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.job_status import JobStatus
from ..models.job import GenerationJob
from .job_admin_actions import _action_sync_upstream
from .stale_jobs import (
    recover_exhausted_pending_jobs,
    recover_exhausted_running_jobs,
    recover_interrupted_jobs_on_startup,
    recover_stale_running_jobs,
)
from .restart_job_recovery import (
    provider_task_id_from_job,
    recover_duplicate_claim_jobs,
)
from .worker_job_lock import is_worker_job_lock_held

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = (
    JobStatus.RUNNING.value,
    JobStatus.POLLING.value,
    JobStatus.ABNORMAL.value,
)


async def sync_active_generation_jobs(
    db: AsyncSession,
    *,
    limit: int = 100,
    include_recovery: bool = True,
) -> dict[str, Any]:
    """回收异常任务并同步含上游 task id 的运行中/轮询中任务状态。"""
    settings = get_settings()
    stale_minutes = max(25, int(settings.worker_job_timeout_s // 60))
    summary: dict[str, Any] = {
        "interruptedRecovered": 0,
        "staleRecovered": 0,
        "exhaustedFailed": 0,
        "exhaustedPendingFailed": 0,
        "duplicateReconciled": 0,
        "upstreamSynced": [],
        "upstreamSkipped": [],
        "statusCounts": {},
    }

    if include_recovery:
        summary["interruptedRecovered"] = await recover_interrupted_jobs_on_startup(db)
        summary["staleRecovered"] = await recover_stale_running_jobs(
            db,
            stale_after_minutes=stale_minutes,
        )
        summary["exhaustedFailed"] = await recover_exhausted_running_jobs(
            db,
            stale_after_minutes=stale_minutes,
        )
        summary["exhaustedPendingFailed"] = await recover_exhausted_pending_jobs(db)
        summary["duplicateReconciled"] = await recover_duplicate_claim_jobs(db)
        await db.flush()

    result = await db.execute(
        select(GenerationJob)
        .where(GenerationJob.status.in_(_ACTIVE_STATUSES))
        .order_by(GenerationJob.id.desc())
        .limit(max(1, min(limit, 200)))
    )
    jobs = list(result.scalars().all())

    for job in jobs:
        if not provider_task_id_from_job(job):
            summary["upstreamSkipped"].append(
                {
                    "jobId": job.id,
                    "status": job.status,
                    "reason": "no_upstream_task_id",
                }
            )
            continue
        # Worker 正在执行时跳过：由其落库，避免双下载出「管理员同步上游产物」重复资产
        if await is_worker_job_lock_held(job.id):
            summary["upstreamSkipped"].append(
                {
                    "jobId": job.id,
                    "status": job.status,
                    "reason": "worker_executing",
                }
            )
            continue
        try:
            payload = await _action_sync_upstream(
                db,
                job,
                "系统自动同步上游任务状态",
            )
            summary["upstreamSynced"].append(
                {
                    "jobId": job.id,
                    "status": job.status,
                    **payload,
                }
            )
        except Exception as exc:
            logger.warning("Upstream sync failed for job %s: %s", job.id, exc)
            summary["upstreamSynced"].append(
                {
                    "jobId": job.id,
                    "status": job.status,
                    "error": str(exc)[:300],
                }
            )

    rows = await db.execute(
        select(GenerationJob.status, func.count()).group_by(GenerationJob.status)
    )
    summary["statusCounts"] = {status: int(count) for status, count in rows.all()}
    return summary
