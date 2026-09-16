"""充值算力 —— Redis 标记锁 + DB 占位单 + 入账（§8.11）。"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_types import CREDIT_TYPE_GENERAL
from ..core.datetime_util import now_cst_naive
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.credit import CreditRechargeOrder
from ..models.user import User
from .credit_lots import sync_user_compute_power
from .credit_operation_lock import seal_credit_recharge
from .credit_transactions import grant_credits_with_log
from .local_credits import _lock_user


async def recharge_user_credits(
    db: AsyncSession,
    *,
    user: User,
    amount: int,
    idempotency_key: str | None = None,
) -> int:
    """用户充值：行锁 → 占位单 UNIQUE(user_id, idempotency_key) → 入账 → seal Redis。"""
    key = (idempotency_key or "").strip() or f"recharge-{uuid.uuid4().hex}"
    await _lock_user(db, user.id)

    existing = await db.execute(
        select(CreditRechargeOrder).filter(
            CreditRechargeOrder.user_id == user.id,
            CreditRechargeOrder.idempotency_key == key,
        )
    )
    order = existing.scalar_one_or_none()
    if order and order.status == "completed":
        return await sync_user_compute_power(db, user)

    if order is None:
        order = CreditRechargeOrder(
            user_id=user.id,
            idempotency_key=key,
            amount=amount,
            pay_amount_fen=0,
            order_type="recharge",
            payment_channel="direct",
            status="pending",
            created_at=now_cst_naive(),
            expires_at=now_cst_naive(),  # 直连演示立即完成，过期时刻记为创建时
        )
        db.add(order)
        try:
            await db.flush()
        except IntegrityError:
            dup = await db.execute(
                select(CreditRechargeOrder).filter(
                    CreditRechargeOrder.user_id == user.id,
                    CreditRechargeOrder.idempotency_key == key,
                )
            )
            order = dup.scalar_one_or_none()
            if order and order.status == "completed":
                return await sync_user_compute_power(db, user)
            fail(ErrorCode.RECHARGE_IN_PROGRESS)

    if order.status != "completed":
        balance = await grant_credits_with_log(
            db,
            user=user,
            amount=amount,
            credit_type=CREDIT_TYPE_GENERAL,
            source="recharge",
            reason=f"用户充值 {amount}",
            source_ref=key,
        )
        order.status = "completed"
        order.completed_at = now_cst_naive()
        await db.flush()
        await seal_credit_recharge(user.id)
        return balance

    return await sync_user_compute_power(db, user)
