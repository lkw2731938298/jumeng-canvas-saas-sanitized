"""在线支付订单：支付宝预下单、异步通知入账、轮询只读缓存（§8.12）。"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from enum import Enum
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_types import CREDIT_TYPE_GENERAL
from ..core.datetime_util import now_cst_naive
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.alipay import alipay_configured, get_alipay_client
from ..models.credit import CreditRechargeOrder
from ..models.subscription import SubscriptionPlan
from ..models.user import User
from .credit_lots import sync_user_compute_power
from .credit_operation_lock import (
    peek_payment_fulfill_lock,
    seal_payment_fulfill,
    try_claim_payment_fulfill,
)
from .credit_transactions import grant_credits_with_log
from .local_credits import _lock_user
from .payment_order_cache import (
    cache_payload_to_api,
    get_payment_order_cache,
    sync_payment_order_cache_from_order,
)
from .payment_tiers import assert_valid_recharge_amount, resolve_pay_amount_fen
from .subscriptions import activate_subscription, get_plan

logger = logging.getLogger(__name__)

ORDER_TYPE_RECHARGE = "recharge"
ORDER_TYPE_SUBSCRIPTION = "subscription"
CHANNEL_ALIPAY = "alipay"
CHANNEL_DIRECT = "direct"

# 充值/会员支付订单待付有效期：创建起 24 小时
RECHARGE_ORDER_TTL = timedelta(hours=24)
# 支付宝当面付超时（与本地 expires_at 对齐）
ALIPAY_ORDER_TIMEOUT_EXPRESS = "24h"

_ALIPAY_SUCCESS_STATUSES = frozenset({"TRADE_SUCCESS", "TRADE_FINISHED"})


class PaymentFulfillOutcome(str, Enum):
    """入账结果：CONCURRENT_BUSY / REDIS_UNAVAILABLE / INVALID 路径禁止写库写缓存。"""

    SUCCESS = "success"
    ALREADY_COMPLETED = "already_completed"
    CONCURRENT_BUSY = "concurrent_busy"
    REDIS_UNAVAILABLE = "redis_unavailable"
    INVALID = "invalid"
    EXPIRED = "expired"


def _new_out_trade_no() -> str:
    """生成商户订单号（同时作为 idempotency_key）。"""
    return f"JM{uuid.uuid4().hex[:26].upper()}"


def compute_recharge_order_expires_at(created_at=None):
    """计算支付订单过期时刻（创建起 24 小时，东八区 naive）。"""
    base = created_at or now_cst_naive()
    return base + RECHARGE_ORDER_TTL


def resolve_order_expires_at(order: CreditRechargeOrder):
    """读取订单过期时刻；历史无字段时回退为 created_at + 24h。"""
    if order.expires_at is not None:
        return order.expires_at
    if order.created_at is not None:
        return order.created_at + RECHARGE_ORDER_TTL
    return None


async def mark_order_expired_if_needed(db: AsyncSession, order: CreditRechargeOrder) -> bool:
    """待支付且已超过 expires_at 时标记 expired，返回是否刚标记过期。"""
    if order.status != "pending":
        return False
    expires = resolve_order_expires_at(order)
    if expires is None or now_cst_naive() <= expires:
        return False
    order.status = "expired"
    if order.expires_at is None:
        order.expires_at = expires
    await db.flush()
    await sync_payment_order_cache_from_order(db, order)
    # 超时未付：提醒用户充值失败/过期
    try:
        from .user_notifications import CATEGORY_RECHARGE, notify_user

        kind = "会员支付" if order.order_type == ORDER_TYPE_SUBSCRIPTION else "充值"
        await notify_user(
            db,
            int(order.user_id),
            category=CATEGORY_RECHARGE,
            title=f"{kind}已过期",
            body=f"订单超时未完成支付（{order.idempotency_key}），请重新发起。",
            dedupe_key=f"recharge:expired:{order.idempotency_key}",
            link_url="/account",
            ref_type="recharge_order",
            ref_id=str(order.idempotency_key),
        )
    except Exception:  # noqa: BLE001
        logger.exception("notify payment expired failed order=%s", order.idempotency_key)
    return True


async def expire_stale_pending_payment_orders(
    db: AsyncSession,
    *,
    limit: int = 500,
) -> int:
    """批量将超时未付的 pending 支付单标为 expired（含历史无 expires_at 的订单）。"""
    now = now_cst_naive()
    legacy_cutoff = now - RECHARGE_ORDER_TTL
    result = await db.execute(
        select(CreditRechargeOrder)
        .where(
            CreditRechargeOrder.status == "pending",
            or_(
                and_(
                    CreditRechargeOrder.expires_at.isnot(None),
                    CreditRechargeOrder.expires_at <= now,
                ),
                and_(
                    CreditRechargeOrder.expires_at.is_(None),
                    CreditRechargeOrder.created_at.isnot(None),
                    CreditRechargeOrder.created_at <= legacy_cutoff,
                ),
            ),
        )
        .order_by(CreditRechargeOrder.id.asc())
        .limit(max(1, min(limit, 2000)))
    )
    orders = list(result.scalars().all())
    if not orders:
        return 0

    for order in orders:
        expires = resolve_order_expires_at(order)
        order.status = "expired"
        if order.expires_at is None and expires is not None:
            order.expires_at = expires
        try:
            await sync_payment_order_cache_from_order(db, order)
        except Exception:
            logger.debug("Skip cache sync for expired order %s", order.idempotency_key)

    await db.flush()
    logger.info("Expired %s stale pending payment order(s)", len(orders))
    return len(orders)


def _order_out(order: CreditRechargeOrder, *, balance: float | None = None) -> dict[str, Any]:
    expires = resolve_order_expires_at(order)
    payload = {
        "outTradeNo": order.idempotency_key,
        "orderType": order.order_type,
        "amount": int(order.amount or 0),
        "payAmountFen": int(order.pay_amount_fen or 0),
        "status": order.status,
        "paymentChannel": order.payment_channel,
        "planId": str(order.plan_id) if order.plan_id else None,
        "completedAt": order.completed_at.isoformat() if order.completed_at else None,
        "expiresAt": expires.isoformat() if expires else None,
    }
    if balance is not None:
        payload["balance"] = balance
    return payload


async def _get_order_by_trade_no(db: AsyncSession, out_trade_no: str) -> CreditRechargeOrder | None:
    result = await db.execute(
        select(CreditRechargeOrder).filter(CreditRechargeOrder.idempotency_key == out_trade_no)
    )
    return result.scalar_one_or_none()


async def create_alipay_recharge_order(
    db: AsyncSession,
    *,
    user: User,
    credits_amount: int,
) -> dict[str, Any]:
    """创建算力充值支付宝扫码单并返回 qr_code。"""
    await assert_valid_recharge_amount(db, credits_amount)
    client = get_alipay_client()
    if client is None:
        fail(ErrorCode.PAYMENT_NOT_CONFIGURED)

    pay_amount_fen = await resolve_pay_amount_fen(db, credits_amount)
    out_trade_no = _new_out_trade_no()
    now = now_cst_naive()
    expires_at = compute_recharge_order_expires_at(now)

    order = CreditRechargeOrder(
        user_id=user.id,
        idempotency_key=out_trade_no,
        amount=credits_amount,
        pay_amount_fen=pay_amount_fen,
        order_type=ORDER_TYPE_RECHARGE,
        payment_channel=CHANNEL_ALIPAY,
        status="pending",
        created_at=now,
        expires_at=expires_at,
    )
    db.add(order)
    try:
        await db.flush()
    except IntegrityError:
        fail(ErrorCode.RECHARGE_IN_PROGRESS)

    subject = f"聚梦画布算力充值{credits_amount}点"
    precreate = await client.trade_precreate(
        out_trade_no=out_trade_no,
        total_amount_yuan=client.fen_to_yuan_str(pay_amount_fen),
        subject=subject,
        timeout_express=ALIPAY_ORDER_TIMEOUT_EXPRESS,
    )
    if precreate.get("code") != "10000" or not precreate.get("qr_code"):
        order.status = "failed"
        await db.flush()
        await sync_payment_order_cache_from_order(db, order)
        try:
            from .user_notifications import CATEGORY_RECHARGE, notify_user

            reason = str(
                precreate.get("sub_msg") or precreate.get("msg") or "支付宝预下单失败"
            )
            await notify_user(
                db,
                int(order.user_id),
                category=CATEGORY_RECHARGE,
                title="充值失败",
                body=f"发起支付失败：{reason}",
                dedupe_key=f"recharge:failed:{order.idempotency_key}",
                link_url="/account",
                ref_type="recharge_order",
                ref_id=str(order.idempotency_key),
            )
        except Exception:  # noqa: BLE001
            logger.exception("notify recharge precreate fail order=%s", out_trade_no)
        fail(
            ErrorCode.PAYMENT_PRECREATE_FAILED,
            message=str(precreate.get("sub_msg") or precreate.get("msg") or "支付宝预下单失败"),
            content={"alipayCode": precreate.get("code"), "subCode": precreate.get("sub_code")},
        )

    await db.flush()
    await sync_payment_order_cache_from_order(db, order)
    return {
        **_order_out(order),
        "qrCode": str(precreate["qr_code"]),
        "payAmountYuan": client.fen_to_yuan_str(pay_amount_fen),
        "subject": subject,
    }


async def create_alipay_subscription_order(
    db: AsyncSession,
    *,
    user: User,
    plan: SubscriptionPlan,
) -> dict[str, Any]:
    """创建会员开通支付宝扫码单并返回 qr_code。"""
    if not plan.is_active:
        fail(ErrorCode.SUBSCRIPTION_PLAN_INACTIVE)
    pay_amount_fen = int(plan.price_cents or 0)
    if pay_amount_fen <= 0:
        fail(ErrorCode.PAYMENT_NOT_CONFIGURED, message="该套餐未配置价格，请联系管理员")

    client = get_alipay_client()
    if client is None:
        fail(ErrorCode.PAYMENT_NOT_CONFIGURED)

    out_trade_no = _new_out_trade_no()
    now = now_cst_naive()
    expires_at = compute_recharge_order_expires_at(now)
    order = CreditRechargeOrder(
        user_id=user.id,
        idempotency_key=out_trade_no,
        amount=int(plan.monthly_credits or 0),
        pay_amount_fen=pay_amount_fen,
        order_type=ORDER_TYPE_SUBSCRIPTION,
        payment_channel=CHANNEL_ALIPAY,
        plan_id=plan.id,
        status="pending",
        created_at=now,
        expires_at=expires_at,
    )
    db.add(order)
    try:
        await db.flush()
    except IntegrityError:
        fail(ErrorCode.SUBSCRIPTION_IN_PROGRESS)

    subject = f"聚梦画布会员-{plan.name}"
    precreate = await client.trade_precreate(
        out_trade_no=out_trade_no,
        total_amount_yuan=client.fen_to_yuan_str(pay_amount_fen),
        subject=subject,
        timeout_express=ALIPAY_ORDER_TIMEOUT_EXPRESS,
    )
    if precreate.get("code") != "10000" or not precreate.get("qr_code"):
        order.status = "failed"
        await db.flush()
        await sync_payment_order_cache_from_order(db, order)
        try:
            from .user_notifications import CATEGORY_RECHARGE, notify_user

            reason = str(
                precreate.get("sub_msg") or precreate.get("msg") or "支付宝预下单失败"
            )
            await notify_user(
                db,
                int(order.user_id),
                category=CATEGORY_RECHARGE,
                title="会员支付失败",
                body=f"发起支付失败：{reason}",
                dedupe_key=f"subscription:failed:{order.idempotency_key}",
                link_url="/account",
                ref_type="recharge_order",
                ref_id=str(order.idempotency_key),
            )
        except Exception:  # noqa: BLE001
            logger.exception("notify subscription precreate fail order=%s", out_trade_no)
        fail(
            ErrorCode.PAYMENT_PRECREATE_FAILED,
            message=str(precreate.get("sub_msg") or precreate.get("msg") or "支付宝预下单失败"),
        )

    await db.flush()
    await sync_payment_order_cache_from_order(db, order)
    return {
        **_order_out(order),
        "qrCode": str(precreate["qr_code"]),
        "payAmountYuan": client.fen_to_yuan_str(pay_amount_fen),
        "subject": subject,
        "planName": plan.name,
    }


async def get_payment_order_for_user(
    db: AsyncSession,
    *,
    user: User,
    out_trade_no: str,
) -> dict[str, Any]:
    """轮询只读：优先 Redis 缓存，miss 时读 DB 回填；不触发查单/入账。"""
    key = out_trade_no.strip()

    cached = await get_payment_order_cache(key)
    if cached is not None:
        if int(cached.get("userId") or 0) != user.id:
            fail(ErrorCode.PAYMENT_ORDER_FORBIDDEN)
        return cache_payload_to_api(cached)

    order = await _get_order_by_trade_no(db, key)
    if not order:
        fail(ErrorCode.PAYMENT_ORDER_NOT_FOUND)
    if order.user_id != user.id:
        fail(ErrorCode.PAYMENT_ORDER_FORBIDDEN)

    # 轮询时顺带过期待支付超时单
    await mark_order_expired_if_needed(db, order)

    balance: float | None = None
    if order.status == "completed" and order.order_type == ORDER_TYPE_RECHARGE:
        balance = await sync_user_compute_power(db, user)

    await sync_payment_order_cache_from_order(db, order, balance=balance)
    return _order_out(order, balance=balance)


async def fulfill_paid_payment_order(
    db: AsyncSession,
    *,
    out_trade_no: str,
    alipay_trade_no: str | None = None,
) -> PaymentFulfillOutcome:
    """支付订单唯一入账公共方法：订单级 Redis 强力锁(5d) → 行锁 → 加款 → 同步缓存。

    并发占位中（CONCURRENT_BUSY）：只读检查，不写 DB/缓存，由 notify 返回 failure 供支付宝重试。
    """
    key = out_trade_no.strip()
    if not key:
        return PaymentFulfillOutcome.INVALID

    order = await _get_order_by_trade_no(db, key)
    if not order:
        return PaymentFulfillOutcome.INVALID
    if order.status == "completed":
        return PaymentFulfillOutcome.ALREADY_COMPLETED
    if order.status in ("failed", "expired"):
        return PaymentFulfillOutcome.INVALID

    # 超过 24h 未付：标记过期，禁止入账
    if await mark_order_expired_if_needed(db, order):
        logger.warning("Payment order expired out_trade_no=%s", key)
        await db.commit()
        return PaymentFulfillOutcome.EXPIRED

    # 入账前只读校验（未 claim 锁，避免占位后无法重试）
    if order.order_type == ORDER_TYPE_SUBSCRIPTION:
        if not order.plan_id:
            logger.error("Subscription payment order %s missing plan_id", key)
            return PaymentFulfillOutcome.INVALID
        plan = await get_plan(db, int(order.plan_id))
        if not plan:
            logger.error("Subscription payment order %s plan missing", key)
            return PaymentFulfillOutcome.INVALID

    user_row = await db.execute(select(User).filter(User.id == order.user_id))
    user = user_row.scalar_one_or_none()
    if not user:
        logger.error("Payment order %s user missing", key)
        return PaymentFulfillOutcome.INVALID

    lock_held = await peek_payment_fulfill_lock(key)
    if lock_held is None:
        return PaymentFulfillOutcome.REDIS_UNAVAILABLE
    if lock_held:
        fresh = await _get_order_by_trade_no(db, key)
        if fresh and fresh.status == "completed":
            return PaymentFulfillOutcome.ALREADY_COMPLETED
        logger.info("Payment fulfill concurrent busy out_trade_no=%s", key)
        return PaymentFulfillOutcome.CONCURRENT_BUSY

    claimed = await try_claim_payment_fulfill(key)
    if claimed is None:
        return PaymentFulfillOutcome.REDIS_UNAVAILABLE
    if not claimed:
        fresh = await _get_order_by_trade_no(db, key)
        if fresh and fresh.status == "completed":
            return PaymentFulfillOutcome.ALREADY_COMPLETED
        logger.info("Payment fulfill claim busy out_trade_no=%s", key)
        return PaymentFulfillOutcome.CONCURRENT_BUSY

    try:
        fresh = await _get_order_by_trade_no(db, key)
        if not fresh:
            await db.rollback()
            return PaymentFulfillOutcome.INVALID
        if fresh.status == "completed":
            await seal_payment_fulfill(key)
            return PaymentFulfillOutcome.ALREADY_COMPLETED
        if fresh.status in ("failed", "expired"):
            await db.rollback()
            return PaymentFulfillOutcome.INVALID
        if await mark_order_expired_if_needed(db, fresh):
            await db.commit()
            return PaymentFulfillOutcome.EXPIRED

        order = fresh
        await _lock_user(db, user.id)

        balance: float | None = None
        if order.order_type == ORDER_TYPE_RECHARGE:
            balance = await grant_credits_with_log(
                db,
                user=user,
                amount=int(order.amount),
                credit_type=CREDIT_TYPE_GENERAL,
                source="recharge",
                reason=f"支付宝充值 {order.amount} 算力",
                source_ref=order.idempotency_key,
            )
        elif order.order_type == ORDER_TYPE_SUBSCRIPTION:
            plan = await get_plan(db, int(order.plan_id))
            if not plan:
                await db.rollback()
                return PaymentFulfillOutcome.INVALID
            await activate_subscription(db, user=user, plan=plan, auto_renew=True)
        else:
            await db.rollback()
            return PaymentFulfillOutcome.INVALID

        order.status = "completed"
        order.completed_at = now_cst_naive()
        if alipay_trade_no:
            order.alipay_trade_no = alipay_trade_no
        await db.flush()

        await sync_payment_order_cache_from_order(db, order, balance=balance)
        await seal_payment_fulfill(key)
        # 被邀请人首充成功后，尝试发放邀请人奖励（失败不影响入账）
        if order.order_type == ORDER_TYPE_RECHARGE:
            try:
                from .invite_referral import try_grant_inviter_on_first_recharge

                await try_grant_inviter_on_first_recharge(db, invitee_user_id=int(order.user_id))
            except Exception:  # noqa: BLE001
                logger.exception("invite inviter reward on first recharge failed user=%s", order.user_id)
        # 充值/会员支付成功站内通知
        try:
            from .user_notifications import (
                CATEGORY_RECHARGE,
                notify_user,
            )

            if order.order_type == ORDER_TYPE_RECHARGE:
                await notify_user(
                    db,
                    int(order.user_id),
                    category=CATEGORY_RECHARGE,
                    title="充值成功",
                    body=f"已到账 {int(order.amount)} 算力，可在个人中心查看余额。",
                    dedupe_key=f"recharge:success:{order.idempotency_key}",
                    link_url="/account",
                    ref_type="recharge_order",
                    ref_id=str(order.idempotency_key),
                )
            elif order.order_type == ORDER_TYPE_SUBSCRIPTION:
                await notify_user(
                    db,
                    int(order.user_id),
                    category=CATEGORY_RECHARGE,
                    title="会员开通成功",
                    body="会员已生效，权益与算力请在个人中心查看。",
                    dedupe_key=f"subscription:success:{order.idempotency_key}",
                    link_url="/account",
                    ref_type="recharge_order",
                    ref_id=str(order.idempotency_key),
                )
        except Exception:  # noqa: BLE001
            logger.exception("notify payment success failed out_trade_no=%s", key)
        return PaymentFulfillOutcome.SUCCESS
    except Exception:
        await db.rollback()
        logger.exception("Payment fulfill failed out_trade_no=%s", key)
        raise


async def try_fulfill_alipay_order(
    db: AsyncSession,
    *,
    order: CreditRechargeOrder,
    alipay_trade_no: str | None = None,
) -> PaymentFulfillOutcome:
    """支付宝异步通知专用：验签后查单 → 调用公共入账方法（查单失败不写库）。"""
    if order.status == "completed":
        return PaymentFulfillOutcome.ALREADY_COMPLETED
    if order.payment_channel != CHANNEL_ALIPAY:
        return PaymentFulfillOutcome.INVALID

    client = get_alipay_client()
    if client is None:
        return PaymentFulfillOutcome.INVALID

    query = await client.trade_query(out_trade_no=order.idempotency_key)
    if query.get("code") != "10000":
        return PaymentFulfillOutcome.INVALID
    if str(query.get("trade_status") or "") not in _ALIPAY_SUCCESS_STATUSES:
        return PaymentFulfillOutcome.INVALID

    paid_fen = _alipay_total_to_fen(str(query.get("total_amount") or ""))
    if paid_fen != int(order.pay_amount_fen or 0):
        logger.warning(
            "Alipay amount mismatch out_trade_no=%s expected=%s got=%s",
            order.idempotency_key,
            order.pay_amount_fen,
            paid_fen,
        )
        return PaymentFulfillOutcome.INVALID

    trade_no = alipay_trade_no or str(query.get("trade_no") or "") or None
    return await fulfill_paid_payment_order(
        db,
        out_trade_no=order.idempotency_key,
        alipay_trade_no=trade_no,
    )


def _alipay_total_to_fen(total_amount_yuan: str) -> int:
    try:
        return int(round(float(total_amount_yuan) * 100))
    except (TypeError, ValueError):
        return -1


async def handle_alipay_notify(db: AsyncSession, form_data: dict[str, str]) -> str:
    """处理支付宝异步通知：验签 → 官方查单 → 公共入账 → 同步缓存。

    并发占位/Redis 不可用/校验失败：仅返回 failure 供支付宝重试，不改订单状态、不加款。
    """
    try:
        client = get_alipay_client()
        if client is None:
            return "failure"

        if not client.verify_notify(form_data):
            logger.warning("Alipay notify signature invalid")
            return "failure"

        out_trade_no = str(form_data.get("out_trade_no") or "").strip()
        if not out_trade_no:
            return "failure"

        order = await _get_order_by_trade_no(db, out_trade_no)
        if not order:
            logger.warning("Alipay notify unknown order %s", out_trade_no)
            return "failure"

        trade_no = str(form_data.get("trade_no") or "") or None
        outcome = await try_fulfill_alipay_order(db, order=order, alipay_trade_no=trade_no)

        if outcome in (PaymentFulfillOutcome.SUCCESS, PaymentFulfillOutcome.ALREADY_COMPLETED):
            return "success"

        if outcome == PaymentFulfillOutcome.CONCURRENT_BUSY:
            logger.info("Alipay notify deferred (concurrent) out_trade_no=%s", out_trade_no)
        elif outcome == PaymentFulfillOutcome.REDIS_UNAVAILABLE:
            logger.warning("Alipay notify deferred (redis unavailable) out_trade_no=%s", out_trade_no)
        else:
            logger.warning("Alipay notify rejected out_trade_no=%s outcome=%s", out_trade_no, outcome.value)

        await db.rollback()
        return "failure"
    except Exception:
        await db.rollback()
        logger.exception(
            "Alipay notify handling failed out_trade_no=%s",
            form_data.get("out_trade_no"),
        )
        return "failure"


def online_payment_enabled() -> bool:
    """是否启用支付宝在线支付（用户侧唯一自助充值/开通通道）。"""
    return alipay_configured()
