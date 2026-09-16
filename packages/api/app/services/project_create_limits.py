"""新建项目频率限制：5 秒冷却 + 每日上限 30。

冷却依赖 Redis SET NX（不可用则 fail-closed）；
日上限以 MySQL projects.created_at（东八区）为准，含已软删记录以防删建绕过。
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.project import Project
from .redis_client import get_redis

logger = logging.getLogger(__name__)

# 同一用户两次新建至少间隔秒数
PROJECT_CREATE_COOLDOWN_SEC = 5
# 东八区自然日创建项目数上限（含软删）
PROJECT_CREATE_DAILY_LIMIT = 30


def _project_create_cooldown_key(user_id: int | str) -> str:
    return f"lock:project:create:{user_id}"


def start_of_today_cst_naive():
    """当前东八区日历日 00:00:00（naive，与 DB created_at 同语义）。"""
    now = now_cst_naive()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def count_projects_created_today(db: AsyncSession, owner_id: int) -> int:
    """统计该用户今日已创建的项目数（含 isdel=True）。"""
    day_start = start_of_today_cst_naive()
    result = await db.execute(
        select(func.count(Project.id)).where(
            Project.owner_id == owner_id,
            Project.created_at >= day_start,
        )
    )
    return int(result.scalar_one() or 0)


async def claim_project_create_cooldown(user_id: int) -> None:
    """占住 5 秒冷却锁；拿不到则 429；Redis 不可用则 503。"""
    redis = await get_redis()
    if not redis:
        fail(ErrorCode.REDIS_UNAVAILABLE, message="限流服务暂不可用，请稍后重试")
    key = _project_create_cooldown_key(user_id)
    try:
        ok = await redis.set(key, "1", nx=True, ex=PROJECT_CREATE_COOLDOWN_SEC)
    except Exception:
        logger.exception("project create cooldown redis failed")
        fail(ErrorCode.REDIS_UNAVAILABLE, message="限流服务暂不可用，请稍后重试")
    if not ok:
        ttl = 0
        try:
            ttl = int(await redis.ttl(key) or 0)
        except Exception:
            ttl = PROJECT_CREATE_COOLDOWN_SEC
        fail(
            ErrorCode.PROJECT_CREATE_COOLDOWN,
            message=f"新建过快，请 {max(ttl, 1)} 秒后再试",
            content={"retryAfterSeconds": max(ttl, 1)},
        )


async def assert_project_create_allowed(db: AsyncSession, owner_id: int) -> None:
    """创建前校验：日上限 → 5 秒冷却占位。"""
    created_today = await count_projects_created_today(db, owner_id)
    if created_today >= PROJECT_CREATE_DAILY_LIMIT:
        fail(
            ErrorCode.PROJECT_CREATE_DAILY_LIMIT,
            message=f"今日新建已达上限（{PROJECT_CREATE_DAILY_LIMIT} 个），请明天再试",
            content={
                "limit": PROJECT_CREATE_DAILY_LIMIT,
                "createdToday": created_today,
            },
        )
    await claim_project_create_cooldown(owner_id)
