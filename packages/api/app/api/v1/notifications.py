"""用户站内通知 API。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.errors import ok
from ...models.database import get_db
from ...models.user import User
from ...services import user_notifications as notify_svc

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=50, alias="pageSize"),
    unreadOnly: bool = Query(False, alias="unreadOnly"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """当前用户通知列表。"""
    data = await notify_svc.list_user_notifications(
        db,
        int(current_user.id),
        page=page,
        page_size=pageSize,
        unread_only=unreadOnly,
    )
    return ok(data)


@router.get("/unread-count")
async def unread_notification_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """未读角标。"""
    n = await notify_svc.get_unread_count(db, int(current_user.id))
    return ok({"unreadCount": n})


@router.post("/read-all")
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """全部已读（须在 /{id}/read 之前注册）。"""
    data = await notify_svc.mark_all_notifications_read(db, int(current_user.id))
    await db.commit()
    return ok(data)


@router.post("/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """单条已读。"""
    data = await notify_svc.mark_notification_read(
        db, int(current_user.id), notification_id
    )
    await db.commit()
    return ok(data)
