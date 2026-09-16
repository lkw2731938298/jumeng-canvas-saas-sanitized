"""用户侧会员订阅 API 路由。

提供套餐列表、当前订阅状态、开通与取消订阅等接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...models.database import get_db
from ...models.user import User
from ...services.credit_flow import credits_enabled
from ...services.payment_orders import online_payment_enabled
from ...services.subscriptions import (
    cancel_subscription,
    get_plan,
    get_user_active_subscription,
    list_active_plans,
)

router = APIRouter()


class SubscriptionPlanOut(BaseModel):
    """可购买的会员套餐摘要。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    code: str
    name: str
    description: str = ""
    monthly_credits: int = Field(..., alias="monthlyCredits")
    storage_gb: int = Field(..., alias="storageGb")
    period_days: int = Field(..., alias="periodDays")
    price_cents: int = Field(..., alias="priceCents")
    sort_order: int = Field(..., alias="sortOrder")
    is_active: bool = Field(..., alias="isActive")


class UserSubscriptionOut(BaseModel):
    """用户当前或历史会员订阅详情。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    plan_id: str = Field(..., alias="planId")
    plan_code: str = Field(..., alias="planCode")
    plan_name: str = Field(..., alias="planName")
    status: str
    auto_renew: bool = Field(..., alias="autoRenew")
    monthly_credits: int = Field(..., alias="monthlyCredits")
    storage_gb: int = Field(..., alias="storageGb")
    current_period_start: str = Field(..., alias="currentPeriodStart")
    current_period_end: str = Field(..., alias="currentPeriodEnd")
    is_active: bool = Field(..., alias="isActive")
    days_remaining: int = Field(..., alias="daysRemaining")


class SubscriptionMeOut(BaseModel):
    """当前用户订阅概览（含算力开关与有效订阅）。"""

    model_config = ConfigDict(populate_by_name=True)

    credits_enabled: bool = Field(..., alias="creditsEnabled")
    subscription: UserSubscriptionOut | None = None


class SubscriptionPlansOut(BaseModel):
    """会员套餐列表响应。"""

    items: list[SubscriptionPlanOut]


class SubscribeIn(BaseModel):
    """开通会员请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    plan_id: str = Field(..., alias="planId")


@router.get("/plans", response_model=SubscriptionPlansOut)
async def list_subscription_plans(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """列出当前可购买的会员套餐（需登录）。"""
    items = await list_active_plans(db)
    return SubscriptionPlansOut(items=[SubscriptionPlanOut(**item) for item in items])


@router.get("/me", response_model=SubscriptionMeOut)
async def get_my_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户的有效会员订阅及算力开关状态。"""
    enabled = credits_enabled()
    if not enabled:
        return SubscriptionMeOut(creditsEnabled=False, subscription=None)
    pair = await get_user_active_subscription(db, current_user.id)
    if not pair:
        return SubscriptionMeOut(creditsEnabled=True, subscription=None)
    sub, plan = pair
    from ...services.subscriptions import _subscription_out

    return SubscriptionMeOut(
        creditsEnabled=True,
        subscription=UserSubscriptionOut(**_subscription_out(sub, plan)),
    )


@router.post("/subscribe", response_model=UserSubscriptionOut)
async def subscribe_plan(
    body: SubscribeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户侧演示直开通已永久关闭。

    架构约定（不对用户文案暴露）：须走支付宝扫码或管理后台发放。
    对用户统一提示支付宝开通。
    """
    _ = (body, current_user, db)
    if not credits_enabled():
        fail(ErrorCode.CREDITS_DISABLED)
    if not online_payment_enabled():
        # 用户侧不暴露「未配置/管理员」细节
        fail(ErrorCode.PAYMENT_NOT_CONFIGURED, message="在线支付暂不可用，请稍后再试")
    fail(ErrorCode.DIRECT_RECHARGE_DISABLED, message="请使用支付宝扫码开通会员")


@router.post("/cancel", response_model=UserSubscriptionOut)
async def cancel_my_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """取消当前有效会员订阅（账期内权益保留至到期）。"""
    if not credits_enabled():
        fail(ErrorCode.CREDITS_DISABLED)
    result = await cancel_subscription(db, user=current_user)
    return UserSubscriptionOut(**result)
