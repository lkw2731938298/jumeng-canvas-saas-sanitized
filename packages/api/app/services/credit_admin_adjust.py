"""管理端算力调整 —— Redis 标记锁（10 分钟）+ DB 占位单 + 改 lot（§8.11）。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from ..core.credit_amount import normalize_credit_amount, normalize_credit_delta
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.credit import CreditAdminAdjustOrder
from ..models.user import User
from .credit_lots import sync_user_compute_power
from .credit_operation_lock import seal_credit_admin_adjust
from .credit_transactions import adjust_credits_with_log
from .local_credits import _lock_user


async def admin_adjust_user_credits(
    db: AsyncSession,
    *,
    user: User,
    delta: float,
    reason: str,
    operator_id: int,
    credit_type: str,
    model_name: str | None = None,
    valid_days: int | None = None,
    idempotency_key: str | None = None,
) -> float:
    """管理加扣款：行锁 → 占位 UNIQUE(user_id, idempotency_key) → 改 lot → seal Redis。"""
    key = (idempotency_key or "").strip() or f"admin-adjust-{uuid.uuid4().hex}"
    normalized_delta = normalize_credit_delta(delta)
    await _lock_user(db, user.id)

    existing = await db.execute(
        select(CreditAdminAdjustOrder).filter(
            CreditAdminAdjustOrder.user_id == user.id,
            CreditAdminAdjustOrder.idempotency_key == key,
        )
    )
    order = existing.scalar_one_or_none()
    if order and order.status == "completed":
        return await sync_user_compute_power(db, user)

    if order is None:
        order = CreditAdminAdjustOrder(
            user_id=user.id,
            operator_id=operator_id,
            idempotency_key=key,
            delta=normalized_delta,
            status="pending",
            reason=reason.strip() or None,
            created_at=now_cst_naive(),
        )
        db.add(order)
        try:
            await db.flush()
        except IntegrityError:
            dup = await db.execute(
                select(CreditAdminAdjustOrder).filter(
                    CreditAdminAdjustOrder.user_id == user.id,
                    CreditAdminAdjustOrder.idempotency_key == key,
                )
            )
            order = dup.scalar_one_or_none()
            if order and order.status == "completed":
                return await sync_user_compute_power(db, user)
            fail(ErrorCode.CREDIT_ADJUST_IN_PROGRESS)

    if order.status != "completed":
        balance = await adjust_credits_with_log(
            db,
            user=user,
            delta=normalized_delta,
            source="admin_adjust",
            reason=reason,
            operator_id=operator_id,
            credit_type=credit_type,
            model_name=model_name,
            valid_days=valid_days,
        )
        order.status = "completed"
        order.completed_at = now_cst_naive()
        await db.flush()
        await seal_credit_admin_adjust(user.id)
        return balance

    return await sync_user_compute_power(db, user)
