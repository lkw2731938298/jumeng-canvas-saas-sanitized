"""Query and serialize credit transaction history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..core.credit_amount import normalize_credit_amount, normalize_credit_delta
from ..core.credit_tx_sources import credit_tx_source_label
from ..core.credit_types import CREDIT_TYPE_GENERAL
from ..core.entity_ids import format_user_display_id, resolve_user_ids_for_filter
from ..models.credit_lot import CreditLot
from ..models.credit_transaction import CreditTransaction
from ..models.job import Model
from ..models.user import User


async def map_model_display_names(
    db: AsyncSession,
    model_names: set[str],
    *,
    fallback_to_internal: bool = True,
) -> dict[str, str]:
    """批量把模型内部 name 映射为展示名。

    fallback_to_internal=True（管理端）：无 display_name 时回退内部 name。
    fallback_to_internal=False（用户端）：仅返回目录已配置的非空 display_name。
    """
    names = {n.strip() for n in model_names if n and str(n).strip()}
    if not names:
        return {}
    rows = (
        await db.execute(select(Model.name, Model.display_name).where(Model.name.in_(names)))
    ).all()
    out: dict[str, str] = {}
    for name, display_name in rows:
        key = str(name or "").strip()
        if not key:
            continue
        label = str(display_name or "").strip()
        if label:
            out[key] = label
        elif fallback_to_internal:
            out[key] = key
    return out


def credit_transaction_to_dict(
    tx: CreditTransaction,
    *,
    user_display_name: str | None = None,
    user_phone: str | None = None,
    user_no: str | None = None,
    lot_expires_at: datetime | None = None,
    lot_credit_type: str | None = None,
) -> dict:
    # 入账流水：通用算力永久；其它类型展示 lot 过期时间。扣费/退款无单独过期概念。
    expires_at = None
    expires_label: str | None = None
    if tx.delta > 0:
        credit_type = (tx.credit_type or lot_credit_type or "").strip()
        if credit_type == CREDIT_TYPE_GENERAL or (
            credit_type == "" and tx.source == "recharge"
        ):
            expires_label = "永久"
            expires_at = None
        elif lot_expires_at is not None:
            expires_at = lot_expires_at
            expires_label = None
        else:
            expires_label = "—"

    return {
        "id": str(tx.id),
        "user_id": str(tx.user_id),
        "user_display_name": user_display_name,
        "user_phone": user_phone,
        "user_no": user_no,
        "operator_id": str(tx.operator_id) if tx.operator_id else None,
        "delta": normalize_credit_delta(tx.delta),
        "balance_after": normalize_credit_amount(tx.balance_after),
        "source": tx.source,
        "source_label": credit_tx_source_label(tx.source),
        "credit_type": tx.credit_type,
        "model_name": tx.model_name,
        # 展示名由 list_credit_transactions 批量回填；此处占位避免缺字段
        "model_display_name": None,
        "job_id": str(tx.job_id) if tx.job_id else None,
        "reason": tx.reason,
        "created_at": tx.created_at,
        "expires_at": expires_at,
        "expires_label": expires_label,
    }


async def list_credit_transactions(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    user_filter: str | None = None,
    source: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    include_user: bool = False,
) -> tuple[list[dict], int]:
    filters = []
    if user_id is not None:
        filters.append(CreditTransaction.user_id == user_id)
    elif user_filter and user_filter.strip():
        matched_ids = await resolve_user_ids_for_filter(db, user_filter)
        if not matched_ids:
            return [], 0
        filters.append(CreditTransaction.user_id.in_(matched_ids))
    if source:
        filters.append(CreditTransaction.source == source)
    if created_from is not None:
        filters.append(CreditTransaction.created_at >= created_from)
    if created_to is not None:
        filters.append(CreditTransaction.created_at < created_to)

    count_query = select(func.count(CreditTransaction.id))
    for clause in filters:
        count_query = count_query.filter(clause)
    total = (await db.execute(count_query)).scalar_one()

    offset = (page - 1) * page_size
    items: list[dict] = []
    Lot = aliased(CreditLot)

    if include_user:
        query = (
            select(
                CreditTransaction,
                User.display_name,
                User.phone,
                User.id,
                Lot.expires_at,
                Lot.credit_type,
            )
            .join(User, User.id == CreditTransaction.user_id)
            .outerjoin(Lot, Lot.id == CreditTransaction.lot_id)
            .order_by(CreditTransaction.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        for clause in filters:
            query = query.filter(clause)
        rows = (await db.execute(query)).all()
        for tx, display_name, phone, uid, lot_expires, lot_type in rows:
            items.append(
                credit_transaction_to_dict(
                    tx,
                    user_display_name=display_name or "",
                    user_phone=phone,
                    user_no=format_user_display_id(uid),
                    lot_expires_at=lot_expires,
                    lot_credit_type=lot_type,
                )
            )
    else:
        query = (
            select(CreditTransaction, Lot.expires_at, Lot.credit_type)
            .outerjoin(Lot, Lot.id == CreditTransaction.lot_id)
            .order_by(CreditTransaction.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        for clause in filters:
            query = query.filter(clause)
        rows = (await db.execute(query)).all()
        for tx, lot_expires, lot_type in rows:
            items.append(
                credit_transaction_to_dict(
                    tx,
                    lot_expires_at=lot_expires,
                    lot_credit_type=lot_type,
                )
            )

    # 流水展示用模型目录 display_name，避免前端直接暴露内部 name
    name_set = {
        str(row.get("model_name") or "").strip()
        for row in items
        if row.get("model_name")
    }
    display_map = await map_model_display_names(db, name_set, fallback_to_internal=False)
    for row in items:
        raw = str(row.get("model_name") or "").strip()
        if not raw:
            continue
        row["model_display_name"] = display_map.get(raw) or None

    return items, total
