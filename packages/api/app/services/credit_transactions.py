"""算力流水记账 —— 将每次余额变动写入 credit_transactions 供审计。"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_tx_sources import (
    CREDIT_TX_SOURCE_JOB_CONSUME,
    CREDIT_TX_SOURCE_JOB_REFUND,
)
from ..core.credit_types import CREDIT_TYPE_GENERAL
from ..core.credit_amount import normalize_credit_amount
from ..models.credit_transaction import CreditTransaction
from ..models.job import GenerationJob
from ..models.user import User
from .credit_lots import sync_user_compute_power
from .credit_ledger_write import CreditLedgerOp, CreditLedgerWriteRequest, apply_credit_ledger_write
from .local_credits import _lock_user


def requeue_suffix_from_idempotency_key(idempotency_key: str | None) -> str | None:
    """从预扣幂等键解析重队列序号，如 canvas-job-12-rq2 → rq2。"""
    if not idempotency_key or "-rq" not in idempotency_key:
        return None
    tail = idempotency_key.rsplit("-rq", 1)[-1]
    return f"rq{tail}" if tail else None


def job_consume_reference_key(job_id: int, *, idempotency_key: str | None = None) -> str:
    """任务扣费流水引用键；重队列与 reservation 幂等键对齐。"""
    suffix = requeue_suffix_from_idempotency_key(idempotency_key)
    if suffix:
        return f"job-consume-{job_id}-{suffix}"
    return f"job-consume-{job_id}"


def job_refund_reference_key(job_id: int, *, idempotency_key: str | None = None) -> str:
    """任务退款流水引用键；与对应预扣/扣费 reference_key 区分动作。"""
    suffix = requeue_suffix_from_idempotency_key(idempotency_key)
    if suffix:
        return f"job-refund-{job_id}-{suffix}"
    return f"job-refund-{job_id}"


async def record_credit_transaction(
    db: AsyncSession,
    *,
    user_id: int,
    delta: float,
    balance_after: float,
    source: str,
    reason: str | None = None,
    credit_type: str | None = None,
    model_name: str | None = None,
    lot_id: int | None = None,
    operator_id: int | None = None,
    job_id: int | None = None,
    reference_key: str | None = None,
    created_at: datetime | None = None,
) -> CreditTransaction | None:
    """写一条算力流水；带 job 三列 UNIQUE 时 IntegrityError 视为幂等成功。"""
    if reference_key and job_id is not None:
        existing = await db.execute(
            select(CreditTransaction.id).filter(
                CreditTransaction.job_id == job_id,
                CreditTransaction.user_id == user_id,
                CreditTransaction.reference_key == reference_key,
            )
        )
        if existing.scalar_one_or_none():
            return None

    tx = CreditTransaction(
        user_id=user_id,
        operator_id=operator_id,
        delta=delta,
        balance_after=balance_after,
        source=source,
        credit_type=credit_type,
        lot_id=lot_id,
        model_name=model_name,
        job_id=job_id,
        reference_key=reference_key,
        reason=(reason or "").strip() or None,
        created_at=created_at or now_cst_naive(),
    )
    db.add(tx)
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(job_id, user_id, reference_key) 冲突 → 幂等成功
        return None
    return tx


async def record_job_consume_transaction(
    db: AsyncSession,
    *,
    user: User,
    job: GenerationJob,
    balance_after: float,
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
) -> CreditTransaction | None:
    """记录一条生成任务扣费流水（负数 delta）。"""
    amount = normalize_credit_amount(job.credit_cost, default=0)
    if amount <= 0:
        return None
    # 用户侧不展示内部模型名；model_name 仍落库供管理端对账
    ref_key = job_consume_reference_key(job.id, idempotency_key=idempotency_key)
    return await record_credit_transaction(
        db,
        user_id=user.id,
        delta=-amount,
        balance_after=normalize_credit_amount(balance_after, default=0),
        source=CREDIT_TX_SOURCE_JOB_CONSUME,
        reason=f"生成任务扣费 · 任务 {job.id}",
        model_name=job.model,
        job_id=job.id,
        reference_key=ref_key,
        created_at=created_at,
    )


async def record_job_refund_transaction(
    db: AsyncSession,
    *,
    user: User,
    job: GenerationJob,
    balance_after: float,
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
) -> CreditTransaction | None:
    """记录一条任务失败退还流水（正数 delta）。"""
    amount = normalize_credit_amount(job.credit_cost, default=0)
    if amount <= 0:
        return None
    # 用户侧不展示内部模型名；model_name 仍落库供管理端对账
    ref_key = job_refund_reference_key(job.id, idempotency_key=idempotency_key)
    return await record_credit_transaction(
        db,
        user_id=user.id,
        delta=amount,
        balance_after=normalize_credit_amount(balance_after, default=0),
        source=CREDIT_TX_SOURCE_JOB_REFUND,
        reason=f"任务失败退还 · 任务 {job.id}",
        model_name=job.model,
        job_id=job.id,
        reference_key=ref_key,
        created_at=created_at,
    )


async def adjust_credits_with_log(
    db: AsyncSession,
    *,
    user: User,
    delta: float,
    source: str,
    reason: str = "",
    operator_id: int | None = None,
    credit_type: str = CREDIT_TYPE_GENERAL,
    model_name: str | None = None,
    valid_days: int | None = None,
    lot_id: int | None = None,
) -> float:
    """调整算力并同时写流水（管理员调整等）：转调统一账本写入层。"""
    if delta == 0:
        user = await _lock_user(db, user.id)
        return await sync_user_compute_power(db, user)

    if delta > 0:
        op = CreditLedgerOp.GRANT_LOT
        amount = normalize_credit_amount(delta)
        source_ref = reason.strip() or None
    else:
        op = CreditLedgerOp.DEDUCT_LOTS
        amount = normalize_credit_amount(abs(delta))
        source_ref = None

    result = await apply_credit_ledger_write(
        db,
        user=user,
        req=CreditLedgerWriteRequest(
            op=op,
            amount=amount,
            source=source,
            credit_type=credit_type,
            reason=reason,
            source_ref=source_ref,
            model_name=model_name,
            valid_days=valid_days,
            operator_id=operator_id,
            transaction_lot_id=lot_id,
        ),
    )
    return result.balance


async def grant_credits_with_log(
    db: AsyncSession,
    *,
    user: User,
    amount: float,
    credit_type: str,
    source: str,
    reason: str = "",
    operator_id: int | None = None,
    model_name: str | None = None,
    valid_days: int | None = None,
    expires_at: datetime | None = None,
    source_ref: str | None = None,
) -> float:
    """发放算力 lot 并同时写流水（充值/活动/会员）：转调统一账本写入层。"""
    result = await apply_credit_ledger_write(
        db,
        user=user,
        req=CreditLedgerWriteRequest(
            op=CreditLedgerOp.GRANT_LOT,
            amount=normalize_credit_amount(amount),
            source=source,
            credit_type=credit_type,
            reason=reason,
            source_ref=source_ref,
            model_name=model_name,
            valid_days=valid_days,
            expires_at=expires_at,
            operator_id=operator_id,
        ),
    )
    return result.balance
