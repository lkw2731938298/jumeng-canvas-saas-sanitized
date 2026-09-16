"""算力对账 —— 修复卡在中间态（commit_pending/release_pending）的任务预扣。"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
import logging
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_status import CreditStatus
from ..core.job_status import JobStatus
from ..models.job import GenerationJob
from .credit_flow import commit_for_job, credits_enabled, fail_job_and_refund, refund_job_credits

logger = logging.getLogger(__name__)

PENDING_STALE_HOURS = 24
BATCH_LIMIT = 50


async def reconcile_credit_reservations(db: AsyncSession) -> dict[str, int]:
    """对账：成功任务补结算、失败任务补退款、长期 pending 超时任务失败退款。"""
    if not credits_enabled():
        return {"committed": 0, "released": 0, "failed_stale": 0}

    stats = {"committed": 0, "released": 0, "failed_stale": 0}
    now = now_cst_naive()

    succeeded = await db.execute(
        select(GenerationJob)
        .filter(
            GenerationJob.status == JobStatus.SUCCEEDED.value,
            GenerationJob.credit_reservation_id.isnot(None),
            or_(
                GenerationJob.credit_status == CreditStatus.RESERVED.value,
                GenerationJob.credit_status == CreditStatus.COMMIT_PENDING.value,
            ),
        )
        .limit(BATCH_LIMIT)
    )
    for job in succeeded.scalars().all():
        await commit_for_job(job, db)
        stats["committed"] += 1

    failed = await db.execute(
        select(GenerationJob)
        .filter(
            GenerationJob.status == JobStatus.FAILED.value,
            GenerationJob.credit_reservation_id.isnot(None),
            or_(
                GenerationJob.credit_status == CreditStatus.RESERVED.value,
                GenerationJob.credit_status == CreditStatus.RELEASE_PENDING.value,
            ),
        )
        .limit(BATCH_LIMIT)
    )
    for job in failed.scalars().all():
        await refund_job_credits(job, db, force=True)
        stats["released"] += 1

    cutoff = now - timedelta(hours=PENDING_STALE_HOURS)
    stale_pending = await db.execute(
        select(GenerationJob)
        .filter(
            GenerationJob.status == JobStatus.PENDING.value,
            GenerationJob.credit_reservation_id.isnot(None),
            GenerationJob.created_at < cutoff,
        )
        .limit(BATCH_LIMIT)
    )
    for job in stale_pending.scalars().all():
        await fail_job_and_refund(
            db,
            job,
            "任务超时未执行，已释放预扣算力",
            force=True,
        )
        stats["failed_stale"] += 1
        stats["released"] += 1

    if any(stats.values()):
        await db.flush()
        logger.info("Credit reconciliation: %s", stats)

    return stats
