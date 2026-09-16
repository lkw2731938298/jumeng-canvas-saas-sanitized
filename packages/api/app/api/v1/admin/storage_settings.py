"""管理端 — 平台通用云存储配额配置。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_STORAGE
from ....models.database import get_db
from ....models.user import User
from ....services.platform_settings import get_storage_settings, set_default_storage_gb_setting

router = APIRouter()


class AdminStorageSettingsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_storage_gb: int = Field(..., alias="defaultStorageGb")
    env_default_storage_gb: int = Field(..., alias="envDefaultStorageGb")
    updated_at: str = Field(..., alias="updatedAt")


class AdminStorageSettingsUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_storage_gb: int = Field(..., alias="defaultStorageGb", ge=0, le=10_000)


@router.get("/storage/settings", response_model=AdminStorageSettingsOut)
async def get_admin_storage_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_STORAGE)),
):
    """读取平台通用云存储配额（GiB）。"""
    payload = await get_storage_settings(db)
    return AdminStorageSettingsOut(
        defaultStorageGb=payload["defaultStorageGb"],
        envDefaultStorageGb=payload["envDefaultStorageGb"],
        updatedAt=payload["updatedAt"].isoformat() if payload.get("updatedAt") else "",
    )


@router.put("/storage/settings", response_model=AdminStorageSettingsOut)
async def put_admin_storage_settings(
    body: AdminStorageSettingsUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_STORAGE)),
):
    """设置平台通用云存储配额（GiB）。"""
    payload = await set_default_storage_gb_setting(db, body.default_storage_gb)
    return AdminStorageSettingsOut(
        defaultStorageGb=payload["defaultStorageGb"],
        envDefaultStorageGb=payload["envDefaultStorageGb"],
        updatedAt=payload["updatedAt"].isoformat() if payload.get("updatedAt") else "",
    )
