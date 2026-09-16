"""Ensure the built-in dev/admin user can log in with a password."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.security import hash_password
from ..models.user import User

logger = logging.getLogger(__name__)

DEV_USER_ID = 1
DEV_PHONE = "13800000000"


async def ensure_dev_admin_password(db: AsyncSession) -> None:
    """
    When DEV_ADMIN_PASSWORD is set, ensure the default admin user exists and has
    that password (only writes if password_hash is currently empty).
    """
    settings = get_settings()
    password = (settings.dev_admin_password or "").strip()
    default_credits = max(settings.default_user_credits, 0)
    if not password:
        return

    result = await db.execute(select(User).filter(User.id == DEV_USER_ID))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            id=DEV_USER_ID,
            source_user_id="dev_user",
            phone=DEV_PHONE,
            display_name="本地管理员",
            role="admin",
            compute_power=default_credits,
        )
        db.add(user)
        await db.flush()

    if user.role != "admin":
        user.role = "admin"
    if not user.phone:
        user.phone = DEV_PHONE
    if user.source_user_id != "dev_user":
        user.source_user_id = str(user.id)
    if int(user.compute_power or 0) <= 0 and default_credits > 0:
        user.compute_power = default_credits

    if user.password_hash:
        return

    user.password_hash = hash_password(password)
    logger.info("Dev admin login enabled for phone %s (user id %s)", user.phone, user.id)
