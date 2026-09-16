"""Admin APIs for credit reconciliation and pending reservations."""

from __future__ import annotations

from ....core.datetime_util import now_cst_naive
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_CREDIT_TRANSACTIONS, PERM_CREDITS
from ....core.credit_amount import normalize_credit_amount
from ....core.entity_ids import parse_entity_id, resolve_user_by_ref
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.job import GenerationJob
from ....models.user import User
from ....schemas.admin import AdminCreditTransactionListOut, AdminCreditTransactionOut
from ....services.credit_flow import get_user_credit_balance
from ....services.credit_admin_adjust import admin_adjust_user_credits
from ....services.credit_lots import get_balance_breakdown
from ....services.credit_operation_lock import credit_admin_adjust_lock
from ....services.credit_reconcile import reconcile_credit_reservations
from ....services.credit_transaction_query import list_credit_transactions

router = APIRouter()


class AdminCreditPendingJobOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: int
    status: str
    credit_status: Optional[str] = Field(None, alias="creditStatus")
    credit_cost: float = Field(0, alias="creditCost")
    model: Optional[str] = None
    node_id: Optional[str] = Field(None, alias="nodeId")
    project_id: Optional[str] = Field(None, alias="projectId")
    created_at: datetime = Field(..., alias="createdAt")
    completed_at: Optional[datetime] = Field(None, alias="completedAt")


class AdminCreditReconcileOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    committed: int
    released: int
    failed_stale: int = Field(..., alias="failedStale")


class AdminAdjustCreditsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    delta: float = Field(..., description="Positive to grant, negative to deduct（一位小数）")
    reason: str = ""
    credit_type: str = Field("general", alias="creditType")
    model_name: Optional[str] = Field(None, alias="modelName")
    valid_days: Optional[int] = Field(None, alias="validDays", ge=1, le=3650)
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey", max_length=128)


class AdminUserCreditsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: str = Field(..., alias="userId")
    display_name: str = Field("", alias="displayName")
    phone: Optional[str] = None
    balance: float
    breakdown: Optional[dict] = None


@router.get("/credits/pending", response_model=list[AdminCreditPendingJobOut])
async def list_pending_credit_jobs(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDITS)),
):
    result = await db.execute(
        select(GenerationJob)
        .filter(
            GenerationJob.credit_reservation_id.isnot(None),
            or_(
                GenerationJob.credit_status.in_(("reserved", "commit_pending", "release_pending")),
                (
                    (GenerationJob.status == "succeeded")
                    & GenerationJob.credit_status.in_(("reserved", "commit_pending"))
                ),
                (
                    (GenerationJob.status == "failed")
                    & GenerationJob.credit_status.in_(("reserved", "release_pending"))
                ),
            ),
        )
        .order_by(GenerationJob.created_at.desc())
        .limit(limit)
    )
    jobs = result.scalars().all()
    return [
        AdminCreditPendingJobOut(
            id=job.id,
            status=job.status,
            creditStatus=job.credit_status,
            creditCost=normalize_credit_amount(job.credit_cost, default=0),
            model=job.model,
            nodeId=job.node_id,
            projectId=str(job.project_id) if job.project_id else None,
            createdAt=job.created_at or now_cst_naive(),
            completedAt=job.completed_at,
        )
        for job in jobs
    ]


@router.post("/credits/reconcile", response_model=AdminCreditReconcileOut)
async def run_admin_credit_reconcile(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDITS)),
):
    stats = await reconcile_credit_reservations(db)
    await db.commit()
    return AdminCreditReconcileOut(
        committed=stats.get("committed", 0),
        released=stats.get("released", 0),
        failedStale=stats.get("failed_stale", 0),
    )


@router.get("/credits/transactions", response_model=AdminCreditTransactionListOut)
async def list_admin_credit_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = Query(None, alias="userId"),
    source: Optional[str] = Query(None),
    created_from: Optional[datetime] = Query(None, alias="createdFrom"),
    created_to: Optional[datetime] = Query(None, alias="createdTo"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDIT_TRANSACTIONS)),
):
    user_id_int: int | None = None
    user_filter: str | None = None
    if user_id:
        trimmed = user_id.strip()
        parsed = parse_entity_id(trimmed)
        if parsed is not None:
            user_id_int = parsed
        else:
            user_filter = trimmed

    items_raw, total = await list_credit_transactions(
        db,
        user_id=user_id_int,
        user_filter=user_filter,
        source=source.strip() if source else None,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
        include_user=True,
    )
    items = [AdminCreditTransactionOut(**row) for row in items_raw]
    return AdminCreditTransactionListOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/credits/users/{user_id}", response_model=AdminUserCreditsOut)
async def get_admin_user_credits(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDITS)),
):
    user = await resolve_user_by_ref(db, user_id)

    balance = normalize_credit_amount(await get_user_credit_balance(user, db) or 0)
    breakdown = await get_balance_breakdown(db, user.id)
    return AdminUserCreditsOut(
        userId=str(user.id),
        displayName=user.display_name or "",
        phone=user.phone,
        balance=balance,
        breakdown=breakdown.get("breakdown"),
    )


@router.post("/credits/users/{user_id}/adjust", response_model=AdminUserCreditsOut)
async def adjust_admin_user_credits(
    user_id: str,
    body: AdminAdjustCreditsIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission(PERM_CREDITS)),
):
    user = await resolve_user_by_ref(db, user_id)

    from ....core.credit_types import ALL_CREDIT_TYPES

    if body.credit_type not in ALL_CREDIT_TYPES:
        fail(ErrorCode.INVALID_CREDIT_TYPE, content={"creditType": body.credit_type})

    async with credit_admin_adjust_lock(user.id):
        balance = await admin_adjust_user_credits(
            db,
            user=user,
            delta=body.delta,
            reason=body.reason,
            operator_id=admin.id,
            credit_type=body.credit_type,
            model_name=body.model_name,
            valid_days=body.valid_days,
            idempotency_key=body.idempotency_key,
        )
    await db.flush()
    breakdown = await get_balance_breakdown(db, user.id)
    return AdminUserCreditsOut(
        userId=str(user.id),
        displayName=user.display_name or "",
        phone=user.phone,
        balance=normalize_credit_amount(balance),
        breakdown=breakdown.get("breakdown"),
    )
