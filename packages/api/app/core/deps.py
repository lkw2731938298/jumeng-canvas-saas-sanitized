from collections.abc import Callable
from typing import Any

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..models.database import get_db
from ..models.user import User
from ..core.admin_permissions import admin_has_permission
from ..core.config import get_settings
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..services.auth_session import resolve_user_from_token

_DEV_USER_ID = 1


async def _get_dev_user(db: AsyncSession) -> User:
    result = await db.execute(select(User).filter(User.id == _DEV_USER_ID))
    user = result.scalar_one_or_none()
    if not user:
        settings = get_settings()
        user = User(
            id=_DEV_USER_ID,
            source_user_id="dev_user",
            display_name="本地用户",
            phone="13800000000",
            role="admin",
            is_super_admin=True,
            compute_power=max(settings.default_user_credits, 0),
        )
        db.add(user)
        await db.flush()
    elif user.role != "admin":
        user.role = "admin"
    # 本地开发占位账号视为超管，便于联调全部后台功能
    if not bool(getattr(user, "is_super_admin", False)):
        user.is_super_admin = True
    await db.flush()
    return user


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    return token or None


async def _resolve_user(authorization: str | None, db: AsyncSession) -> User | None:
    token = _extract_bearer_token(authorization)
    if not token:
        return None
    return await resolve_user_from_token(db, token)


async def get_current_user(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await _resolve_user(authorization, db)
    if user:
        return user
    settings = get_settings()
    if settings.debug or settings.admin_auth_disabled:
        return await _get_dev_user(db)
    fail(ErrorCode.UNAUTHORIZED)


async def get_current_user_optional(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """可选登录：有有效 token 则返回用户，否则 None（不强制登录）。"""
    user = await _resolve_user(authorization, db)
    if user:
        return user
    settings = get_settings()
    if settings.debug or settings.admin_auth_disabled:
        return await _get_dev_user(db)
    return None


# Backward-compatible alias（历史调用方视为强制登录）
get_optional_user = get_current_user


async def get_openapi_user(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """外部 Agent OpenAPI：仅接受 Access Key（jm_ak_…），不接受用户会话 token。"""
    token = _extract_bearer_token(authorization)
    if not token:
        fail(ErrorCode.UNAUTHORIZED, message="请在 Authorization: Bearer 中携带 Access Key")
    from ..services.agent_access_keys import resolve_user_from_access_key

    user = await resolve_user_from_access_key(db, token)
    if not user:
        fail(ErrorCode.UNAUTHORIZED, message="Access Key 无效或已吊销")
    return user


async def require_admin(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    settings = get_settings()
    if settings.admin_auth_disabled:
        return await get_current_user(authorization, db)

    user = await _resolve_user(authorization, db)
    if not user and settings.debug:
        user = await _get_dev_user(db)
    if not user:
        fail(ErrorCode.UNAUTHORIZED)
    if user.role != "admin":
        fail(ErrorCode.ADMIN_REQUIRED)
    return user


def require_permission(permission: str) -> Callable[..., Any]:
    """要求当前用户为管理员且拥有指定功能权限（超管默认通过）。"""

    async def _dependency(user: User = Depends(require_admin)) -> User:
        settings = get_settings()
        # 开发关闭管理鉴权时不做细粒度拦截
        if settings.admin_auth_disabled:
            return user
        if not admin_has_permission(user, permission):
            fail(ErrorCode.ADMIN_PERMISSION_DENIED)
        return user

    return _dependency
