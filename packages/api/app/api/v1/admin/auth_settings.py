"""Admin auth settings and audit APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_USERS
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.auth_event import AuthEvent
from ....models.database import get_db
from ....models.user import User
from ....services.auth_risk import clear_all_phone_auth_risk
from ....services.platform_settings import get_auth_settings, set_registration_enabled
from .users import _resolve_admin_user

router = APIRouter()


class AdminAuthSettingsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    registration_enabled: bool = Field(..., alias="registrationEnabled")
    env_registration_default: bool = Field(..., alias="envRegistrationDefault")
    updated_at: datetime = Field(..., alias="updatedAt")


class AdminAuthSettingsPatchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    registration_enabled: bool = Field(..., alias="registrationEnabled")


class AdminAuthEventOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_id: Optional[str] = Field(None, alias="userId")
    phone: Optional[str] = None
    action: str
    result: str
    ip: str = ""
    user_agent: Optional[str] = Field(None, alias="userAgent")
    detail: Optional[dict] = None
    created_at: datetime = Field(..., alias="createdAt")


class AdminAuthEventListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[AdminAuthEventOut]
    total: int
    page: int = Field(..., alias="page")
    page_size: int = Field(..., alias="pageSize")


class AdminClearAuthRiskOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    phone: str = ""


@router.get("/settings/auth", response_model=AdminAuthSettingsOut)
async def get_admin_auth_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    data = await get_auth_settings(db)
    return AdminAuthSettingsOut(
        registrationEnabled=data["registration_enabled"],
        envRegistrationDefault=data["env_registration_default"],
        updatedAt=data["updated_at"],
    )


@router.patch("/settings/auth", response_model=AdminAuthSettingsOut)
async def patch_admin_auth_settings(
    body: AdminAuthSettingsPatchIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    data = await set_registration_enabled(db, body.registration_enabled)
    return AdminAuthSettingsOut(
        registrationEnabled=data["registration_enabled"],
        envRegistrationDefault=data["env_registration_default"],
        updatedAt=data["updated_at"],
    )


@router.get("/users/{user_id}/auth-events", response_model=AdminAuthEventListOut)
async def list_admin_user_auth_events(
    user_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    user = await _resolve_admin_user(db, user_id)

    if user.phone:
        condition = or_(AuthEvent.user_id == user.id, AuthEvent.phone == user.phone)
    else:
        condition = AuthEvent.user_id == user.id

    total = (await db.execute(select(func.count(AuthEvent.id)).filter(condition))).scalar_one()
    offset = (page - 1) * page_size
    rows = (
        await db.execute(
            select(AuthEvent)
            .filter(condition)
            .order_by(AuthEvent.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()

    return AdminAuthEventListOut(
        items=[
            AdminAuthEventOut(
                id=str(row.id),
                userId=str(row.user_id) if row.user_id else None,
                phone=row.phone,
                action=row.action,
                result=row.result,
                ip=row.ip or "",
                userAgent=row.user_agent,
                detail=row.detail if isinstance(row.detail, dict) else None,
                createdAt=row.created_at,
            )
            for row in rows
        ],
        total=int(total or 0),
        page=page,
        pageSize=page_size,
    )


@router.post("/users/{user_id}/clear-auth-risk", response_model=AdminClearAuthRiskOut)
async def clear_admin_user_auth_risk(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_USERS)),
):
    user = await _resolve_admin_user(db, user_id)
    if not user.phone:
        fail(ErrorCode.PHONE_REQUIRED)
    await clear_all_phone_auth_risk(user.phone)
    return AdminClearAuthRiskOut(ok=True, phone=user.phone)
