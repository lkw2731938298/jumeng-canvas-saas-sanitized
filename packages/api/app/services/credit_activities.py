"""算力活动 —— 列表、领取（Redis 强力锁 + DB UNIQUE）、管理端增改。

领取防重（两层都必须做）：
1. Redis：lock:credit:claim:{activity_id}:{user_id}，is_/claim_/seal_，TTL 60s
2. DB：UNIQUE(activity_id, user_id)，每人每活动仅一条 claim；per_user_limit 固定为 1
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_types import ALL_CREDIT_TYPES, CREDIT_TYPE_MODEL_SPECIFIC
from ..core.datetime_util import as_cst_aware, now_cst_aware, now_cst_naive
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .storage_urls import normalize_browser_storage_url, persistable_media_url
from ..models.credit_activity import CreditActivity, CreditActivityClaim
from ..models.user import User
from .credit_activity_eligibility import (
    assert_user_eligible,
    describe_claim_rules,
    evaluate_eligibility,
    load_user_claim_profile,
    normalize_claim_rules,
    serialize_claim_rules,
)
from .credit_claim_lock import (
    claim_credit_activity,
    is_credit_activity_claimed,
    seal_credit_activity_claim,
)
from .credit_transactions import grant_credits_with_log

ACTIVITY_STATUS_DRAFT = "draft"
ACTIVITY_STATUS_ACTIVE = "active"
ACTIVITY_STATUS_ENDED = "ended"

# 每人每活动仅允许领取 1 次（与 UNIQUE(activity_id, user_id) 一致）
PER_USER_CLAIM_LIMIT = 1


def _utcnow() -> datetime:
    """业务比较用东八区 aware 时刻（勿与 MySQL naive 直接比）。"""
    return now_cst_aware()


def _activity_is_claimable(activity: CreditActivity, now: datetime) -> bool:
    """判断活动当前是否可领取：状态 active、在起止时间内、总配额未用尽。"""
    if activity.status != ACTIVITY_STATUS_ACTIVE:
        return False
    starts_at = as_cst_aware(activity.starts_at)
    ends_at = as_cst_aware(activity.ends_at)
    now_aware = as_cst_aware(now)
    if starts_at is None or ends_at is None or now_aware is None:
        return False
    # 起止与 now 一律按东八区 aware 比较，避免 naive/aware TypeError → 500
    if starts_at > now_aware or ends_at < now_aware:
        return False
    if activity.total_quota is not None and int(activity.claimed_count or 0) >= int(activity.total_quota):
        return False
    return True


async def _user_has_claimed(db: AsyncSession, *, activity_id: int, user_id: int) -> bool:
    """该用户是否已领取过该活动（有任意一条 claim 即视为已领）。"""
    result = await db.execute(
        select(func.count(CreditActivityClaim.id)).filter(
            CreditActivityClaim.activity_id == activity_id,
            CreditActivityClaim.user_id == user_id,
        )
    )
    return int(result.scalar_one() or 0) > 0


async def _enrich_activity_model_display(
    db: AsyncSession, items: list[dict[str, Any]]
) -> None:
    """用户侧活动列表：补充 modelDisplayName（无目录展示名则不填）。"""
    from .credit_transaction_query import map_model_display_names

    names = {
        str(item.get("modelName") or "").strip()
        for item in items
        if item.get("modelName")
    }
    if not names:
        return
    display_map = await map_model_display_names(db, names, fallback_to_internal=False)
    for item in items:
        raw = str(item.get("modelName") or "").strip()
        item["modelDisplayName"] = display_map.get(raw) or None


def _activity_out(
    activity: CreditActivity,
    *,
    user_claim_count: int = 0,
    claimable: bool = False,
    eligible: bool = True,
    eligibility_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """将活动实体序列化为前端 DTO（含可领状态、剩余配额、领取资格）。"""
    rules = serialize_claim_rules(activity)
    reasons = list(eligibility_reasons or [])
    can_claim = claimable and eligible and user_claim_count < PER_USER_CLAIM_LIMIT
    return {
        "id": str(activity.id),
        "title": activity.title,
        "description": activity.description or "",
        "coverUrl": normalize_browser_storage_url(activity.cover_url or "") if activity.cover_url else "",
        "creditType": activity.credit_type,
        "amount": int(activity.amount or 0),
        "modelName": activity.model_name,
        "modelDisplayName": None,
        "validDays": int(activity.valid_days or 30),
        "startsAt": activity.starts_at.isoformat() if activity.starts_at else None,
        "endsAt": activity.ends_at.isoformat() if activity.ends_at else None,
        "perUserLimit": PER_USER_CLAIM_LIMIT,
        "totalQuota": activity.total_quota,
        "claimedCount": int(activity.claimed_count or 0),
        "status": activity.status,
        "claimRules": rules,
        "claimRuleSummary": describe_claim_rules(rules),
        "userClaimCount": user_claim_count,
        "eligible": eligible,
        "eligibilityReasons": reasons,
        "canClaim": can_claim,
        "remainingQuota": (
            max(int(activity.total_quota) - int(activity.claimed_count or 0), 0)
            if activity.total_quota is not None
            else None
        ),
    }


async def list_activities_for_user(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """列出对该用户可见的进行中活动（含其领取次数、资格与可领状态）。"""
    now = _utcnow()
    now_db = now_cst_naive()
    profile = await load_user_claim_profile(db, user)
    result = await db.execute(
        select(CreditActivity)
        .filter(
            CreditActivity.status == ACTIVITY_STATUS_ACTIVE,
            CreditActivity.ends_at >= now_db,
        )
        .order_by(CreditActivity.starts_at.desc())
    )
    activities = result.scalars().all()
    out: list[dict[str, Any]] = []
    for activity in activities:
        claimed = await _user_has_claimed(db, activity_id=activity.id, user_id=user.id)
        claim_count = 1 if claimed else 0
        elig = evaluate_eligibility(serialize_claim_rules(activity), profile)
        out.append(
            _activity_out(
                activity,
                user_claim_count=claim_count,
                claimable=_activity_is_claimable(activity, now),
                eligible=elig.eligible,
                eligibility_reasons=elig.reasons,
            )
        )
    await _enrich_activity_model_display(db, out)
    return out


async def list_showcase_activities(db: AsyncSession) -> list[dict[str, Any]]:
    """公开活动页内容：返回进行中与已结束活动，不返回草稿。"""
    result = await db.execute(
        select(CreditActivity)
        .filter(CreditActivity.status.in_((ACTIVITY_STATUS_ACTIVE, ACTIVITY_STATUS_ENDED)))
        .order_by(CreditActivity.starts_at.desc())
    )
    items = [_activity_out(activity) for activity in result.scalars().all()]
    await _enrich_activity_model_display(db, items)
    return items


async def claim_activity(
    db: AsyncSession,
    *,
    user: User,
    activity_id: int,
) -> dict[str, Any]:
    """领取活动算力：资格预检 → Redis is_/claim_/seal_ → DB 写 claim + 发 lot。

    失败路径不删除 Redis 标记（与媒体一致，防误重试）；DB UNIQUE 永久兜底。
    资格不满足时在占 Redis 锁之前 fail，避免不合格用户被锁误伤。
    """
    # 【锁·activity+user】60 秒内已有标记 → 视为已领取过
    if await is_credit_activity_claimed(activity_id, user.id):
        fail(ErrorCode.ACTIVITY_CLAIM_LIMIT)

    # 占锁前预检：活动可领 + 用户资格（不合格不占 Redis）
    now = now_cst_aware()
    pre = await db.execute(select(CreditActivity).filter(CreditActivity.id == activity_id))
    activity_pre = pre.scalar_one_or_none()
    if not activity_pre:
        fail(ErrorCode.ACTIVITY_NOT_FOUND)
    if not _activity_is_claimable(activity_pre, now):
        fail(ErrorCode.ACTIVITY_NOT_CLAIMABLE)
    profile = await load_user_claim_profile(db, user)
    assert_user_eligible(activity_pre, profile)

    # 【锁·activity+user】SET NX 占位，防并发连点
    if not await claim_credit_activity(activity_id, user.id):
        fail(ErrorCode.CLAIM_IN_PROGRESS)

    # 占位成功后进入 DB；成功则 seal；失败不 DELETE Redis
    return await _claim_activity_db(db, user=user, activity_id=activity_id, profile=profile)


async def _claim_activity_db(
    db: AsyncSession,
    *,
    user: User,
    activity_id: int,
    profile=None,
) -> dict[str, Any]:
    """领取核心逻辑：行锁活动→资格再验→已有 claim 则 409→先写 claim→再发放 lot→seal Redis。"""
    now = now_cst_aware()
    now_db = now_cst_naive()
    result = await db.execute(
        select(CreditActivity).filter(CreditActivity.id == activity_id).with_for_update()
    )
    activity = result.scalar_one_or_none()
    if not activity:
        fail(ErrorCode.ACTIVITY_NOT_FOUND)

    if not _activity_is_claimable(activity, now):
        fail(ErrorCode.ACTIVITY_NOT_CLAIMABLE)

    if activity.credit_type == CREDIT_TYPE_MODEL_SPECIFIC and not activity.model_name:
        fail(ErrorCode.ACTIVITY_CONFIG_ERROR)

    # 行锁后再验资格（画像可复用预检结果）
    if profile is None:
        profile = await load_user_claim_profile(db, user)
    assert_user_eligible(activity, profile)

    # 已有领取记录 → 409「已领取过」（与 UNIQUE 兜底一致）
    claims_result = await db.execute(
        select(CreditActivityClaim)
        .filter(
            CreditActivityClaim.activity_id == activity.id,
            CreditActivityClaim.user_id == user.id,
        )
        .with_for_update()
    )
    if claims_result.scalars().first() is not None:
        fail(ErrorCode.ACTIVITY_CLAIM_LIMIT)

    claim = CreditActivityClaim(
        activity_id=activity.id,
        user_id=user.id,
        claim_seq=PER_USER_CLAIM_LIMIT,
        lot_id=None,
        amount=int(activity.amount),
        claimed_at=now_db,
    )
    activity.claimed_count = int(activity.claimed_count or 0) + 1
    activity.per_user_limit = PER_USER_CLAIM_LIMIT
    activity.updated_at = now_db
    db.add(claim)
    try:
        await db.flush()
    except IntegrityError:
        # UNIQUE(activity_id, user_id) 冲突 → 已领取过
        fail(ErrorCode.ACTIVITY_CLAIM_LIMIT)

    balance = await grant_credits_with_log(
        db,
        user=user,
        amount=int(activity.amount),
        credit_type=activity.credit_type,
        source="activity_claim",
        reason=f"领取活动：{activity.title}",
        model_name=activity.model_name,
        valid_days=int(activity.valid_days or 30),
        source_ref=f"{activity.id}:{claim.id}",
    )

    from ..models.credit_lot import CreditLot

    lot_row = await db.execute(
        select(CreditLot)
        .filter(
            CreditLot.user_id == user.id,
            CreditLot.source == "activity_claim",
            CreditLot.source_ref == f"{activity.id}:{claim.id}",
        )
        .limit(1)
    )
    lot = lot_row.scalar_one_or_none()
    if lot is None:
        fail(ErrorCode.CLAIM_GRANT_FAILED)

    claim.lot_id = lot.id
    expires_at = as_cst_aware(lot.expires_at) or (now + timedelta(days=int(activity.valid_days or 30)))
    await db.flush()

    # DB 写 claim + 发放 lot 成功后续期 Redis 标记（不 DELETE）
    await seal_credit_activity_claim(activity_id, user.id)

    return {
        "ok": True,
        "activityId": str(activity.id),
        "amount": int(activity.amount),
        "creditType": activity.credit_type,
        "balance": balance,
        "expiresAt": expires_at.isoformat(),
    }


async def list_all_activities(db: AsyncSession) -> list[dict[str, Any]]:
    """管理端：列出全部活动（含草稿/已结束）。"""
    result = await db.execute(
        select(CreditActivity).order_by(CreditActivity.created_at.desc())
    )
    return [_activity_out(activity) for activity in result.scalars().all()]


async def get_activity(db: AsyncSession, activity_id: int) -> CreditActivity | None:
    """按 ID 查询单个活动。"""
    result = await db.execute(select(CreditActivity).filter(CreditActivity.id == activity_id))
    return result.scalar_one_or_none()


async def create_activity(
    db: AsyncSession,
    *,
    title: str,
    description: str,
    cover_url: str | None,
    credit_type: str,
    amount: int,
    model_name: str | None,
    valid_days: int,
    starts_at: datetime,
    ends_at: datetime,
    per_user_limit: int = PER_USER_CLAIM_LIMIT,
    total_quota: int | None,
    status: str,
    claim_rules: Any = None,
) -> CreditActivity:
    """管理端创建活动，校验算力类型/模型/数量/起止时间/领取条件后落库。"""
    if credit_type not in ALL_CREDIT_TYPES:
        fail(ErrorCode.INVALID_CREDIT_TYPE, content={"creditType": credit_type})
    if credit_type == CREDIT_TYPE_MODEL_SPECIFIC and not model_name:
        fail(ErrorCode.ACTIVITY_CONFIG_ERROR)
    if amount <= 0:
        fail(ErrorCode.BAD_REQUEST, message="活动赠送数量须大于 0")
    if ends_at <= starts_at:
        fail(ErrorCode.ACTIVITY_INVALID_SCHEDULE)

    rules = normalize_claim_rules(claim_rules)

    now = now_cst_naive()
    activity = CreditActivity(
        title=title.strip(),
        description=description.strip() or None,
        cover_url=persistable_media_url(cover_url),
        credit_type=credit_type,
        amount=amount,
        model_name=model_name,
        valid_days=valid_days,
        starts_at=starts_at,
        ends_at=ends_at,
        # 每人每活动固定只可领 1 次（忽略传入的更高限额）
        per_user_limit=PER_USER_CLAIM_LIMIT,
        total_quota=total_quota,
        claim_rules=rules,
        status=status if status in (ACTIVITY_STATUS_DRAFT, ACTIVITY_STATUS_ACTIVE, ACTIVITY_STATUS_ENDED) else ACTIVITY_STATUS_DRAFT,
        created_at=now,
        updated_at=now,
    )
    db.add(activity)
    await db.flush()
    return activity


async def update_activity(
    db: AsyncSession,
    activity: CreditActivity,
    *,
    title: str | None = None,
    description: str | None = None,
    cover_url: str | None = None,
    credit_type: str | None = None,
    amount: int | None = None,
    model_name: str | None = None,
    valid_days: int | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    per_user_limit: int | None = None,
    total_quota: int | None = ...,  # type: ignore[assignment]
    status: str | None = None,
    claim_rules: Any = ...,
) -> CreditActivity:
    """管理端按需更新活动字段（仅更新显式传入的项）。"""
    if title is not None:
        activity.title = title.strip()
    if description is not None:
        activity.description = description.strip() or None
    if cover_url is not None:
        # 中文：落库只存 CDN/可解析公共 URL，禁止签权串
        activity.cover_url = persistable_media_url(cover_url)
    if credit_type is not None:
        if credit_type not in ALL_CREDIT_TYPES:
            fail(ErrorCode.INVALID_CREDIT_TYPE, content={"creditType": credit_type})
        activity.credit_type = credit_type
    if amount is not None:
        activity.amount = int(amount)
    if model_name is not None:
        activity.model_name = model_name or None
    if valid_days is not None:
        activity.valid_days = int(valid_days)
    if starts_at is not None:
        activity.starts_at = starts_at
    if ends_at is not None:
        activity.ends_at = ends_at
    # per_user_limit 服务端固定为 1，忽略管理端改更高值的请求
    if per_user_limit is not None:
        activity.per_user_limit = PER_USER_CLAIM_LIMIT
    if total_quota is not ...:
        activity.total_quota = total_quota
    if claim_rules is not ...:
        activity.claim_rules = normalize_claim_rules(claim_rules)
    if status is not None:
        if status in (ACTIVITY_STATUS_DRAFT, ACTIVITY_STATUS_ACTIVE, ACTIVITY_STATUS_ENDED):
            activity.status = status
    activity.updated_at = now_cst_naive()
    await db.flush()
    return activity


async def delete_activity(db: AsyncSession, activity: CreditActivity) -> None:
    """管理端删除活动：已有任何用户领取记录则禁止删除（应改为结束），以保留领取历史完整性。"""
    result = await db.execute(
        select(func.count(CreditActivityClaim.id)).filter(
            CreditActivityClaim.activity_id == activity.id
        )
    )
    if int(result.scalar_one() or 0) > 0:
        fail(ErrorCode.ACTIVITY_IN_USE)
    await db.delete(activity)
    await db.flush()
