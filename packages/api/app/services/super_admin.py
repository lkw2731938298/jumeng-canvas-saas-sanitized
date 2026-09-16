"""启动时确保配置的超级管理员账号具备超管身份。"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.admin_permissions import parse_super_admin_phones
from ..core.config import get_settings
from ..models.user import User

logger = logging.getLogger(__name__)


async def ensure_super_admins(db: AsyncSession) -> None:
    """按 SUPER_ADMIN_PHONES 将对应账号升为管理员并标记 is_super_admin。

    手机号尚不存在时创建占位账号（无密码，需后续设置密码/短信登录）。
    """
    settings = get_settings()
    phones = parse_super_admin_phones(getattr(settings, "super_admin_phones", "") or "")
    if not phones:
        return

    default_credits = max(settings.default_user_credits, 0)

    for phone in phones:
        result = await db.execute(select(User).where(User.phone == phone))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                source_user_id=f"super_admin_{phone}",
                phone=phone,
                display_name="超级管理员",
                role="admin",
                is_super_admin=True,
                admin_permissions=None,
                compute_power=default_credits,
            )
            db.add(user)
            await db.flush()
            logger.info("Created super admin placeholder for phone %s (id=%s)", phone, user.id)
            continue

        changed = False
        if user.role != "admin":
            user.role = "admin"
            changed = True
        if not bool(getattr(user, "is_super_admin", False)):
            user.is_super_admin = True
            changed = True
        if changed:
            await db.flush()
            logger.info("Promoted phone %s (id=%s) to super admin", phone, user.id)
