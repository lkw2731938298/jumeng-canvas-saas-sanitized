"""用户侧在线支付 API（支付宝扫码 + 异步通知）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.entity_ids import require_entity_id
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...models.database import get_db
from ...models.user import User
from ...services.alipay_notify_http import handle_alipay_notify_http
from ...services.credit_flow import credits_enabled
from ...services.credit_operation_lock import credit_recharge_lock
from ...services.payment_orders import (
    create_alipay_recharge_order,
    create_alipay_subscription_order,
    get_payment_order_for_user,
    online_payment_enabled,
)
from ...services.payment_tiers import get_active_recharge_amounts, get_recharge_prices_fen_map
from ...services.subscriptions import get_plan

router = APIRouter()


@router.post("/alipay-notify")
async def alipay_notify(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """支付宝异步通知（权威路径，无 admin 前缀）：验签 → 查单 → 入账。"""
    return await handle_alipay_notify_http(request, db)


class PaymentConfigOut(BaseModel):
    """支付能力与充值档位。用户侧仅支付宝；directRechargeEnabled 恒 false（兼容旧前端）。"""

    model_config = ConfigDict(populate_by_name=True)

    alipay_enabled: bool = Field(..., alias="alipayEnabled")
    # 兼容字段：无支付直连已永久关闭，恒返回 false
    direct_recharge_enabled: bool = Field(False, alias="directRechargeEnabled")
    recharge_tiers: list[int] = Field(..., alias="rechargeTiers")
    recharge_tier_prices_fen: dict[str, int] = Field(..., alias="rechargeTierPricesFen")


class AlipayRechargePrecreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    amount: int = Field(..., ge=1, description="充值算力点数")


class AlipaySubscriptionPrecreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plan_id: str = Field(..., alias="planId")


class PaymentPrecreateOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    out_trade_no: str = Field(..., alias="outTradeNo")
    order_type: str = Field(..., alias="orderType")
    amount: int
    pay_amount_fen: int = Field(..., alias="payAmountFen")
    pay_amount_yuan: str = Field(..., alias="payAmountYuan")
    status: str
    payment_channel: str = Field(..., alias="paymentChannel")
    qr_code: str = Field(..., alias="qrCode")
    subject: str
    plan_id: str | None = Field(None, alias="planId")
    plan_name: str | None = Field(None, alias="planName")


class PaymentOrderOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    out_trade_no: str = Field(..., alias="outTradeNo")
    order_type: str = Field(..., alias="orderType")
    amount: int
    pay_amount_fen: int = Field(..., alias="payAmountFen")
    status: str
    payment_channel: str = Field(..., alias="paymentChannel")
    plan_id: str | None = Field(None, alias="planId")
    completed_at: str | None = Field(None, alias="completedAt")
    balance: float | None = None


@router.get("/config", response_model=PaymentConfigOut)
async def get_payment_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """查询当前环境支付能力（仅支付宝）及充值档位。"""
    amounts = await get_active_recharge_amounts(db)
    price_map = await get_recharge_prices_fen_map(db)
    return PaymentConfigOut(
        alipayEnabled=online_payment_enabled(),
        directRechargeEnabled=False,
        rechargeTiers=list(amounts),
        rechargeTierPricesFen={str(k): v for k, v in price_map.items()},
    )


@router.post("/alipay/recharge", response_model=PaymentPrecreateOut)
async def alipay_recharge_precreate(
    body: AlipayRechargePrecreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建算力充值支付宝扫码单。"""
    if not credits_enabled():
        fail(ErrorCode.CREDITS_DISABLED)
    if not online_payment_enabled():
        fail(ErrorCode.PAYMENT_NOT_CONFIGURED)
    async with credit_recharge_lock(current_user.id):
        data = await create_alipay_recharge_order(db, user=current_user, credits_amount=body.amount)
    return PaymentPrecreateOut(
        outTradeNo=data["outTradeNo"],
        orderType=data["orderType"],
        amount=data["amount"],
        payAmountFen=data["payAmountFen"],
        payAmountYuan=data["payAmountYuan"],
        status=data["status"],
        paymentChannel=data["paymentChannel"],
        qrCode=data["qrCode"],
        subject=data["subject"],
        planId=data.get("planId"),
        planName=None,
    )


@router.post("/alipay/subscription", response_model=PaymentPrecreateOut)
async def alipay_subscription_precreate(
    body: AlipaySubscriptionPrecreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建会员开通支付宝扫码单。"""
    if not credits_enabled():
        fail(ErrorCode.CREDITS_DISABLED)
    if not online_payment_enabled():
        fail(ErrorCode.PAYMENT_NOT_CONFIGURED)
    plan_id_int = require_entity_id(body.plan_id, message="会员套餐 ID 无效")
    plan = await get_plan(db, plan_id_int)
    if not plan:
        fail(ErrorCode.SUBSCRIPTION_PLAN_NOT_FOUND)
    async with credit_recharge_lock(current_user.id):
        data = await create_alipay_subscription_order(db, user=current_user, plan=plan)
    return PaymentPrecreateOut(
        outTradeNo=data["outTradeNo"],
        orderType=data["orderType"],
        amount=data["amount"],
        payAmountFen=data["payAmountFen"],
        payAmountYuan=data["payAmountYuan"],
        status=data["status"],
        paymentChannel=data["paymentChannel"],
        qrCode=data["qrCode"],
        subject=data["subject"],
        planId=data.get("planId"),
        planName=data.get("planName"),
    )


@router.get("/orders/{out_trade_no}", response_model=PaymentOrderOut)
async def get_payment_order(
    out_trade_no: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询支付订单状态（只读 Redis 缓存，miss 回填 DB；不触发入账）。"""
    data = await get_payment_order_for_user(db, user=current_user, out_trade_no=out_trade_no)
    return PaymentOrderOut(
        outTradeNo=data["outTradeNo"],
        orderType=data["orderType"],
        amount=data["amount"],
        payAmountFen=data["payAmountFen"],
        status=data["status"],
        paymentChannel=data["paymentChannel"],
        planId=data.get("planId"),
        completedAt=data.get("completedAt"),
        balance=data.get("balance"),
    )
