"""Admin APIs for credit activities."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from ....core.entity_ids import require_entity_id
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_CREDIT_ACTIVITIES
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....services.cache import check_rate_limit
from ....services.credit_activities import (
    ACTIVITY_STATUS_ACTIVE,
    ACTIVITY_STATUS_DRAFT,
    ACTIVITY_STATUS_ENDED,
    _activity_out,
    create_activity,
    delete_activity,
    get_activity,
    list_all_activities,
    update_activity,
)
from ....services.discover_media import upload_credit_activity_image

router = APIRouter()


class AdminCreditActivityOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    description: str = ""
    cover_url: str = Field("", alias="coverUrl")
    credit_type: str = Field(..., alias="creditType")
    amount: int
    model_name: str | None = Field(None, alias="modelName")
    valid_days: int = Field(..., alias="validDays")
    starts_at: str | None = Field(None, alias="startsAt")
    ends_at: str | None = Field(None, alias="endsAt")
    per_user_limit: int = Field(..., alias="perUserLimit")
    total_quota: int | None = Field(None, alias="totalQuota")
    claimed_count: int = Field(..., alias="claimedCount")
    status: str
    user_claim_count: int = Field(0, alias="userClaimCount")
    can_claim: bool = Field(False, alias="canClaim")
    remaining_quota: int | None = Field(None, alias="remainingQuota")
    claim_rules: dict | None = Field(None, alias="claimRules")
    claim_rule_summary: list[str] = Field(default_factory=list, alias="claimRuleSummary")
    eligible: bool = True
    eligibility_reasons: list[str] = Field(default_factory=list, alias="eligibilityReasons")


class AdminClaimRulesIn(BaseModel):
    """领取资格条件（全部可选；条件之间 AND）。"""

    model_config = ConfigDict(populate_by_name=True)

    registered_from: Optional[datetime] = Field(None, alias="registeredFrom")
    registered_to: Optional[datetime] = Field(None, alias="registeredTo")
    # 累计实付金额（分）；仅统计已完成的 recharge 订单
    min_recharge_fen: Optional[int] = Field(None, alias="minRechargeFen", ge=1)
    # 累计入账算力点
    min_recharge_credits: Optional[int] = Field(None, alias="minRechargeCredits", ge=1)
    require_active_member: Optional[bool] = Field(None, alias="requireActiveMember")


class AdminCreditActivityListOut(BaseModel):
    items: list[AdminCreditActivityOut]


class AdminCreditActivityCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    description: str = ""
    cover_url: str = Field("", alias="coverUrl", max_length=1024)
    credit_type: str = Field("activity", alias="creditType")
    amount: int = Field(..., ge=1)
    model_name: Optional[str] = Field(None, alias="modelName")
    valid_days: int = Field(30, alias="validDays", ge=1, le=3650)
    starts_at: datetime = Field(..., alias="startsAt")
    ends_at: datetime = Field(..., alias="endsAt")
    # 每人每活动固定只可领 1 次（服务端强制；字段保留兼容管理端表单）
    per_user_limit: int = Field(1, alias="perUserLimit", ge=1, le=1)
    total_quota: Optional[int] = Field(None, alias="totalQuota", ge=1)
    status: str = ACTIVITY_STATUS_DRAFT
    claim_rules: Optional[AdminClaimRulesIn] = Field(None, alias="claimRules")


class AdminCreditActivityUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = Field(None, alias="coverUrl", max_length=1024)
    credit_type: Optional[str] = Field(None, alias="creditType")
    amount: Optional[int] = Field(None, ge=1)
    model_name: Optional[str] = Field(None, alias="modelName")
    valid_days: Optional[int] = Field(None, alias="validDays", ge=1, le=3650)
    starts_at: Optional[datetime] = Field(None, alias="startsAt")
    ends_at: Optional[datetime] = Field(None, alias="endsAt")
    per_user_limit: Optional[int] = Field(None, alias="perUserLimit", ge=1, le=1)
    total_quota: Optional[int] = Field(None, alias="totalQuota")
    status: Optional[str] = None
    # 显式传 null/空对象可清空领取条件
    claim_rules: Optional[AdminClaimRulesIn] = Field(None, alias="claimRules")


class CreditActivityImageUploadOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    oss_key: str = Field(..., alias="ossKey")
    image_url: str = Field(..., alias="imageUrl")


@router.post("/credit-activities/image", response_model=CreditActivityImageUploadOut)
async def upload_admin_credit_activity_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(PERM_CREDIT_ACTIVITIES)),
):
    """上传算力活动封面到平台 OSS。"""
    allowed = await check_rate_limit(
        f"admin:credit-activity-image:{current_user.id}",
        limit=30,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)
    raw = await file.read()
    try:
        uploaded = upload_credit_activity_image(
            file.filename or "activity.jpg",
            raw,
            file.content_type,
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    return CreditActivityImageUploadOut(**uploaded)


@router.get("/credit-activities", response_model=AdminCreditActivityListOut)
async def list_admin_credit_activities(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDIT_ACTIVITIES)),
):
    items = await list_all_activities(db)
    return AdminCreditActivityListOut(items=[AdminCreditActivityOut(**item) for item in items])


def _claim_rules_payload(rules: AdminClaimRulesIn | None) -> dict | None:
    """管理端领取条件 → 服务层 dict（含 ISO 时间）；None 表示无限制。"""
    if rules is None:
        return None
    data = rules.model_dump(by_alias=True, exclude_none=True)
    # datetime → ISO，供 normalize_claim_rules 解析
    for key in ("registeredFrom", "registeredTo"):
        val = data.get(key)
        if isinstance(val, datetime):
            data[key] = val.isoformat()
    return data or None


@router.post("/credit-activities", response_model=AdminCreditActivityOut)
async def create_admin_credit_activity(
    body: AdminCreditActivityCreateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDIT_ACTIVITIES)),
):
    activity = await create_activity(
        db,
        title=body.title,
        description=body.description,
        cover_url=body.cover_url,
        credit_type=body.credit_type,
        amount=body.amount,
        model_name=body.model_name,
        valid_days=body.valid_days,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        per_user_limit=body.per_user_limit,
        total_quota=body.total_quota,
        status=body.status,
        claim_rules=_claim_rules_payload(body.claim_rules),
    )
    await db.flush()
    return AdminCreditActivityOut(**_activity_out(activity))


@router.patch("/credit-activities/{activity_id}", response_model=AdminCreditActivityOut)
async def patch_admin_credit_activity(
    activity_id: str,
    body: AdminCreditActivityUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDIT_ACTIVITIES)),
):
    activity_id_int = require_entity_id(activity_id, message="活动 ID 无效")

    activity = await get_activity(db, activity_id_int)
    if not activity:
        fail(ErrorCode.ACTIVITY_NOT_FOUND)

    payload = body.model_dump(exclude_unset=True, by_alias=False)
    claim_rules_arg = ...
    if "claim_rules" in payload:
        # 显式传 claimRules（含 null）时更新；null/空 → 清空条件
        raw_rules = body.claim_rules
        claim_rules_arg = _claim_rules_payload(raw_rules)
    activity = await update_activity(
        db,
        activity,
        title=payload.get("title"),
        description=payload.get("description"),
        cover_url=payload.get("cover_url"),
        credit_type=payload.get("credit_type"),
        amount=payload.get("amount"),
        model_name=payload.get("model_name"),
        valid_days=payload.get("valid_days"),
        starts_at=payload.get("starts_at"),
        ends_at=payload.get("ends_at"),
        per_user_limit=payload.get("per_user_limit"),
        total_quota=payload.get("total_quota") if "total_quota" in payload else ...,
        status=payload.get("status"),
        claim_rules=claim_rules_arg,
    )
    return AdminCreditActivityOut(**_activity_out(activity))


@router.delete("/credit-activities/{activity_id}")
async def delete_admin_credit_activity(
    activity_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_CREDIT_ACTIVITIES)),
):
    """删除算力活动（已有用户领取记录时禁止删除，返回 409 提示改为结束）。"""
    activity_id_int = require_entity_id(activity_id, message="活动 ID 无效")
    activity = await get_activity(db, activity_id_int)
    if not activity:
        fail(ErrorCode.ACTIVITY_NOT_FOUND)
    await delete_activity(db, activity)
    return {"ok": True}
