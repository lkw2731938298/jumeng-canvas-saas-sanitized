"""管理端：邀请活动 CRUD 与绑定明细。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_INVITE_CAMPAIGN
from ....core.deps import require_permission
from ....core.entity_ids import require_entity_id
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....services.cache import check_rate_limit
from ....services.discover_media import upload_credit_activity_image
from ....services.invite_referral import (
    CAMPAIGN_STATUS_DRAFT,
    create_campaign,
    list_bindings_admin,
    list_campaigns_admin,
    reconcile_binding_admin,
    update_campaign,
)

router = APIRouter()


class InviteCampaignOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class InviteCampaignCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    description: str = ""
    cover_url: str = Field("", alias="coverUrl", max_length=1024)
    inviter_reward_amount: int = Field(100, alias="inviterRewardAmount", ge=0)
    invitee_reward_amount: int = Field(50, alias="inviteeRewardAmount", ge=0)
    reward_credit_type: str = Field("activity", alias="rewardCreditType")
    reward_valid_days: int = Field(30, alias="rewardValidDays", ge=1, le=3650)
    inviter_reward_on: str = Field("register", alias="inviterRewardOn")
    max_rewards_per_inviter: Optional[int] = Field(None, alias="maxRewardsPerInviter", ge=1)
    total_invite_quota: Optional[int] = Field(None, alias="totalInviteQuota", ge=1)
    starts_at: datetime = Field(..., alias="startsAt")
    ends_at: datetime = Field(..., alias="endsAt")
    status: str = CAMPAIGN_STATUS_DRAFT


class InviteCampaignUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = Field(None, alias="coverUrl", max_length=1024)
    inviter_reward_amount: Optional[int] = Field(None, alias="inviterRewardAmount", ge=0)
    invitee_reward_amount: Optional[int] = Field(None, alias="inviteeRewardAmount", ge=0)
    reward_credit_type: Optional[str] = Field(None, alias="rewardCreditType")
    reward_valid_days: Optional[int] = Field(None, alias="rewardValidDays", ge=1, le=3650)
    inviter_reward_on: Optional[str] = Field(None, alias="inviterRewardOn")
    max_rewards_per_inviter: Optional[int] = Field(None, alias="maxRewardsPerInviter")
    total_invite_quota: Optional[int] = Field(None, alias="totalInviteQuota")
    starts_at: Optional[datetime] = Field(None, alias="startsAt")
    ends_at: Optional[datetime] = Field(None, alias="endsAt")
    status: Optional[str] = None


class InviteCampaignImageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    oss_key: str = Field("", alias="ossKey")
    image_url: str = Field(..., alias="imageUrl")
    url: str = ""


@router.post("/invite-campaigns/image", response_model=InviteCampaignImageOut)
async def upload_invite_campaign_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(PERM_INVITE_CAMPAIGN)),
):
    """上传邀请活动封面（复用活动图片存储）。"""
    allowed = await check_rate_limit(
        f"admin:invite-campaign-image:{current_user.id}",
        limit=30,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)
    raw = await file.read()
    try:
        uploaded = upload_credit_activity_image(
            file.filename or "invite.jpg",
            raw,
            file.content_type,
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    url = uploaded.get("imageUrl") or uploaded.get("image_url") or ""
    return InviteCampaignImageOut(
        id=str(uploaded.get("id") or ""),
        oss_key=str(uploaded.get("ossKey") or uploaded.get("oss_key") or ""),
        image_url=url,
        url=url,
    )


@router.get("/invite-campaigns")
async def list_invite_campaigns(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_INVITE_CAMPAIGN)),
):
    items = await list_campaigns_admin(db)
    return {"items": items}


@router.post("/invite-campaigns")
async def create_invite_campaign(
    body: InviteCampaignCreateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_INVITE_CAMPAIGN)),
):
    item = await create_campaign(
        db,
        title=body.title,
        description=body.description,
        cover_url=body.cover_url,
        inviter_reward_amount=body.inviter_reward_amount,
        invitee_reward_amount=body.invitee_reward_amount,
        reward_credit_type=body.reward_credit_type,
        reward_valid_days=body.reward_valid_days,
        inviter_reward_on=body.inviter_reward_on,
        max_rewards_per_inviter=body.max_rewards_per_inviter,
        total_invite_quota=body.total_invite_quota,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=body.status,
    )
    await db.commit()
    return item


@router.patch("/invite-campaigns/{campaign_id}")
async def patch_invite_campaign(
    campaign_id: str,
    body: InviteCampaignUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_INVITE_CAMPAIGN)),
):
    cid = require_entity_id(campaign_id, message="活动 ID 无效")
    payload = body.model_dump(exclude_unset=True)
    item = await update_campaign(db, cid, **payload)
    await db.commit()
    return item


@router.get("/invite-bindings")
async def list_invite_bindings(
    inviterUserId: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_INVITE_CAMPAIGN)),
):
    inviter_id = (
        require_entity_id(inviterUserId, message="邀请人 ID 无效") if inviterUserId else None
    )
    return await list_bindings_admin(db, inviter_user_id=inviter_id, limit=limit, offset=offset)


@router.post("/invite-bindings/{binding_id}/reconcile")
async def reconcile_invite_binding(
    binding_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_INVITE_CAMPAIGN)),
):
    bid = require_entity_id(binding_id, message="绑定 ID 无效")
    out = await reconcile_binding_admin(db, bid)
    await db.commit()
    return out
