"""用户站内通知：写入、列表、已读。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.user_notification import UserNotification

logger = logging.getLogger(__name__)

CATEGORY_RECHARGE = "recharge"
CATEGORY_JOB = "job"
CATEGORY_SKILL_REVIEW = "skill_review"
CATEGORY_WORKFLOW_REVIEW = "workflow_review"

_VALID_CATEGORIES = frozenset(
    {
        CATEGORY_RECHARGE,
        CATEGORY_JOB,
        CATEGORY_SKILL_REVIEW,
        CATEGORY_WORKFLOW_REVIEW,
    }
)


def notification_to_dict(row: UserNotification) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "category": str(row.category or ""),
        "title": str(row.title or ""),
        "body": str(row.body or ""),
        "linkUrl": str(row.link_url) if row.link_url else None,
        "refType": str(row.ref_type) if row.ref_type else None,
        "refId": str(row.ref_id) if row.ref_id else None,
        "isRead": bool(row.is_read),
        "createdAt": to_cst_iso(row.created_at) or "",
    }


async def notify_user(
    db: AsyncSession,
    user_id: int,
    *,
    category: str,
    title: str,
    body: str,
    dedupe_key: str | None = None,
    link_url: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> None:
    """写入一条站内通知（savepoint 幂等）；失败只打日志，不打断主业务。"""
    await notify_user_safe(
        db,
        user_id,
        category=category,
        title=title,
        body=body,
        dedupe_key=dedupe_key,
        link_url=link_url,
        ref_type=ref_type,
        ref_id=ref_id,
    )


async def notify_user_safe(
    db: AsyncSession,
    user_id: int,
    *,
    category: str,
    title: str,
    body: str,
    dedupe_key: str | None = None,
    link_url: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
) -> None:
    """同 notify_user：用 savepoint，避免 IntegrityError 污染外层事务。"""
    uid = int(user_id)
    if uid <= 0:
        return
    cat = (category or "").strip()
    if cat not in _VALID_CATEGORIES:
        logger.warning("notify_user invalid category=%s user=%s", cat, uid)
        return
    title_s = (title or "").strip()[:128] or "通知"
    body_s = (body or "").strip()[:2000] or ""
    dedupe = (dedupe_key or "").strip()[:160] or None
    try:
        async with db.begin_nested():
            row = UserNotification(
                user_id=uid,
                category=cat,
                title=title_s,
                body=body_s,
                link_url=(link_url or "").strip()[:512] or None,
                ref_type=(ref_type or "").strip()[:32] or None,
                ref_id=(ref_id or "").strip()[:64] or None,
                dedupe_key=dedupe,
                is_read=False,
                created_at=now_cst_naive(),
            )
            db.add(row)
            await db.flush()
    except IntegrityError:
        logger.info("notify_user dedupe hit user=%s key=%s", uid, dedupe)
    except Exception as exc:  # noqa: BLE001
        logger.warning("notify_user failed user=%s cat=%s err=%s", uid, cat, exc)


async def list_user_notifications(
    db: AsyncSession,
    user_id: int,
    *,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
) -> dict[str, Any]:
    """分页列出当前用户通知（新→旧）。"""
    uid = int(user_id)
    page = max(1, int(page))
    page_size = min(50, max(1, int(page_size)))

    base = select(UserNotification).where(UserNotification.user_id == uid)
    count_q = (
        select(func.count())
        .select_from(UserNotification)
        .where(UserNotification.user_id == uid)
    )
    if unread_only:
        base = base.where(UserNotification.is_read.is_(False))
        count_q = count_q.where(UserNotification.is_read.is_(False))

    total = int((await db.execute(count_q)).scalar_one() or 0)
    unread_count = int(
        (
            await db.execute(
                select(func.count())
                .select_from(UserNotification)
                .where(
                    UserNotification.user_id == uid,
                    UserNotification.is_read.is_(False),
                )
            )
        ).scalar_one()
        or 0
    )

    rows = list(
        (
            await db.execute(
                base.order_by(UserNotification.created_at.desc(), UserNotification.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [notification_to_dict(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
        "unreadCount": unread_count,
    }


async def get_unread_count(db: AsyncSession, user_id: int) -> int:
    """未读条数（顶栏角标）。"""
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(UserNotification)
                .where(
                    UserNotification.user_id == int(user_id),
                    UserNotification.is_read.is_(False),
                )
            )
        ).scalar_one()
        or 0
    )


async def mark_notification_read(
    db: AsyncSession,
    user_id: int,
    notification_id: str,
) -> dict[str, Any]:
    """单条标已读。"""
    try:
        nid = int(str(notification_id).strip())
    except (TypeError, ValueError):
        fail(ErrorCode.NOT_FOUND, message="通知不存在")
    row = (
        await db.execute(
            select(UserNotification).where(
                UserNotification.id == nid,
                UserNotification.user_id == int(user_id),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        fail(ErrorCode.NOT_FOUND, message="通知不存在")
    if not row.is_read:
        row.is_read = True
        await db.flush()
    return notification_to_dict(row)


async def mark_all_notifications_read(db: AsyncSession, user_id: int) -> dict[str, Any]:
    """全部标已读。"""
    uid = int(user_id)
    result = await db.execute(
        update(UserNotification)
        .where(
            UserNotification.user_id == uid,
            UserNotification.is_read.is_(False),
        )
        .values(is_read=True)
    )
    await db.flush()
    return {"updated": int(result.rowcount or 0), "unreadCount": 0}
