"""管理端充值/支付订单查询。"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import format_user_display_id, resolve_user_ids_for_filter
from ..models.credit import CreditRechargeOrder
from ..models.subscription import SubscriptionPlan
from ..models.user import User

ORDER_TYPE_LABELS = {
    "recharge": "算力充值",
    "subscription": "会员开通",
}

PAYMENT_CHANNEL_LABELS = {
    "alipay": "支付宝",
    "direct": "直连演示",
}

STATUS_LABELS = {
    "pending": "待支付",
    "completed": "已完成",
    "failed": "失败",
    "expired": "已过期",
}


def _resolve_display_expires(
    order: CreditRechargeOrder,
    *,
    period_days: int | None = None,
) -> tuple[datetime | None, str | None]:
    """解析列表「过期时间」展示。

    - 算力充值订单：支付有效期（创建起 24h，优先读 order.expires_at）
    - 会员已完成：会员账期结束；未完成：支付单过期时间
    """
    from .payment_orders import resolve_order_expires_at

    order_type = order.order_type or "recharge"
    order_pay_expires = resolve_order_expires_at(order)

    if order_type == "recharge":
        return order_pay_expires, None

    if order.status == "completed" and order.completed_at is not None:
        days = int(period_days or 30)
        return order.completed_at + timedelta(days=days), None

    return order_pay_expires, None


def recharge_order_to_dict(
    order: CreditRechargeOrder,
    *,
    user_display_name: str | None = None,
    user_phone: str | None = None,
    user_no: str | None = None,
    plan_name: str | None = None,
    period_days: int | None = None,
) -> dict:
    expires_at, expires_label = _resolve_display_expires(order, period_days=period_days)

    return {
        "id": str(order.id),
        "user_id": str(order.user_id),
        "user_display_name": user_display_name,
        "user_phone": user_phone,
        "user_no": user_no,
        "out_trade_no": order.idempotency_key,
        "order_type": order.order_type,
        "order_type_label": ORDER_TYPE_LABELS.get(order.order_type or "", order.order_type or ""),
        "payment_channel": order.payment_channel,
        "payment_channel_label": PAYMENT_CHANNEL_LABELS.get(
            order.payment_channel or "", order.payment_channel or ""
        ),
        "amount": int(order.amount or 0),
        "pay_amount_fen": int(order.pay_amount_fen or 0),
        "plan_id": str(order.plan_id) if order.plan_id else None,
        "plan_name": plan_name,
        "alipay_trade_no": order.alipay_trade_no,
        "status": order.status,
        "status_label": STATUS_LABELS.get(order.status or "", order.status or ""),
        "created_at": order.created_at,
        "completed_at": order.completed_at,
        "expires_at": expires_at,
        "expires_label": expires_label,
    }


async def list_recharge_orders(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    user_filter: str | None = None,
    order_type: str | None = None,
    status: str | None = None,
    payment_channel: str | None = None,
    out_trade_no: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """分页查询充值/支付订单，含用户、套餐与过期时间。"""
    # 列表前先批量过期超时 pending，避免界面仍显示「待支付」
    from .payment_orders import expire_stale_pending_payment_orders

    await expire_stale_pending_payment_orders(db)

    filters = []
    if user_id is not None:
        filters.append(CreditRechargeOrder.user_id == user_id)
    elif user_filter and user_filter.strip():
        matched_ids = await resolve_user_ids_for_filter(db, user_filter)
        if not matched_ids:
            return [], 0
        filters.append(CreditRechargeOrder.user_id.in_(matched_ids))
    if order_type:
        filters.append(CreditRechargeOrder.order_type == order_type)
    if status:
        filters.append(CreditRechargeOrder.status == status)
    if payment_channel:
        filters.append(CreditRechargeOrder.payment_channel == payment_channel)
    if out_trade_no and out_trade_no.strip():
        key = out_trade_no.strip()
        filters.append(
            or_(
                CreditRechargeOrder.idempotency_key == key,
                CreditRechargeOrder.idempotency_key.ilike(f"%{key}%"),
            )
        )
    if created_from is not None:
        filters.append(CreditRechargeOrder.created_at >= created_from)
    if created_to is not None:
        filters.append(CreditRechargeOrder.created_at < created_to)

    count_query = select(func.count(CreditRechargeOrder.id))
    for clause in filters:
        count_query = count_query.filter(clause)
    total = (await db.execute(count_query)).scalar_one()

    offset = (page - 1) * page_size
    query = (
        select(
            CreditRechargeOrder,
            User.display_name,
            User.phone,
            User.id,
            SubscriptionPlan.name,
            SubscriptionPlan.period_days,
        )
        .join(User, User.id == CreditRechargeOrder.user_id)
        .outerjoin(SubscriptionPlan, SubscriptionPlan.id == CreditRechargeOrder.plan_id)
        .order_by(CreditRechargeOrder.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    for clause in filters:
        query = query.filter(clause)

    rows = (await db.execute(query)).all()
    items = [
        recharge_order_to_dict(
            order,
            user_display_name=display_name or "",
            user_phone=phone,
            user_no=format_user_display_id(uid),
            plan_name=plan_name,
            period_days=period_days,
        )
        for order, display_name, phone, uid, plan_name, period_days in rows
    ]
    return items, total
