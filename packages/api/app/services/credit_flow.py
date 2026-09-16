"""画布生成任务的算力预扣 / 结算 / 释放（本地账本）。

统一封装 reserve→commit→release 三段式流程，是生成任务与算力账本的对接层。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_status import CreditStatus
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.database import async_session
from ..models.credit import CreditReservation
from ..models.job import GenerationJob
from ..models.user import User
from .credit_operation_lock import (
    claim_credit_refund,
    is_credit_refund_in_progress,
    seal_credit_refund,
)
from .credit_pricing import CreditQuote, quote_generation_cost, credits_enabled as pricing_credits_enabled
from .credit_transactions import record_job_consume_transaction, record_job_refund_transaction
from .local_credits import (
    adjust_local_credits,
    commit_local_credits,
    get_local_balance,
    release_local_credits,
    reserve_local_credits,
)

logger = logging.getLogger(__name__)


def credits_enabled() -> bool:
    """算力功能全局开关是否开启（关闭时不预扣/结算）。"""
    return pricing_credits_enabled()


def model_credit_cost(catalog_model, generation_options: dict[str, str] | None = None) -> float:
    """按模型与生成选项返回本次生成应扣算力总额（唯一计价入口的封装）。"""
    return quote_generation_cost(catalog_model, generation_options).total


async def get_user_credit_balance(
    user: User,
    db: AsyncSession | None = None,
    *,
    model_name: str | None = None,
) -> float | None:
    """查询用户当前可用算力：传入 db 时按 lot 精确汇总，否则回退用户缓存值。"""
    if not credits_enabled():
        return None
    if db is not None:
        return await get_local_balance(db, user, model_name=model_name)
    from ..core.credit_amount import normalize_credit_amount

    return normalize_credit_amount(user.compute_power or 0)


@asynccontextmanager
async def _credit_db(db: AsyncSession | None):
    """复用传入 session 或新建独立 session（新建时自动 commit/rollback）。"""
    if db is not None:
        yield db
        return
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def reserve_for_job(
    db: AsyncSession,
    *,
    user: User,
    job: GenerationJob,
    quote: CreditQuote,
    reference: str,
) -> None:
    """为任务预扣算力：按报价从 lot 扣减并写入 reservation（幂等键 canvas-job-{id}）。"""
    amount = quote.total
    job.credit_cost = amount
    job.pricing_version = quote.pricing_version
    job.credit_breakdown = [item.to_dict() for item in quote.breakdown]

    if amount <= 0 or not credits_enabled():
        job.credit_status = CreditStatus.SKIPPED.value
        return

    idempotency_key = f"canvas-job-{job.id}"
    result = await reserve_local_credits(
        db,
        user_id=user.id,
        amount=amount,
        idempotency_key=idempotency_key,
        reference=reference,
        job_id=job.id,
        model_name=job.model,
    )
    if not result or not result.get("reservation_id"):
        fail(ErrorCode.CREDIT_RESERVE_FAILED)

    job.credit_reservation_id = str(result["reservation_id"])
    job.credit_status = CreditStatus.RESERVED.value
    if result.get("created") and amount > 0:
        balance_after = await get_local_balance(db, user)
        await record_job_consume_transaction(
            db,
            user=user,
            job=job,
            balance_after=balance_after,
            idempotency_key=idempotency_key,
        )
    await db.flush()


async def commit_for_job(job: GenerationJob, db: AsyncSession | None = None) -> bool:
    """任务成功后结算预扣：将 reservation 转为已提交（lot 在预扣时已扣，不再动余额）。"""
    reservation_id = job.credit_reservation_id
    if not reservation_id or not credits_enabled():
        if job.credit_status != CreditStatus.SKIPPED.value:
            job.credit_status = (
                CreditStatus.COMMITTED.value
                if (job.credit_cost or 0) <= 0
                else job.credit_status
            )
        return True

    async with _credit_db(db) as session:
        ok = await commit_local_credits(session, reservation_id)
    job.credit_status = CreditStatus.COMMITTED.value if ok else CreditStatus.COMMIT_PENDING.value
    if not ok:
        logger.error("Credit commit pending for job %s reservation %s", job.id, reservation_id)
    return ok


def credit_commit_succeeded(job: GenerationJob) -> bool:
    """判断任务算力是否已成功结算（免扣费/零额度视为成功）。"""
    if not credits_enabled():
        return True
    if (job.credit_cost or 0) <= 0 or job.credit_status == CreditStatus.SKIPPED.value:
        return True
    return job.credit_status == CreditStatus.COMMITTED.value


def job_has_upstream_in_flight(job: GenerationJob) -> bool:
    """判断任务是否已有在途上游任务（有则默认不退款，避免误退已产生费用的任务）。"""
    for value in (
        job.provider_task_id,
        job.provider_job_id,
        job.processing_id,
        job.upstream_job_id,
    ):
        if value and str(value).strip():
            return True
    return False


async def _load_reservation_idempotency_key(
    db: AsyncSession, reservation_id: str
) -> str | None:
    """读取预扣行的幂等键，供退流水 reference_key 与 -rq{N} 对齐。"""
    from ..core.entity_ids import parse_entity_id

    rid = parse_entity_id(reservation_id)
    if rid is None:
        return None
    row = await db.execute(
        select(CreditReservation.idempotency_key).filter(CreditReservation.id == rid)
    )
    return row.scalar_one_or_none()


async def _release_reserved_credits(job: GenerationJob, db: AsyncSession | None) -> bool:
    """按 reservation 原路退 lot → 改 reservation → 写退流水（持 DB 行锁）。"""
    reservation_id = job.credit_reservation_id
    if not reservation_id or not credits_enabled():
        if job.credit_status in (
            CreditStatus.RESERVED.value,
            CreditStatus.COMMIT_PENDING.value,
        ):
            job.credit_status = CreditStatus.RELEASED.value
        return True
    if job.credit_status == CreditStatus.COMMITTED.value:
        return False

    async with _credit_db(db) as session:
        idempotency_key = await _load_reservation_idempotency_key(session, reservation_id)
        ok = await release_local_credits(session, reservation_id)
        if ok and (job.credit_cost or 0) > 0:
            user = (
                await session.execute(select(User).filter(User.id == job.user_id))
            ).scalar_one_or_none()
            if user:
                balance = await get_local_balance(session, user)
                await record_job_refund_transaction(
                    session,
                    user=user,
                    job=job,
                    balance_after=balance,
                    idempotency_key=idempotency_key,
                )
    job.credit_status = CreditStatus.RELEASED.value if ok else CreditStatus.RELEASE_PENDING.value
    if not ok:
        logger.error("Credit release pending for job %s reservation %s", job.id, reservation_id)
    return ok


async def refund_job_credits(
    job: GenerationJob,
    db: AsyncSession | None = None,
    *,
    force: bool = False,
) -> bool:
    """退还任务预扣算力：Redis 标记锁 fail-closed → 退 lot → 改 reservation → 退流水 → seal。"""
    if not force and job_has_upstream_in_flight(job):
        logger.warning(
            "Skipping credit refund for job %s — upstream in flight (task_id=%s job_id=%s)",
            job.id,
            job.provider_task_id,
            job.provider_job_id or job.processing_id,
        )
        return False

    job_id = int(job.id)
    # 已 seal 的退款标记：若 DB 已 released 则幂等成功
    if await is_credit_refund_in_progress(job_id):
        if job.credit_status == CreditStatus.RELEASED.value:
            return True
        logger.info("Credit refund lock held for job %s — treating as in progress", job.id)
        return False

    if not await claim_credit_refund(job_id):
        if job.credit_status == CreditStatus.RELEASED.value:
            return True
        logger.info("Credit refund claim busy for job %s — skipping duplicate refund", job.id)
        return False

    try:
        ok = await _release_reserved_credits(job, db)
        if ok:
            await seal_credit_refund(job_id)
        return ok
    except Exception:
        # 失败路径禁止 clear 标记，防 Worker 自动重试双退
        raise


async def fail_job_and_refund(
    db: AsyncSession,
    job: GenerationJob,
    error_message: str,
    *,
    error_code: str | None = None,
    force: bool = False,
) -> bool:
    """将任务标记失败并经统一退款路径退还预扣算力。"""
    from .generation_jobs import mark_job_failed

    await mark_job_failed(db, job, error_message, error_code=error_code)
    return await refund_job_credits(job, db, force=force)


# 向后兼容别名 —— 新代码应优先使用 refund_job_credits / fail_job_and_refund。
async def release_for_job(job: GenerationJob, db: AsyncSession | None = None) -> None:
    """兼容旧接口：强制释放任务预扣算力。"""
    await refund_job_credits(job, db, force=True)


async def maybe_release_for_job(
    job: GenerationJob,
    db: AsyncSession | None = None,
    *,
    force: bool = False,
) -> bool:
    """兼容旧接口：按需释放任务预扣算力（尊重在途保护）。"""
    return await refund_job_credits(job, db, force=force)


async def adjust_user_credits(
    db: AsyncSession,
    *,
    user: User,
    delta: float,
    credit_type: str | None = None,
    model_name: str | None = None,
    valid_days: int | None = None,
    source: str = "admin_adjust",
    source_ref: str | None = None,
) -> float:
    """管理员/系统调整用户算力（默认入账通用算力），返回调整后余额。"""
    from ..core.credit_types import CREDIT_TYPE_GENERAL

    return await adjust_local_credits(
        db,
        user_id=user.id,
        delta=delta,
        credit_type=credit_type or CREDIT_TYPE_GENERAL,
        model_name=model_name,
        valid_days=valid_days,
        source=source,
        source_ref=source_ref,
    )
