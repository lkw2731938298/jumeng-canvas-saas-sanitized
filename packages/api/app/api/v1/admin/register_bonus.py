"""Admin：首次注册赠送开关/点数与发放列表。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_USERS
from ....core.deps import require_permission
from ....models.database import get_db
from ....models.user import User
from ....services.platform_settings import get_register_bonus_settings, set_register_bonus_settings
from ....services.register_bonus import list_register_bonus_grants

router = APIRouter()


class RegisterBonusSettingsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    amount: int
    updated_at: datetime = Field(..., alias="updatedAt")


class RegisterBonusSettingsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    amount: int = Field(..., ge=0, le=1_000_000)


class RegisterBonusGrantOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_id: str = Field(..., alias="userId")
    display_name: str = Field("", alias="displayName")
    phone: str = ""
    phone_masked: str = Field("", alias="phoneMasked")
    amount: int
    status: str
    created_at: Optional[datetime] = Field(None, alias="createdAt")
    granted_at: Optional[datetime] = Field(None, alias="grantedAt")


class RegisterBonusGrantListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[RegisterBonusGrantOut]
    total: int
    page: int
    page_size: int = Field(..., alias="pageSize")


@router.get("/register-bonus", response_model=RegisterBonusSettingsOut)
async def get_admin_register_bonus(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    data = await get_register_bonus_settings(db)
    return RegisterBonusSettingsOut(
        enabled=bool(data["enabled"]),
        amount=int(data["amount"] or 0),
        updatedAt=data["updated_at"],
    )


@router.put("/register-bonus", response_model=RegisterBonusSettingsOut)
async def put_admin_register_bonus(
    body: RegisterBonusSettingsIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    data = await set_register_bonus_settings(db, enabled=body.enabled, amount=body.amount)
    await db.commit()
    return RegisterBonusSettingsOut(
        enabled=bool(data["enabled"]),
        amount=int(data["amount"] or 0),
        updatedAt=data["updated_at"],
    )


@router.get("/register-bonus/grants", response_model=RegisterBonusGrantListOut)
async def list_admin_register_bonus_grants(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", max_length=64),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    data = await list_register_bonus_grants(db, page=page, page_size=page_size, search=search)
    return RegisterBonusGrantListOut(
        items=[RegisterBonusGrantOut.model_validate(item) for item in data["items"]],
        total=int(data["total"]),
        page=int(data["page"]),
        pageSize=int(data["pageSize"]),
    )
