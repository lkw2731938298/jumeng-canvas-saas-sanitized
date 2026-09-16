"""用户侧算力活动 API 路由。

提供可领取活动列表与单次领取接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from ...core.entity_ids import require_entity_id
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...models.database import get_db
from ...models.user import User
from ...services.credit_activities import claim_activity, list_activities_for_user
from ...services.credit_flow import credits_enabled

router = APIRouter()


class CreditActivityOut(BaseModel):
    """算力活动条目（含用户领取状态与剩余配额）。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    description: str = ""
    cover_url: str = Field("", alias="coverUrl")
    credit_type: str = Field(..., alias="creditType")
    amount: int
    model_name: str | None = Field(None, alias="modelName")
    model_display_name: str | None = Field(None, alias="modelDisplayName")
    valid_days: int = Field(..., alias="validDays")
    starts_at: str | None = Field(None, alias="startsAt")
    ends_at: str | None = Field(None, alias="endsAt")
    per_user_limit: int = Field(..., alias="perUserLimit")
    total_quota: int | None = Field(None, alias="totalQuota")
    claimed_count: int = Field(..., alias="claimedCount")
    status: str
    user_claim_count: int = Field(..., alias="userClaimCount")
    can_claim: bool = Field(..., alias="canClaim")
    remaining_quota: int | None = Field(None, alias="remainingQuota")
    claim_rules: dict | None = Field(None, alias="claimRules")
    claim_rule_summary: list[str] = Field(default_factory=list, alias="claimRuleSummary")
    eligible: bool = True
    eligibility_reasons: list[str] = Field(default_factory=list, alias="eligibilityReasons")


class CreditActivityListOut(BaseModel):
    """算力活动列表响应。"""

    items: list[CreditActivityOut]


class CreditActivityClaimOut(BaseModel):
    """算力活动领取成功响应。"""

    model_config = ConfigDict(populate_by_name=True)

    ok: bool = True
    activity_id: str = Field(..., alias="activityId")
    amount: int
    credit_type: str = Field(..., alias="creditType")
    balance: float
    expires_at: str = Field(..., alias="expiresAt")


@router.get("/activities", response_model=CreditActivityListOut)
async def list_credit_activities(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户可查看的算力活动（算力关闭时返回空列表）。"""
    if not credits_enabled():
        return CreditActivityListOut(items=[])
    items = await list_activities_for_user(db, current_user)
    return CreditActivityListOut(items=[CreditActivityOut(**item) for item in items])


@router.post("/activities/{activity_id}/claim", response_model=CreditActivityClaimOut)
async def post_claim_credit_activity(
    activity_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """领取指定算力活动额度并返回入账结果与最新余额。"""
    if not credits_enabled():
        fail(ErrorCode.CREDITS_DISABLED)
    activity_id_int = require_entity_id(activity_id, message="活动 ID 无效")
    result = await claim_activity(db, user=current_user, activity_id=activity_id_int)
    return CreditActivityClaimOut(**result)
