"""运营仪表盘统计：总量 + 近期新增 + 近 30 日趋势 + 算力（东八区）。"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_DASHBOARD
from ....core.credit_amount import normalize_credit_amount
from ....core.datetime_util import now_cst_naive
from ....core.deps import require_permission
from ....models.credit_lot import CreditLot
from ....models.credit_transaction import CreditTransaction
from ....models.database import get_db
from ....models.job import GenerationJob
from ....models.project import Project, Workflow
from ....models.user import User
from ....schemas.admin import AdminStatsDailyPoint, AdminStatsOut

router = APIRouter()

# 趋势窗口：含今日共 30 个东八区日历日
_TREND_DAYS = 30


def _day_start_cst(*, days_ago: int = 0) -> datetime:
    """东八区当日 00:00 往前推 days_ago 天（naive，与 DATETIME 列比较）。"""
    base = now_cst_naive().replace(hour=0, minute=0, second=0, microsecond=0)
    return base - timedelta(days=days_ago)


def _date_key(value: Any) -> str:
    """将 MySQL DATE / datetime / str 统一为 YYYY-MM-DD。"""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return str(value)[:10]


def _fill_daily_points(by_day: dict[str, float], *, days: int = _TREND_DAYS) -> list[AdminStatsDailyPoint]:
    points: list[AdminStatsDailyPoint] = []
    for offset in range(days - 1, -1, -1):
        day = _day_start_cst(days_ago=offset)
        key = day.strftime("%Y-%m-%d")
        points.append(AdminStatsDailyPoint(date=key, count=by_day.get(key, 0)))
    return points


async def _count_since(db: AsyncSession, model: type, since: datetime) -> int:
    result = await db.execute(select(func.count()).select_from(model).where(model.created_at >= since))
    return int(result.scalar_one() or 0)


async def _daily_trend(
    db: AsyncSession,
    model: type,
    *,
    days: int = _TREND_DAYS,
) -> list[AdminStatsDailyPoint]:
    """按日 COUNT，并补齐窗口内缺失日期为 0。"""
    since = _day_start_cst(days_ago=days - 1)
    rows = (
        await db.execute(
            select(func.date(model.created_at), func.count())
            .select_from(model)
            .where(model.created_at >= since)
            .group_by(func.date(model.created_at))
            .order_by(func.date(model.created_at))
        )
    ).all()
    by_day = {_date_key(row[0]): float(int(row[1] or 0)) for row in rows if row[0] is not None}
    return _fill_daily_points(by_day, days=days)


async def _sum_tx_since(db: AsyncSession, *, since: datetime, negative: bool) -> float:
    """流水绝对值汇总：negative=True 为消耗（delta<0），False 为发放（delta>0）。"""
    cond = CreditTransaction.delta < 0 if negative else CreditTransaction.delta > 0
    result = await db.execute(
        select(func.coalesce(func.sum(func.abs(CreditTransaction.delta)), 0)).where(
            CreditTransaction.created_at >= since,
            cond,
        )
    )
    return normalize_credit_amount(result.scalar_one() or 0)


async def _daily_tx_trend(
    db: AsyncSession,
    *,
    negative: bool,
    days: int = _TREND_DAYS,
) -> list[AdminStatsDailyPoint]:
    """按日汇总消耗或发放算力（绝对值），缺日补 0。"""
    since = _day_start_cst(days_ago=days - 1)
    cond = CreditTransaction.delta < 0 if negative else CreditTransaction.delta > 0
    rows = (
        await db.execute(
            select(
                func.date(CreditTransaction.created_at),
                func.coalesce(func.sum(func.abs(CreditTransaction.delta)), 0),
            )
            .where(CreditTransaction.created_at >= since, cond)
            .group_by(func.date(CreditTransaction.created_at))
            .order_by(func.date(CreditTransaction.created_at))
        )
    ).all()
    by_day = {
        _date_key(row[0]): normalize_credit_amount(row[1] or 0)
        for row in rows
        if row[0] is not None
    }
    return _fill_daily_points(by_day, days=days)


@router.get("/stats", response_model=AdminStatsOut)
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_DASHBOARD)),
):
    today = _day_start_cst(days_ago=0)
    d7 = _day_start_cst(days_ago=6)
    d30 = _day_start_cst(days_ago=29)

    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    project_count = (await db.execute(select(func.count(Project.id)))).scalar_one()
    workflow_count = (await db.execute(select(func.count(Workflow.id)))).scalar_one()

    status_rows = (
        await db.execute(
            select(GenerationJob.status, func.count(GenerationJob.id)).group_by(GenerationJob.status)
        )
    ).all()
    jobs = {row[0]: int(row[1]) for row in status_rows}

    lane_rows = (
        await db.execute(
            select(GenerationJob.lane, func.count(GenerationJob.id)).group_by(GenerationJob.lane)
        )
    ).all()
    jobs_by_lane = {str(row[0] or "unknown"): int(row[1]) for row in lane_rows}

    users_today = await _count_since(db, User, today)
    users_7d = await _count_since(db, User, d7)
    users_30d = await _count_since(db, User, d30)
    projects_today = await _count_since(db, Project, today)
    projects_7d = await _count_since(db, Project, d7)
    projects_30d = await _count_since(db, Project, d30)
    jobs_today = await _count_since(db, GenerationJob, today)
    jobs_7d = await _count_since(db, GenerationJob, d7)
    jobs_30d = await _count_since(db, GenerationJob, d30)

    jobs_succeeded_7d = int(
        (
            await db.execute(
                select(func.count(GenerationJob.id)).where(
                    GenerationJob.created_at >= d7,
                    GenerationJob.status == "succeeded",
                )
            )
        ).scalar_one()
        or 0
    )
    jobs_failed_7d = int(
        (
            await db.execute(
                select(func.count(GenerationJob.id)).where(
                    GenerationJob.created_at >= d7,
                    GenerationJob.status == "failed",
                )
            )
        ).scalar_one()
        or 0
    )

    user_trend = await _daily_trend(db, User)
    project_trend = await _daily_trend(db, Project)
    job_trend = await _daily_trend(db, GenerationJob)

    # —— 算力：活跃 lot 库存（按类型）——
    lot_rows = (
        await db.execute(
            select(
                CreditLot.credit_type,
                func.coalesce(func.sum(CreditLot.amount_remaining), 0),
            )
            .where(
                CreditLot.status == "active",
                CreditLot.amount_remaining > 0,
                # 未过期或永久（expires_at IS NULL）
                (CreditLot.expires_at.is_(None)) | (CreditLot.expires_at > now_cst_naive()),
            )
            .group_by(CreditLot.credit_type)
        )
    ).all()
    credits_by_type = {
        str(row[0] or "unknown"): normalize_credit_amount(row[1] or 0) for row in lot_rows
    }
    credits_remaining_total = normalize_credit_amount(sum(credits_by_type.values()))

    credits_consumed_today = await _sum_tx_since(db, since=today, negative=True)
    credits_consumed_week = await _sum_tx_since(db, since=d7, negative=True)
    credits_consumed_month = await _sum_tx_since(db, since=d30, negative=True)
    credits_granted_today = await _sum_tx_since(db, since=today, negative=False)
    credits_granted_week = await _sum_tx_since(db, since=d7, negative=False)
    credits_granted_month = await _sum_tx_since(db, since=d30, negative=False)
    credit_consume_trend = await _daily_tx_trend(db, negative=True)
    credit_grant_trend = await _daily_tx_trend(db, negative=False)

    return AdminStatsOut(
        user_count=int(user_count or 0),
        project_count=int(project_count or 0),
        workflow_count=int(workflow_count or 0),
        jobs=jobs,
        users_today=users_today,
        users_week=users_7d,
        users_month=users_30d,
        projects_today=projects_today,
        projects_week=projects_7d,
        projects_month=projects_30d,
        jobs_today=jobs_today,
        jobs_week=jobs_7d,
        jobs_month=jobs_30d,
        user_trend=user_trend,
        project_trend=project_trend,
        job_trend=job_trend,
        jobs_by_lane=jobs_by_lane,
        jobs_succeeded_week=jobs_succeeded_7d,
        jobs_failed_week=jobs_failed_7d,
        credits_remaining_total=credits_remaining_total,
        credits_by_type=credits_by_type,
        credits_consumed_today=credits_consumed_today,
        credits_consumed_week=credits_consumed_week,
        credits_consumed_month=credits_consumed_month,
        credits_granted_today=credits_granted_today,
        credits_granted_week=credits_granted_week,
        credits_granted_month=credits_granted_month,
        credit_consume_trend=credit_consume_trend,
        credit_grant_trend=credit_grant_trend,
    )
