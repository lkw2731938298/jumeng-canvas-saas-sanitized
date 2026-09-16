"""管理端 — 算力充值档位配置。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_RECHARGE_TIERS
from ....models.database import get_db
from ....models.user import User
from ....services.platform_settings import get_recharge_tiers_settings, set_recharge_tiers_settings

router = APIRouter()


class AdminRechargeTierItemIn(BaseModel):
    """单条充值档位（算力点 ↔ 支付分）。"""

    model_config = ConfigDict(populate_by_name=True)

    credits_amount: int = Field(..., alias="creditsAmount", ge=1)
    pay_amount_fen: int = Field(..., alias="payAmountFen", ge=1)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)
    is_active: bool = Field(True, alias="isActive")


class AdminRechargeTierItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    credits_amount: int = Field(..., alias="creditsAmount")
    pay_amount_fen: int = Field(..., alias="payAmountFen")
    sort_order: int = Field(..., alias="sortOrder")
    is_active: bool = Field(..., alias="isActive")


class AdminRechargeTiersOut(BaseModel):
    items: list[AdminRechargeTierItemOut]
    updated_at: str = Field(..., alias="updatedAt")


class AdminRechargeTiersUpdateIn(BaseModel):
    items: list[AdminRechargeTierItemIn]


@router.get("/recharge-tiers", response_model=AdminRechargeTiersOut)
async def get_admin_recharge_tiers(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_RECHARGE_TIERS)),
):
    """读取算力充值档位配置。"""
    payload = await get_recharge_tiers_settings(db)
    return AdminRechargeTiersOut(
        items=[AdminRechargeTierItemOut(**item) for item in payload["items"]],
        updatedAt=payload["updatedAt"].isoformat() if payload.get("updatedAt") else "",
    )


@router.put("/recharge-tiers", response_model=AdminRechargeTiersOut)
async def put_admin_recharge_tiers(
    body: AdminRechargeTiersUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_RECHARGE_TIERS)),
):
    """保存算力充值档位（整表替换，至少保留一条启用档位）。"""
    raw = [
        {
            "creditsAmount": item.credits_amount,
            "payAmountFen": item.pay_amount_fen,
            "sortOrder": item.sort_order,
            "isActive": item.is_active,
        }
        for item in body.items
    ]
    if not any(item.is_active for item in body.items):
        from ....core.error_codes import ErrorCode
        from ....core.errors import fail

        fail(ErrorCode.INVALID_RECHARGE_AMOUNT, message="至少保留一条启用的充值档位")
    payload = await set_recharge_tiers_settings(db, raw)
    return AdminRechargeTiersOut(
        items=[AdminRechargeTierItemOut(**item) for item in payload["items"]],
        updatedAt=payload["updatedAt"].isoformat() if payload.get("updatedAt") else "",
    )
