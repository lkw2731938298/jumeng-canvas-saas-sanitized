"""算力批次（lot）账本 —— 发放、余额汇总、按优先级分配、过期处理。

credit_lots 是权威余额来源；users.compute_power 仅为汇总缓存。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_types import (
    ALL_CREDIT_TYPES,
    CREDIT_TYPE_GENERAL,
    CREDIT_TYPE_MODEL_SPECIFIC,
    DEFAULT_CONSUME_PRIORITY,
    normalize_consume_priority,
)
from ..core.credit_amount import normalize_credit_amount
from ..core.datetime_util import now_cst_naive
from ..models.credit_lot import CreditLot
from ..models.user import User

logger = logging.getLogger(__name__)

LOT_STATUS_ACTIVE = "active"
LOT_STATUS_EXHAUSTED = "exhausted"
LOT_STATUS_EXPIRED = "expired"


@dataclass
class LotAllocation:
    """一次扣费在某个 lot 上的分配结果（记录扣了哪个 lot、扣多少，用于原路退回）。"""

    lot_id: int
    amount: float

    def to_dict(self) -> dict[str, Any]:
        """序列化为可存入 reservation.allocations 的 JSON 结构。"""
        return {"lotId": str(self.lot_id), "amount": self.amount}


def _utcnow() -> datetime:
    return now_cst_naive()


def _lot_is_usable(lot: CreditLot, now: datetime) -> bool:
    """判断 lot 是否可用：状态 active、有余额、未过期。"""
    if lot.status != LOT_STATUS_ACTIVE:
        return False
    if normalize_credit_amount(lot.amount_remaining) <= 0:
        return False
    # `now` and lot.expires_at are both naive CST wall-clock (MySQL DATETIME).
    if lot.expires_at is not None and lot.expires_at <= now:
        return False
    return True


def _lot_applies_to_model(lot: CreditLot, model_name: str | None) -> bool:
    """判断 lot 是否适用于该模型：模型专用算力仅匹配同名模型，其余类型通用。"""
    if lot.credit_type == CREDIT_TYPE_MODEL_SPECIFIC:
        return bool(model_name) and lot.model_name == model_name
    return True


def get_user_consume_priority(user: User) -> list[str]:
    """读取用户配置的算力消耗顺序，非法或缺失时回退默认顺序。"""
    raw = user.credit_consume_priority
    if isinstance(raw, list):
        return normalize_consume_priority([str(x) for x in raw])
    return list(DEFAULT_CONSUME_PRIORITY)


def _sort_lots_by_priority(
    lots: list[CreditLot],
    priority: list[str],
) -> list[CreditLot]:
    """按用户类型顺序排序 lot；同类型内先扣最早过期（永久 general 最后）。"""
    type_rank = {credit_type: idx for idx, credit_type in enumerate(priority)}

    def sort_key(lot: CreditLot) -> tuple[int, float, datetime]:
        expires = lot.expires_at
        expires_ts = expires.timestamp() if expires else float("inf")
        return (type_rank.get(lot.credit_type, 999), expires_ts, lot.created_at or _utcnow())

    return sorted(lots, key=sort_key)


def allocate_credits(
    lots: list[CreditLot],
    *,
    amount: float,
    model_name: str | None,
    priority: list[str],
    now: datetime | None = None,
) -> list[LotAllocation] | None:
    """核心分配算法：筛可用 lot→按优先级排序→贪心扣满；额度不足返回 None。"""
    amount = normalize_credit_amount(amount)
    if amount <= 0:
        return []
    now = now or _utcnow()
    usable = [
        lot
        for lot in lots
        if _lot_is_usable(lot, now) and _lot_applies_to_model(lot, model_name)
    ]
    ordered = _sort_lots_by_priority(usable, priority)
    remaining = amount
    allocations: list[LotAllocation] = []
    for lot in ordered:
        if remaining <= 0:
            break
        avail = normalize_credit_amount(lot.amount_remaining)
        take = normalize_credit_amount(min(avail, remaining))
        if take <= 0:
            continue
        allocations.append(LotAllocation(lot_id=lot.id, amount=take))
        remaining = normalize_credit_amount(remaining - take)
    if remaining > 0:
        return None
    return allocations


async def sync_user_compute_power(db: AsyncSession, user: User) -> float:
    """按 lot 汇总重算并回写 users.compute_power 缓存，返回最新总额。"""
    total = await sum_user_balance(db, user.id)
    user.compute_power = total
    await db.flush()
    return total


async def sum_user_balance(db: AsyncSession, user_id: int, model_name: str | None = None) -> float:
    """汇总用户未过期可用算力；传 model_name 时仅计入对该模型可用的额度。"""
    now_db = now_cst_naive()
    result = await db.execute(
        select(CreditLot).filter(
            CreditLot.user_id == user_id,
            CreditLot.status == LOT_STATUS_ACTIVE,
            CreditLot.amount_remaining > 0,
            or_(CreditLot.expires_at.is_(None), CreditLot.expires_at > now_db),
        )
    )
    lots = result.scalars().all()
    total = 0.0
    for lot in lots:
        if model_name is not None and not _lot_applies_to_model(lot, model_name):
            continue
        total = normalize_credit_amount(total + normalize_credit_amount(lot.amount_remaining))
    return total


async def get_balance_breakdown(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """返回用户算力明细：按类型汇总、模型专用余额、即将过期列表。"""
    now_db = now_cst_naive()
    result = await db.execute(
        select(CreditLot).filter(
            CreditLot.user_id == user_id,
            CreditLot.status == LOT_STATUS_ACTIVE,
            CreditLot.amount_remaining > 0,
            or_(CreditLot.expires_at.is_(None), CreditLot.expires_at > now_db),
        )
    )
    lots = list(result.scalars().all())
    breakdown = {credit_type: 0.0 for credit_type in ALL_CREDIT_TYPES}
    model_specific: dict[str, dict[str, Any]] = {}
    expiring_soon: list[dict[str, Any]] = []

    for lot in lots:
        amount = normalize_credit_amount(lot.amount_remaining)
        breakdown[lot.credit_type] = normalize_credit_amount(
            breakdown.get(lot.credit_type, 0) + amount
        )
        if lot.credit_type == CREDIT_TYPE_MODEL_SPECIFIC and lot.model_name:
            entry = model_specific.setdefault(
                lot.model_name,
                {"model": lot.model_name, "balance": 0.0, "expiresAt": None},
            )
            entry["balance"] = normalize_credit_amount(entry["balance"] + amount)
            if lot.expires_at and (
                entry["expiresAt"] is None or lot.expires_at.isoformat() < entry["expiresAt"]
            ):
                entry["expiresAt"] = lot.expires_at.isoformat()
        if lot.expires_at and lot.credit_type != CREDIT_TYPE_GENERAL:
            expiring_soon.append(
                {
                    "type": lot.credit_type,
                    "amount": amount,
                    "expiresAt": lot.expires_at.isoformat(),
                    "model": lot.model_name,
                }
            )

    expiring_soon.sort(key=lambda row: row["expiresAt"])
    total = normalize_credit_amount(sum(breakdown.values()))

    # 模型专用/即将过期明细展示目录 display_name，内部 name 仍保留在 model 字段
    from .credit_transaction_query import map_model_display_names

    name_set = {str(k).strip() for k in model_specific.keys() if k}
    for row in expiring_soon:
        if row.get("model"):
            name_set.add(str(row["model"]).strip())
    display_map = await map_model_display_names(db, name_set, fallback_to_internal=False)
    for entry in model_specific.values():
        raw = str(entry.get("model") or "").strip()
        entry["modelDisplayName"] = display_map.get(raw) or None
    for row in expiring_soon:
        raw = str(row.get("model") or "").strip()
        if raw:
            row["modelDisplayName"] = display_map.get(raw) or None

    return {
        "balance": total,
        "breakdown": breakdown,
        "modelSpecific": list(model_specific.values()),
        "expiringSoon": expiring_soon[:10],
    }


async def grant_credit_lot(
    db: AsyncSession,
    *,
    user_id: int,
    credit_type: str,
    amount: float,
    source: str,
    source_ref: str | None = None,
    model_name: str | None = None,
    expires_at: datetime | None = None,
    valid_days: int | None = None,
) -> CreditLot:
    """发放一个算力 lot：通用算力永久有效，其余类型按 expires_at/valid_days 设过期。"""
    amount = normalize_credit_amount(amount)
    if amount <= 0:
        raise ValueError("grant amount must be positive")
    if credit_type == CREDIT_TYPE_MODEL_SPECIFIC and not model_name:
        raise ValueError("model_specific lot requires model_name")
    if credit_type == CREDIT_TYPE_GENERAL:
        expires_at = None
    elif expires_at is None and valid_days is not None:
        expires_at = _utcnow() + timedelta(days=valid_days)
    elif expires_at is None and credit_type != CREDIT_TYPE_GENERAL:
        expires_at = _utcnow() + timedelta(days=30)

    now = _utcnow()
    lot = CreditLot(
        user_id=user_id,
        credit_type=credit_type,
        model_name=model_name,
        amount_initial=amount,
        amount_remaining=amount,
        expires_at=expires_at,
        source=source,
        source_ref=source_ref,
        status=LOT_STATUS_ACTIVE,
        created_at=now,
        updated_at=now,
    )
    db.add(lot)
    await db.flush()
    return lot


async def apply_allocations(
    db: AsyncSession,
    allocations: list[LotAllocation],
) -> None:
    """执行扣减：对各 lot 加行锁后扣余额，扣空则置 exhausted。"""
    for alloc in allocations:
        result = await db.execute(
            select(CreditLot).filter(CreditLot.id == alloc.lot_id).with_for_update()
        )
        lot = result.scalar_one_or_none()
        if not lot:
            raise ValueError(f"Credit lot {alloc.lot_id} not found")
        remaining = normalize_credit_amount(
            normalize_credit_amount(lot.amount_remaining) - normalize_credit_amount(alloc.amount)
        )
        if remaining < 0:
            raise ValueError(f"Insufficient lot balance for {alloc.lot_id}")
        lot.amount_remaining = remaining
        if remaining <= 0:
            lot.status = LOT_STATUS_EXHAUSTED
        lot.updated_at = _utcnow()
    await db.flush()


async def reverse_allocations(
    db: AsyncSession,
    allocations: list[LotAllocation],
) -> None:
    """按分配记录原路退回各 lot 余额（退款/释放时使用），未过期则恢复 active。"""
    now = _utcnow()
    for alloc in allocations:
        result = await db.execute(
            select(CreditLot).filter(CreditLot.id == alloc.lot_id).with_for_update()
        )
        lot = result.scalar_one_or_none()
        if not lot:
            logger.warning("Release skipped missing lot %s", alloc.lot_id)
            continue
        lot.amount_remaining = normalize_credit_amount(
            normalize_credit_amount(lot.amount_remaining) + normalize_credit_amount(alloc.amount)
        )
        if lot.expires_at is None or lot.expires_at > now:
            lot.status = LOT_STATUS_ACTIVE
        lot.updated_at = now
    await db.flush()


async def deduct_from_lots_by_priority(
    db: AsyncSession,
    *,
    user: User,
    amount: float,
    model_name: str | None = None,
) -> list[LotAllocation] | None:
    """按用户消耗优先级从 lot 扣减（唯一扣费入口）：加行锁→分配→执行，不足返回 None。"""
    amount = normalize_credit_amount(amount)
    if amount <= 0:
        return []
    now = _utcnow()
    now_db = now_cst_naive()
    result = await db.execute(
        select(CreditLot)
        .filter(
            CreditLot.user_id == user.id,
            CreditLot.status == LOT_STATUS_ACTIVE,
            CreditLot.amount_remaining > 0,
            or_(CreditLot.expires_at.is_(None), CreditLot.expires_at > now_db),
        )
        .with_for_update()
    )
    lots = list(result.scalars().all())
    priority = get_user_consume_priority(user)
    allocations = allocate_credits(
        lots,
        amount=amount,
        model_name=model_name,
        priority=priority,
        now=now,
    )
    if allocations is None:
        return None
    await apply_allocations(db, allocations)
    return allocations


async def expire_stale_lots(db: AsyncSession) -> int:
    """定时将到期 lot 标记为 expired 并同步相关用户的算力缓存，返回处理条数。"""
    now = _utcnow()
    now_db = now_cst_naive()
    result = await db.execute(
        select(CreditLot)
        .filter(
            CreditLot.status == LOT_STATUS_ACTIVE,
            CreditLot.amount_remaining > 0,
            CreditLot.expires_at.isnot(None),
            CreditLot.expires_at <= now_db,
        )
        .with_for_update()
    )
    lots = result.scalars().all()
    affected_users: set[int] = set()
    count = 0
    for lot in lots:
        lot.status = LOT_STATUS_EXPIRED
        lot.updated_at = now
        affected_users.add(lot.user_id)
        count += 1
    if count:
        await db.flush()
        for user_id in affected_users:
            user_result = await db.execute(select(User).filter(User.id == user_id).with_for_update())
            user = user_result.scalar_one_or_none()
            if user:
                await sync_user_compute_power(db, user)
        logger.info("Expired %s credit lot(s)", count)
    return count


async def migrate_user_balance_to_lots(db: AsyncSession) -> int:
    """一次性迁移：把 users.compute_power 中尚未落入 lot 的差额转成 general lot。

    仅迁移 lot 汇总与 compute_power 缓存之间的差额，避免 admin_adjust / 充值等
    已建 lot 后仍按整段 compute_power 重复 grant（见历史「缓存与 lot 双入账」案例）。
    """
    result = await db.execute(
        select(User).filter(User.compute_power > 0)
    )
    users = result.scalars().all()
    migrated = 0
    for user in users:
        existing = await db.execute(
            select(CreditLot.id)
            .filter(CreditLot.user_id == user.id, CreditLot.source == "migration")
            .limit(1)
        )
        if existing.scalar_one_or_none():
            continue
        lot_sum = await sum_user_balance(db, user.id)
        cached = normalize_credit_amount(user.compute_power)
        # 已有 lot 账本时只补差额；差额≤0 说明缓存已被 lot 覆盖，无需再迁
        amount = normalize_credit_amount(cached - lot_sum)
        if amount <= 0:
            continue
        await grant_credit_lot(
            db,
            user_id=user.id,
            credit_type=CREDIT_TYPE_GENERAL,
            amount=amount,
            source="migration",
            source_ref="compute_power",
        )
        await sync_user_compute_power(db, user)
        migrated += 1
    if migrated:
        await db.flush()
        logger.info("Migrated compute_power to credit_lots for %s user(s)", migrated)
    return migrated


def parse_allocations(raw: list | None) -> list[LotAllocation]:
    """将 reservation.allocations JSON 反序列化为 LotAllocation 列表（供退款使用）。"""
    if not raw:
        return []
    out: list[LotAllocation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        lot_id_raw = item.get("lotId") or item.get("lot_id")
        amount = normalize_credit_amount(item.get("amount"), default=0)
        if not lot_id_raw or amount <= 0:
            continue
        try:
            out.append(LotAllocation(lot_id=int(lot_id_raw), amount=amount))
        except ValueError:
            continue
    return out
