"""Backfill credit_transactions for historical generation job reservations."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.credit import CreditReservation
from ..models.credit_transaction import CreditTransaction
from ..models.job import GenerationJob
from ..models.user import User
from .credit_lots import sum_user_balance
from .credit_transactions import record_job_consume_transaction, record_job_refund_transaction
from .local_credits import get_local_balance

logger = logging.getLogger(__name__)


async def backfill_missing_job_credit_transactions(db: AsyncSession, *, limit: int = 5000) -> int:
    """Create missing consume/refund rows for committed or released reservations."""
    result = await db.execute(
        select(CreditReservation)
        .filter(
            CreditReservation.job_id.isnot(None),
            CreditReservation.amount > 0,
            CreditReservation.status.in_(("committed", "released")),
        )
        .order_by(CreditReservation.created_at.asc())
        .limit(limit)
    )
    reservations = result.scalars().all()
    if not reservations:
        return 0

    created = 0
    for reservation in reservations:
        job = (
            await db.execute(select(GenerationJob).filter(GenerationJob.id == reservation.job_id))
        ).scalar_one_or_none()
        if not job:
            continue
        user = (
            await db.execute(select(User).filter(User.id == reservation.user_id))
        ).scalar_one_or_none()
        if not user:
            continue

        if not job.credit_cost:
            job.credit_cost = int(reservation.amount)

        consume_tx = await record_job_consume_transaction(
            db,
            user=user,
            job=job,
            balance_after=await get_local_balance(db, user),
            created_at=reservation.created_at,
        )
        if consume_tx:
            consume_tx.reason = f"{consume_tx.reason}（历史补录）"
            created += 1

        if reservation.status == "released":
            refund_tx = await record_job_refund_transaction(
                db,
                user=user,
                job=job,
                balance_after=await get_local_balance(db, user),
                created_at=reservation.updated_at or reservation.created_at,
            )
            if refund_tx:
                refund_tx.reason = f"{refund_tx.reason}（历史补录）"
                created += 1

    if created:
        logger.info("Backfilled %s credit transaction rows from reservations", created)
    return created


async def repair_credit_transaction_balances(db: AsyncSession, *, limit_users: int = 5000) -> int:
    """Recompute balance_after per user by walking ledger backwards from current balance."""
    user_rows = await db.execute(
        select(CreditTransaction.user_id).distinct().limit(limit_users)
    )
    user_ids = [row[0] for row in user_rows.all()]
    if not user_ids:
        return 0

    updated = 0
    for user_id in user_ids:
        user = (await db.execute(select(User).filter(User.id == user_id))).scalar_one_or_none()
        if not user:
            continue
        balance = await sum_user_balance(db, user.id)
        tx_rows = await db.execute(
            select(CreditTransaction)
            .filter(CreditTransaction.user_id == user_id)
            .order_by(CreditTransaction.created_at.desc(), CreditTransaction.id.desc())
        )
        for tx in tx_rows.scalars().all():
            if tx.balance_after != balance:
                tx.balance_after = balance
                updated += 1
            balance -= int(tx.delta or 0)

    if updated:
        logger.info("Repaired balance_after on %s credit transaction row(s)", updated)
    return updated
