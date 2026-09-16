"""用户邀请：我的邀请码 / 当前活动。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...models.database import get_db
from ...models.user import User
from ...services.invite_referral import campaign_public_dict, get_active_campaign, get_referral_me

router = APIRouter()


@router.get("/me")
async def referral_me(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """我的邀请码、分享链接、活动介绍与邀请明细。"""
    return await get_referral_me(db, user)


@router.get("/campaign")
async def referral_campaign(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前进行中的邀请活动（公开字段）。"""
    campaign = await get_active_campaign(db)
    return {"campaign": campaign_public_dict(campaign)}
