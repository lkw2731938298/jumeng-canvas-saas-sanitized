"""会员订阅 —— 套餐管理、开通/取消、按账期发放订阅算力、到期续费。"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .credit_operation_lock import (
    claim_credit_subscription,
    is_credit_subscription_in_progress,
    seal_credit_subscription,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_types import CREDIT_TYPE_SUBSCRIPTION
from ..core.datetime_util import as_cst_aware, now_cst_aware, now_cst_naive
from ..models.subscription import SubscriptionGrant, SubscriptionPlan, UserSubscription
from ..models.user import User
from .credit_transactions import grant_credits_with_log

logger = logging.getLogger(__name__)

SUB_STATUS_ACTIVE = "active"
SUB_STATUS_CANCELLED = "cancelled"
SUB_STATUS_EXPIRED = "expired"

DEFAULT_PLANS: list[dict[str, Any]] = [
    {
        "code": "monthly_basic",
        "name": "月度会员 · 基础",
        "description": "每月赠送 500 会员订阅算力，额外 20 GiB 云存储",
        "monthly_credits": 500,
        "storage_gb": 20,
        "period_days": 30,
        "price_cents": 2900,
        "sort_order": 10,
    },
    {
        "code": "monthly_pro",
        "name": "月度会员 · 专业",
        "description": "每月赠送 1500 会员订阅算力，额外 100 GiB 云存储",
        "monthly_credits": 1500,
        "storage_gb": 100,
        "period_days": 30,
        "price_cents": 7900,
        "sort_order": 20,
    },
]


def _utcnow() -> datetime:
    return now_cst_naive()


def _period_key(period_start: datetime) -> str:
    """按账期起始日生成幂等键（防同一账期重复发放订阅算力）。"""
    return as_cst_aware(period_start).strftime("%Y-%m-%d")


def _plan_out(plan: SubscriptionPlan) -> dict[str, Any]:
    """将套餐实体序列化为前端 DTO。"""
    return {
        "id": str(plan.id),
        "code": plan.code,
        "name": plan.name,
        "description": plan.description or "",
        "monthlyCredits": int(plan.monthly_credits or 0),
        "storageGb": int(plan.storage_gb or 0),
        "periodDays": int(plan.period_days or 30),
        "priceCents": int(plan.price_cents or 0),
        "sortOrder": int(plan.sort_order or 0),
        "isActive": bool(plan.is_active),
    }


def _subscription_out(sub: UserSubscription, plan: SubscriptionPlan) -> dict[str, Any]:
    """将用户订阅 + 套餐序列化为前端 DTO（含是否有效、剩余天数）。"""
    # 统一使用东八区 aware 时间比较：period_end 经 as_cst_aware 转为 aware，
    # now 也必须 aware，否则 aware vs naive 比较会抛 TypeError 触发 500。
    now = now_cst_aware()
    period_end = as_cst_aware(sub.current_period_end)
    period_start = as_cst_aware(sub.current_period_start)
    is_active = (
        sub.status == SUB_STATUS_ACTIVE
        and period_end is not None
        and period_end > now
    )
    return {
        "id": str(sub.id),
        "planId": str(plan.id),
        "planCode": plan.code,
        "planName": plan.name,
        "status": sub.status,
        "autoRenew": bool(sub.auto_renew),
        "monthlyCredits": int(plan.monthly_credits or 0),
        "storageGb": int(plan.storage_gb or 0),
        "currentPeriodStart": period_start.isoformat() if period_start else None,
        "currentPeriodEnd": period_end.isoformat() if period_end else None,
        "isActive": is_active,
        "daysRemaining": max((period_end - now).days, 0) if is_active and period_end else 0,
    }


async def ensure_default_plans(db: AsyncSession) -> int:
    """初始化默认会员套餐（已存在同 code 则跳过），返回新建数量。"""
    created = 0
    now = _utcnow()
    for item in DEFAULT_PLANS:
        result = await db.execute(
            select(SubscriptionPlan).filter(SubscriptionPlan.code == item["code"])
        )
        if result.scalar_one_or_none():
            continue
        db.add(
            SubscriptionPlan(
                code=item["code"],
                name=item["name"],
                description=item.get("description"),
                monthly_credits=item["monthly_credits"],
                storage_gb=int(item.get("storage_gb") or 0),
                period_days=item.get("period_days", 30),
                price_cents=item.get("price_cents", 0),
                sort_order=item.get("sort_order", 0),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    if created:
        await db.flush()
        logger.info("Seeded %s subscription plan(s)", created)
    return created


async def list_active_plans(db: AsyncSession) -> list[dict[str, Any]]:
    """列出对用户开放的启用套餐（按排序）。"""
    result = await db.execute(
        select(SubscriptionPlan)
        .filter(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.created_at.asc())
    )
    return [_plan_out(plan) for plan in result.scalars().all()]


async def list_all_plans(db: AsyncSession) -> list[dict[str, Any]]:
    """管理端：列出全部套餐（含停用）。"""
    result = await db.execute(
        select(SubscriptionPlan).order_by(SubscriptionPlan.sort_order.asc())
    )
    return [_plan_out(plan) for plan in result.scalars().all()]


async def get_plan(db: AsyncSession, plan_id: int) -> SubscriptionPlan | None:
    """按 ID 查询套餐。"""
    result = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id))
    return result.scalar_one_or_none()


async def get_user_active_subscription(
    db: AsyncSession, user_id: int
) -> tuple[UserSubscription, SubscriptionPlan] | None:
    """获取用户当前有效订阅及其套餐（无则返回 None）。"""
    now = _utcnow()
    now_db = now_cst_naive()
    result = await db.execute(
        select(UserSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
        .filter(
            UserSubscription.user_id == user_id,
            UserSubscription.status == SUB_STATUS_ACTIVE,
            UserSubscription.current_period_end > now_db,
        )
        .order_by(UserSubscription.current_period_end.desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return None
    return row[0], row[1]


async def _grant_period_credits(
    db: AsyncSession,
    *,
    user: User,
    subscription: UserSubscription,
    plan: SubscriptionPlan,
    period_start: datetime,
    period_end: datetime,
    operator_id: int | None = None,
) -> bool:
    """按账期发放订阅算力：先写 SubscriptionGrant（UNIQUE user+period）再加款。"""
    period_key = _period_key(period_start)
    amount = int(plan.monthly_credits or 0)
    if amount <= 0:
        return False

    existing = await db.execute(
        select(SubscriptionGrant.id).filter(
            SubscriptionGrant.user_id == user.id,
            SubscriptionGrant.period_key == period_key,
        )
    )
    if existing.scalar_one_or_none():
        return False

    source_ref = f"{subscription.id}:{period_key}"
    grant = SubscriptionGrant(
        subscription_id=subscription.id,
        user_id=user.id,
        lot_id=None,
        period_key=period_key,
        amount=amount,
        granted_at=_utcnow(),
    )
    db.add(grant)
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(user_id, period_key) 冲突 → 该账期已发放
        return False

    await grant_credits_with_log(
        db,
        user=user,
        amount=amount,
        credit_type=CREDIT_TYPE_SUBSCRIPTION,
        source="subscription_grant",
        reason=f"会员订阅赠送：{plan.name}（{period_key}）",
        operator_id=operator_id,
        expires_at=period_end,
        source_ref=source_ref,
    )

    from ..models.credit_lot import CreditLot

    lot_row = await db.execute(
        select(CreditLot)
        .filter(
            CreditLot.user_id == user.id,
            CreditLot.source == "subscription_grant",
            CreditLot.source_ref == source_ref,
        )
        .order_by(CreditLot.created_at.desc())
        .limit(1)
    )
    lot = lot_row.scalar_one_or_none()
    if lot:
        grant.lot_id = lot.id
    await db.flush()
    await seal_credit_subscription(user.id)
    return True


async def activate_subscription(
    db: AsyncSession,
    *,
    user: User,
    plan: SubscriptionPlan,
    auto_renew: bool = True,
    operator_id: int | None = None,
) -> dict[str, Any]:
    """开通/切换会员：标记锁→失效旧订阅→建新订阅→发放当期算力。"""
    if await is_credit_subscription_in_progress(user.id):
        fail(ErrorCode.SUBSCRIPTION_IN_PROGRESS)
    if not await claim_credit_subscription(user.id):
        fail(ErrorCode.SUBSCRIPTION_IN_PROGRESS)

    try:
        if not plan.is_active:
            fail(ErrorCode.SUBSCRIPTION_PLAN_INACTIVE)

        now = _utcnow()
        existing = await get_user_active_subscription(db, user.id)
        if existing:
            old_sub, _old_plan = existing
            old_sub.status = SUB_STATUS_EXPIRED
            old_sub.auto_renew = False
            old_sub.updated_at = now

        period_start = now
        period_end = now + timedelta(days=int(plan.period_days or 30))
        subscription = UserSubscription(
            user_id=user.id,
            plan_id=plan.id,
            status=SUB_STATUS_ACTIVE,
            auto_renew=auto_renew,
            current_period_start=period_start,
            current_period_end=period_end,
            created_at=now,
            updated_at=now,
        )
        db.add(subscription)
        await db.flush()
        await _grant_period_credits(
            db,
            user=user,
            subscription=subscription,
            plan=plan,
            period_start=period_start,
            period_end=period_end,
            operator_id=operator_id,
        )
        return _subscription_out(subscription, plan)
    except Exception:
        raise


async def cancel_subscription(db: AsyncSession, *, user: User) -> dict[str, Any]:
    """取消会员：关闭自动续费（当前账期仍有效直至到期）。"""
    pair = await get_user_active_subscription(db, user.id)
    if not pair:
        fail(ErrorCode.SUBSCRIPTION_NOT_FOUND)
    subscription, plan = pair
    now = _utcnow()
    subscription.auto_renew = False
    subscription.updated_at = now
    await db.flush()
    return _subscription_out(subscription, plan)


async def renew_due_subscriptions(db: AsyncSession) -> int:
    """定时续费：为到期且开启自动续费的订阅进入新账期并发算力；关闭续费的置为过期。"""
    now = _utcnow()
    now_db = now_cst_naive()
    result = await db.execute(
        select(UserSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
        .filter(
            UserSubscription.status == SUB_STATUS_ACTIVE,
            UserSubscription.auto_renew.is_(True),
            UserSubscription.current_period_end <= now_db,
            SubscriptionPlan.is_active.is_(True),
        )
        .with_for_update()
    )
    renewed = 0
    for subscription, plan in result.all():
        user_row = await db.execute(select(User).filter(User.id == subscription.user_id))
        user = user_row.scalar_one_or_none()
        if not user:
            subscription.status = SUB_STATUS_EXPIRED
            subscription.updated_at = now
            continue

        period_start = subscription.current_period_end
        period_end = period_start + timedelta(days=int(plan.period_days or 30))
        subscription.current_period_start = period_start
        subscription.current_period_end = period_end
        subscription.updated_at = now
        granted = await _grant_period_credits(
            db,
            user=user,
            subscription=subscription,
            plan=plan,
            period_start=period_start,
            period_end=period_end,
        )
        if granted:
            renewed += 1

    expired = await db.execute(
        select(UserSubscription).filter(
            UserSubscription.status == SUB_STATUS_ACTIVE,
            UserSubscription.current_period_end <= now_db,
            UserSubscription.auto_renew.is_(False),
        )
    )
    for subscription in expired.scalars().all():
        subscription.status = SUB_STATUS_EXPIRED
        subscription.updated_at = now

    if renewed:
        await db.flush()
        logger.info("Renewed %s subscription period(s)", renewed)
    return renewed


async def create_plan(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    description: str,
    monthly_credits: int,
    storage_gb: int,
    period_days: int,
    price_cents: int,
    sort_order: int,
    is_active: bool,
) -> SubscriptionPlan:
    """管理端创建套餐（code 唯一，冲突返回错误）。"""
    existing = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.code == code))
    if existing.scalar_one_or_none():
        fail(ErrorCode.PLAN_CODE_CONFLICT)
    now = _utcnow()
    plan = SubscriptionPlan(
        code=code.strip(),
        name=name.strip(),
        description=description.strip() or None,
        monthly_credits=monthly_credits,
        storage_gb=max(int(storage_gb or 0), 0),
        period_days=period_days,
        price_cents=price_cents,
        sort_order=sort_order,
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )
    db.add(plan)
    await db.flush()
    return plan


async def update_plan(db: AsyncSession, plan: SubscriptionPlan, **fields: Any) -> SubscriptionPlan:
    """管理端按需更新套餐字段。"""
    if "name" in fields and fields["name"] is not None:
        plan.name = str(fields["name"]).strip()
    if "description" in fields and fields["description"] is not None:
        plan.description = str(fields["description"]).strip() or None
    if "monthly_credits" in fields and fields["monthly_credits"] is not None:
        plan.monthly_credits = int(fields["monthly_credits"])
    if "storage_gb" in fields and fields["storage_gb"] is not None:
        plan.storage_gb = max(int(fields["storage_gb"]), 0)
    if "period_days" in fields and fields["period_days"] is not None:
        plan.period_days = int(fields["period_days"])
    if "price_cents" in fields and fields["price_cents"] is not None:
        plan.price_cents = int(fields["price_cents"])
    if "sort_order" in fields and fields["sort_order"] is not None:
        plan.sort_order = int(fields["sort_order"])
    if "is_active" in fields and fields["is_active"] is not None:
        plan.is_active = bool(fields["is_active"])
    plan.updated_at = _utcnow()
    await db.flush()
    return plan


async def delete_plan(db: AsyncSession, plan: SubscriptionPlan) -> None:
    """管理端删除套餐：已有任何用户订阅记录则禁止删除（应改为下架），以保留订阅历史完整性。"""
    result = await db.execute(
        select(func.count(UserSubscription.id)).filter(UserSubscription.plan_id == plan.id)
    )
    if int(result.scalar_one() or 0) > 0:
        fail(ErrorCode.SUBSCRIPTION_PLAN_IN_USE)
    await db.delete(plan)
    await db.flush()
