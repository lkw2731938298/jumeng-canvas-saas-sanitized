"""Admin membership plan and subscription management."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.entity_ids import require_entity_id, resolve_user_by_ref
from ....core.deps import require_permission
from ....core.admin_permissions import PERM_SUBSCRIPTION_PLANS
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....services.subscriptions import (
    _plan_out,
    activate_subscription,
    create_plan,
    delete_plan,
    get_plan,
    list_all_plans,
    update_plan,
)

router = APIRouter()


class AdminSubscriptionPlanOut(BaseModel):
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


class AdminSubscriptionPlanListOut(BaseModel):
    items: list[AdminSubscriptionPlanOut]


class AdminSubscriptionPlanCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    name: str
    description: str = ""
    # 每期赠送算力：不设上限，允许 0（仅扩容存储/占位套餐），发放时 amount<=0 会跳过加款
    monthly_credits: int = Field(..., alias="monthlyCredits", ge=0)
    # 额外存储：不设上限（GiB）；服务层会对负值 clamp 到 0
    storage_gb: int = Field(0, alias="storageGb", ge=0)
    # 账期天数：放宽到 3650（10 年），支持年度/多年套餐
    period_days: int = Field(30, alias="periodDays", ge=1, le=3650)
    price_cents: int = Field(0, alias="priceCents", ge=0)
    sort_order: int = Field(0, alias="sortOrder")
    is_active: bool = Field(True, alias="isActive")


class AdminSubscriptionPlanUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = None
    description: Optional[str] = None
    monthly_credits: Optional[int] = Field(None, alias="monthlyCredits", ge=0)
    storage_gb: Optional[int] = Field(None, alias="storageGb", ge=0)
    period_days: Optional[int] = Field(None, alias="periodDays", ge=1, le=3650)
    price_cents: Optional[int] = Field(None, alias="priceCents", ge=0)
    sort_order: Optional[int] = Field(None, alias="sortOrder")
    is_active: Optional[bool] = Field(None, alias="isActive")


class AdminGrantSubscriptionIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plan_id: str = Field(..., alias="planId")
    auto_renew: bool = Field(True, alias="autoRenew")


@router.get("/subscription-plans", response_model=AdminSubscriptionPlanListOut)
async def list_admin_subscription_plans(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SUBSCRIPTION_PLANS)),
):
    items = await list_all_plans(db)
    return AdminSubscriptionPlanListOut(
        items=[AdminSubscriptionPlanOut(**item) for item in items]
    )


@router.post("/subscription-plans", response_model=AdminSubscriptionPlanOut)
async def create_admin_subscription_plan(
    body: AdminSubscriptionPlanCreateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SUBSCRIPTION_PLANS)),
):
    plan = await create_plan(
        db,
        code=body.code,
        name=body.name,
        description=body.description,
        monthly_credits=body.monthly_credits,
        storage_gb=body.storage_gb,
        period_days=body.period_days,
        price_cents=body.price_cents,
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    return AdminSubscriptionPlanOut(**_plan_out(plan))


@router.patch("/subscription-plans/{plan_id}", response_model=AdminSubscriptionPlanOut)
async def patch_admin_subscription_plan(
    plan_id: str,
    body: AdminSubscriptionPlanUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SUBSCRIPTION_PLANS)),
):
    plan_id_int = require_entity_id(plan_id, message="套餐 ID 无效")
    plan = await get_plan(db, plan_id_int)
    if not plan:
        fail(ErrorCode.SUBSCRIPTION_PLAN_NOT_FOUND)
    payload = body.model_dump(exclude_unset=True, by_alias=False)
    plan = await update_plan(db, plan, **payload)
    return AdminSubscriptionPlanOut(**_plan_out(plan))


@router.delete("/subscription-plans/{plan_id}")
async def delete_admin_subscription_plan(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_SUBSCRIPTION_PLANS)),
):
    """删除会员套餐（已有用户订阅记录时禁止删除，返回 409 提示改为下架）。"""
    plan_id_int = require_entity_id(plan_id, message="套餐 ID 无效")
    plan = await get_plan(db, plan_id_int)
    if not plan:
        fail(ErrorCode.SUBSCRIPTION_PLAN_NOT_FOUND)
    await delete_plan(db, plan)
    return {"ok": True}


@router.post("/subscriptions/users/{user_id}/grant")
async def grant_user_subscription(
    user_id: str,
    body: AdminGrantSubscriptionIn,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission(PERM_SUBSCRIPTION_PLANS)),
):
    """为用户开通会员：`user_id` 支持手机号、展示 ID 或用户主键（resolve_user_by_ref 统一解析）。"""
    user = await resolve_user_by_ref(db, user_id)
    plan_id_int = require_entity_id(body.plan_id, message="套餐 ID 无效")
    plan = await get_plan(db, plan_id_int)
    if not plan:
        fail(ErrorCode.SUBSCRIPTION_PLAN_NOT_FOUND)

    sub_out = await activate_subscription(
        db,
        user=user,
        plan=plan,
        auto_renew=body.auto_renew,
        operator_id=admin.id,
    )
    return {"ok": True, "subscription": sub_out}
