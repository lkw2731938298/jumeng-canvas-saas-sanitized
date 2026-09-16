"""支付订单 Redis 缓存：轮询只读；入账/建单后必须与 DB 同步写入。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.credit import CreditRechargeOrder
from .redis_client import get_redis

logger = logging.getLogger(__name__)

# 已完成订单缓存 7 天；pending 与支付宝 timeout_express(15m) 对齐留余量
PAYMENT_ORDER_CACHE_TTL_COMPLETED_SEC = 7 * 24 * 3600
PAYMENT_ORDER_CACHE_TTL_PENDING_SEC = 30 * 60


def payment_order_cache_key(out_trade_no: str) -> str:
    return f"payment:order:{out_trade_no.strip()}"


def order_to_cache_payload(
    order: CreditRechargeOrder,
    *,
    balance: float | None = None,
) -> dict[str, Any]:
    """将 DB 订单行转为缓存 JSON 结构（含 userId 供轮询鉴权）。"""
    return {
        "outTradeNo": order.idempotency_key,
        "orderType": order.order_type,
        "amount": int(order.amount or 0),
        "payAmountFen": int(order.pay_amount_fen or 0),
        "status": order.status,
        "paymentChannel": order.payment_channel,
        "planId": str(order.plan_id) if order.plan_id else None,
        "completedAt": order.completed_at.isoformat() if order.completed_at else None,
        "userId": int(order.user_id),
        "balance": balance,
    }


def cache_payload_to_api(payload: dict[str, Any]) -> dict[str, Any]:
    """缓存 → 用户侧 API 响应字段。"""
    return {
        "outTradeNo": payload["outTradeNo"],
        "orderType": payload["orderType"],
        "amount": payload["amount"],
        "payAmountFen": payload["payAmountFen"],
        "status": payload["status"],
        "paymentChannel": payload["paymentChannel"],
        "planId": payload.get("planId"),
        "completedAt": payload.get("completedAt"),
        "balance": payload.get("balance"),
    }


async def get_payment_order_cache(out_trade_no: str) -> dict[str, Any] | None:
    """读取订单缓存；Redis 不可用返回 None（由调用方回退 DB）。"""
    client = await get_redis()
    if client is None:
        return None
    raw = await client.get(payment_order_cache_key(out_trade_no))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        logger.warning("Invalid payment order cache json out_trade_no=%s", out_trade_no)
        return None


async def set_payment_order_cache(payload: dict[str, Any]) -> None:
    """写入/更新订单缓存（与 DB 状态同步）。"""
    client = await get_redis()
    if client is None:
        return
    out_trade_no = str(payload.get("outTradeNo") or "").strip()
    if not out_trade_no:
        return
    status = str(payload.get("status") or "")
    ttl = (
        PAYMENT_ORDER_CACHE_TTL_COMPLETED_SEC
        if status == "completed"
        else PAYMENT_ORDER_CACHE_TTL_PENDING_SEC
    )
    await client.set(
        payment_order_cache_key(out_trade_no),
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ex=ttl,
    )


async def sync_payment_order_cache_from_order(
    db: AsyncSession,
    order: CreditRechargeOrder,
    *,
    balance: float | None = None,
) -> None:
    """DB 订单变更后刷新 Redis 缓存（入账/建单/回填必调）。"""
    payload = order_to_cache_payload(order, balance=balance)
    await set_payment_order_cache(payload)
