"""本地算力账本 —— 基于 credit_lots 的预扣 / 结算 / 释放（含行锁与幂等）。"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import parse_entity_id
from ..core.credit_types import CREDIT_TYPE_GENERAL
from ..core.credit_amount import normalize_credit_amount
from ..models.credit import CreditReservation
from ..models.user import User
from .credit_lots import (
    LotAllocation,
    deduct_from_lots_by_priority,
    grant_credit_lot,
    parse_allocations,
    reverse_allocations,
    sum_user_balance,
    sync_user_compute_power,
)

logger = logging.getLogger(__name__)


async def _lock_user(db: AsyncSession, user_id: int) -> User:
    """加行锁（SELECT ... FOR UPDATE）取用户，保证余额读改写的并发安全。"""
    result = await db.execute(select(User).filter(User.id == user_id).with_for_update())
    user = result.scalar_one_or_none()
    if not user:
        raise ValueError(f"User {user_id} not found")
    return user


async def get_local_balance(
    db: AsyncSession,
    user: User,
    *,
    model_name: str | None = None,
) -> float:
    """按 lot 汇总用户可用余额（可选按模型过滤）。"""
    return await sum_user_balance(db, user.id, model_name=model_name)


async def reserve_local_credits(
    db: AsyncSession,
    *,
    user_id: int,
    amount: float,
    idempotency_key: str,
    reference: str = "",
    job_id: int | None = None,
    model_name: str | None = None,
) -> dict | None:
    """预扣算力：幂等键命中已存在则复用；否则校验余额→按优先级扣 lot→写 reservation。"""
    user = await _lock_user(db, user_id)
    amount = normalize_credit_amount(amount)

    existing = await db.execute(
        select(CreditReservation).filter(CreditReservation.idempotency_key == idempotency_key)
    )
    reservation = existing.scalar_one_or_none()
    if reservation:
        if reservation.status == "released":
            return None
        return {
            "ok": True,
            "reservation_id": str(reservation.id),
            "remaining": await get_local_balance(db, user, model_name=model_name),
            "created": False,
        }

    if amount <= 0:
        return {
            "ok": True,
            "reservation_id": None,
            "remaining": await get_local_balance(db, user, model_name=model_name),
            "created": False,
        }

    available = await get_local_balance(db, user, model_name=model_name)
    if available < amount:
        return None

    allocations = await deduct_from_lots_by_priority(
        db,
        user=user,
        amount=amount,
        model_name=model_name,
    )
    if allocations is None:
        return None

    now = now_cst_naive()
    reservation = CreditReservation(
        user_id=user_id,
        job_id=job_id,
        amount=amount,
        status="reserved",
        idempotency_key=idempotency_key,
        reference=reference or None,
        model_name=model_name,
        allocations=[a.to_dict() for a in allocations],
        created_at=now,
        updated_at=now,
    )
    db.add(reservation)
    await sync_user_compute_power(db, user)
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(job_id, user_id, idempotency_key) 或 idempotency_key 全局唯一冲突 → 幂等复用
        dup = await db.execute(
            select(CreditReservation).filter(CreditReservation.idempotency_key == idempotency_key)
        )
        reservation = dup.scalar_one_or_none()
        if not reservation:
            return None
        if reservation.status == "released":
            return None
        user = await _lock_user(db, user_id)
        return {
            "ok": True,
            "reservation_id": str(reservation.id),
            "remaining": await get_local_balance(db, user, model_name=model_name),
            "created": False,
        }
    return {
        "ok": True,
        "reservation_id": str(reservation.id),
        "remaining": await get_local_balance(db, user, model_name=model_name),
        "created": True,
    }


async def commit_local_credits(db: AsyncSession, reservation_id: str) -> bool:
    """结算预扣：将 reservation 置为 committed（幂等，lot 余额已在预扣时扣减）。"""
    rid = parse_entity_id(reservation_id)
    if rid is None:
        logger.warning("Invalid reservation id: %s", reservation_id)
        return False

    result = await db.execute(
        select(CreditReservation).filter(CreditReservation.id == rid).with_for_update()
    )
    reservation = result.scalar_one_or_none()
    if not reservation:
        logger.warning("Reservation not found: %s", reservation_id)
        return False
    if reservation.status == "committed":
        return True
    if reservation.status != "reserved":
        logger.warning("Cannot commit reservation %s in status %s", reservation_id, reservation.status)
        return False

    reservation.status = "committed"
    reservation.updated_at = now_cst_naive()
    await db.flush()
    return True


async def release_local_credits(db: AsyncSession, reservation_id: str) -> bool:
    """释放预扣：按 allocations 原路退回各 lot 并同步缓存；已 committed 的不退。"""
    rid = parse_entity_id(reservation_id)
    if rid is None:
        logger.warning("Invalid reservation id: %s", reservation_id)
        return False

    result = await db.execute(
        select(CreditReservation).filter(CreditReservation.id == rid).with_for_update()
    )
    reservation = result.scalar_one_or_none()
    if not reservation:
        logger.warning("Reservation not found: %s", reservation_id)
        return False
    if reservation.status == "released":
        return True
    if reservation.status == "committed":
        return False

    allocations = parse_allocations(reservation.allocations)
    if allocations:
        await reverse_allocations(db, allocations)
        user = await _lock_user(db, reservation.user_id)
        await sync_user_compute_power(db, user)
    else:
        user = await _lock_user(db, reservation.user_id)
        user.compute_power = normalize_credit_amount(
            normalize_credit_amount(user.compute_power)
            + normalize_credit_amount(reservation.amount)
        )

    reservation.status = "released"
    reservation.updated_at = now_cst_naive()
    await db.flush()
    return True


async def adjust_local_credits(
    db: AsyncSession,
    *,
    user_id: int,
    delta: float,
    credit_type: str = CREDIT_TYPE_GENERAL,
    model_name: str | None = None,
    valid_days: int | None = None,
    source: str = "admin_adjust",
    source_ref: str | None = None,
) -> float:
    """直接调整用户算力：正数发放新 lot，负数按优先级扣减，返回调整后余额。"""
    user = await _lock_user(db, user_id)
    delta = normalize_credit_amount(delta) if delta >= 0 else -normalize_credit_amount(abs(delta))
    if delta > 0:
        await grant_credit_lot(
            db,
            user_id=user_id,
            credit_type=credit_type,
            amount=delta,
            source=source,
            source_ref=source_ref,
            model_name=model_name,
            valid_days=valid_days,
        )
    elif delta < 0:
        amount = abs(delta)
        allocations = await deduct_from_lots_by_priority(
            db,
            user=user,
            amount=amount,
            model_name=model_name,
        )
        if allocations is None:
            raise ValueError("Insufficient credits for deduction")
    return await sync_user_compute_power(db, user)
