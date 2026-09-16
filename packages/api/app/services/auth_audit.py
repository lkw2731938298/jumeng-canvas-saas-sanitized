"""认证审计事件持久化到 auth_events 表。"""

from __future__ import annotations

import logging
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.auth_event import AuthEvent

logger = logging.getLogger(__name__)


async def log_auth_event(
    db: AsyncSession,
    *,
    action: str,
    result: str,
    phone: str | None = None,
    user_id: int | None = None,
    ip: str = "",
    user_agent: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    """记录登录/注册等认证行为审计（失败不阻断主流程）。"""
    try:
        db.add(
            AuthEvent(
                user_id=user_id,
                phone=phone or None,
                action=action,
                result=result,
                ip=ip or "",
                user_agent=user_agent or None,
                detail=detail or None,
            )
        )
        await db.flush()
    except Exception:
        logger.exception("Failed to write auth event action=%s result=%s", action, result)
