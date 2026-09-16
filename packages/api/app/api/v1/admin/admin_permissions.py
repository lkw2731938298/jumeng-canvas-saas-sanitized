"""管理员功能权限目录与分配接口。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import (
    ADMIN_PERMISSION_CATALOG,
    ALL_ADMIN_PERMISSION_KEYS,
    PERM_ADMIN_PERMISSIONS,
    admin_has_permission,
    normalize_permission_list,
    resolved_permissions,
)
from ....core.deps import require_permission
from ....core.entity_ids import format_user_display_id, resolve_user_by_ref
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User

router = APIRouter()


class PermissionCatalogItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    key: str
    label: str
    group: str


class PermissionCatalogOut(BaseModel):
    items: list[PermissionCatalogItemOut]


class AdminAccountPermissionsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    user_no: str | None = Field(None, alias="userNo")
    display_name: str
    phone: str | None = None
    role: str
    is_super_admin: bool = Field(False, alias="isSuperAdmin")
    is_active: bool = True
    permissions: list[str]


class AdminAccountPermissionsListOut(BaseModel):
    items: list[AdminAccountPermissionsOut]


class AdminPermissionsPatchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    permissions: list[str]


def _account_out(user: User) -> AdminAccountPermissionsOut:
    return AdminAccountPermissionsOut(
        id=str(user.id),
        user_no=format_user_display_id(user.id),
        display_name=user.display_name or "",
        phone=user.phone,
        role=user.role or "user",
        is_super_admin=bool(getattr(user, "is_super_admin", False)),
        is_active=user.is_active is not False,
        permissions=resolved_permissions(user),
    )


@router.get("/admin-permissions/catalog", response_model=PermissionCatalogOut)
async def get_admin_permission_catalog(
    _: User = Depends(require_permission(PERM_ADMIN_PERMISSIONS)),
):
    """返回可分配的功能权限目录（中文标签 + 分组）。"""
    return PermissionCatalogOut(
        items=[PermissionCatalogItemOut(**item) for item in ADMIN_PERMISSION_CATALOG]
    )


@router.get("/admin-permissions/admins", response_model=AdminAccountPermissionsListOut)
async def list_admin_accounts_with_permissions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_ADMIN_PERMISSIONS)),
):
    """列出全部管理员及其当前生效权限。"""
    result = await db.execute(
        select(User)
        .where(User.role == "admin")
        .order_by(User.is_super_admin.desc(), User.id.asc())
    )
    admins = list(result.scalars().all())
    return AdminAccountPermissionsListOut(items=[_account_out(u) for u in admins])


@router.put("/admin-permissions/admins/{user_id}", response_model=AdminAccountPermissionsOut)
async def patch_admin_account_permissions(
    user_id: str,
    body: AdminPermissionsPatchIn,
    db: AsyncSession = Depends(get_db),
    operator: User = Depends(require_permission(PERM_ADMIN_PERMISSIONS)),
):
    """为指定管理员设置功能权限（不可改超管；非超管只能授出自己已有的权限）。"""
    target = await resolve_user_by_ref(db, user_id)

    if target.role != "admin":
        fail(ErrorCode.INVALID_ROLE, message="只能给管理员账号分配功能权限")

    if bool(getattr(target, "is_super_admin", False)):
        fail(ErrorCode.SUPER_ADMIN_PROTECTED)

    requested = normalize_permission_list(body.permissions) or []
    unknown = [p for p in (body.permissions or []) if str(p).strip() and str(p).strip() not in ALL_ADMIN_PERMISSION_KEYS]
    if unknown:
        fail(ErrorCode.BAD_REQUEST, message=f"未知权限键: {', '.join(unknown)}")

    # 非超管操作者只能授出自己已拥有的权限（含「管理员权限设置」）
    if not bool(getattr(operator, "is_super_admin", False)):
        for key in requested:
            if not admin_has_permission(operator, key):
                fail(
                    ErrorCode.ADMIN_PERMISSION_DENIED,
                    message=f"不能授出当前账号没有的权限: {key}",
                )

    target.admin_permissions = requested
    await db.flush()
    return _account_out(target)
