"""算力账本统一写入 —— lot 入账/扣减 + sync + 流水（不含 Redis、占位单、支付查单）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from .credit_lots import (
    deduct_from_lots_by_priority,
    grant_credit_lot,
    sync_user_compute_power,
)
from .local_credits import _lock_user


class CreditLedgerOp(str, Enum):
    """账本写入操作：新建 lot 加点，或按优先级从 lot 扣减。"""

    GRANT_LOT = "grant_lot"
    DEDUCT_LOTS = "deduct_lots"


class InsufficientCreditsError(ValueError):
    """扣减时 lot 余额不足（与 adjust_local_credits 原 ValueError 语义一致）。"""


@dataclass(frozen=True)
class CreditLedgerWriteRequest:
    """场景编排层传入的账本写入参数（锁/占位单/业务规则不在此）。"""

    op: CreditLedgerOp
    amount: float
    source: str
    credit_type: str
    reason: str = ""
    source_ref: str | None = None
    model_name: str | None = None
    valid_days: int | None = None
    expires_at: datetime | None = None
    operator_id: int | None = None
    skip_transaction: bool = False
    transaction_lot_id: int | None = None


@dataclass(frozen=True)
class CreditLedgerWriteResult:
    """账本写入结果：余额、流水 delta、新建 lot id（GRANT 时）。"""

    balance: float
    delta: float
    lot_id: int | None = None


async def apply_credit_ledger_write(
    db: AsyncSession,
    *,
    user: User,
    req: CreditLedgerWriteRequest,
) -> CreditLedgerWriteResult:
    """唯一账本写入：行锁用户 → 改 lot → sync →（可选）写流水。"""
    if req.amount <= 0:
        raise ValueError("ledger write amount must be positive")

    user = await _lock_user(db, user.id)
    lot_id: int | None = req.transaction_lot_id
    delta: float

    if req.op is CreditLedgerOp.GRANT_LOT:
        lot = await grant_credit_lot(
            db,
            user_id=user.id,
            credit_type=req.credit_type,
            amount=req.amount,
            source=req.source,
            source_ref=req.source_ref or (req.reason.strip() or None),
            model_name=req.model_name,
            valid_days=req.valid_days,
            expires_at=req.expires_at,
        )
        lot_id = lot.id
        delta = req.amount
    elif req.op is CreditLedgerOp.DEDUCT_LOTS:
        allocations = await deduct_from_lots_by_priority(
            db,
            user=user,
            amount=req.amount,
            model_name=req.model_name,
        )
        if allocations is None:
            raise InsufficientCreditsError("Insufficient credits for deduction")
        delta = -req.amount
    else:
        raise ValueError(f"unsupported ledger op: {req.op}")

    balance = await sync_user_compute_power(db, user)

    if not req.skip_transaction:
        from .credit_transactions import record_credit_transaction

        await record_credit_transaction(
            db,
            user_id=user.id,
            operator_id=req.operator_id,
            delta=delta,
            balance_after=balance,
            source=req.source,
            credit_type=req.credit_type,
            lot_id=lot_id,
            model_name=req.model_name,
            reason=req.reason.strip() or None,
        )

    return CreditLedgerWriteResult(balance=balance, delta=delta, lot_id=lot_id)
