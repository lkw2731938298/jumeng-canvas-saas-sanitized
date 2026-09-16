"""用户邀请归因与算力发奖（对齐 §8.11：行锁 + UNIQUE + Redis 标记锁）。"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.credit_types import ALL_CREDIT_TYPES, CREDIT_TYPE_ACTIVITY, CREDIT_TYPE_GENERAL
from ..core.datetime_util import as_cst_aware, now_cst_aware, now_cst_naive, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import AppError, fail
from ..models.credit import CreditRechargeOrder
from ..models.invite_campaign import InviteBinding, InviteCampaign, InviteRewardGrant
from ..models.user import User
from .auth_sms import mask_phone
from .credit_transactions import grant_credits_with_log
from .invite_reward_lock import (
    claim_invite_bind,
    claim_invite_reward,
    is_invite_bind_in_progress,
    is_invite_reward_in_progress,
    seal_invite_bind,
    seal_invite_reward,
)

logger = logging.getLogger(__name__)

# 去掉易混字符 0 O I L 1
_INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_INVITE_CODE_LEN = 6

CAMPAIGN_STATUS_DRAFT = "draft"
CAMPAIGN_STATUS_ACTIVE = "active"
CAMPAIGN_STATUS_ENDED = "ended"

REWARD_STATUS_NONE = "none"
REWARD_STATUS_PENDING = "pending"
REWARD_STATUS_GRANTED = "granted"
REWARD_STATUS_SKIPPED = "skipped"

ROLE_INVITEE = "invitee"
ROLE_INVITER = "inviter"

INVITER_ON_REGISTER = "register"
INVITER_ON_FIRST_RECHARGE = "invitee_first_recharge"

GRANT_STATUS_RESERVED = "reserved"
GRANT_STATUS_GRANTED = "granted"

SOURCE_INVITE_REWARD = "invite_reward"


def normalize_invite_code(raw: str | None) -> str:
    """规范化邀请码：去空白、大写。"""
    return (raw or "").strip().upper()


def _gen_code() -> str:
    return "".join(secrets.choice(_INVITE_CODE_ALPHABET) for _ in range(_INVITE_CODE_LEN))


async def ensure_user_invite_code(db: AsyncSession, user: User) -> str:
    """懒生成用户专属 6 位邀请码（先查唯一性，冲突用 savepoint 重试）。"""
    existing = (getattr(user, "invite_code", None) or "").strip().upper()
    if existing:
        return existing
    for _ in range(20):
        code = _gen_code()
        taken = await db.execute(select(User.id).filter(User.invite_code == code).limit(1))
        if taken.scalar_one_or_none() is not None:
            continue
        try:
            async with db.begin_nested():
                user.invite_code = code
                await db.flush()
            return code
        except IntegrityError:
            await db.refresh(user)
            if (user.invite_code or "").strip():
                return str(user.invite_code).strip().upper()
            user.invite_code = None
    fail(ErrorCode.INTERNAL_ERROR, message="邀请码生成失败，请稍后重试")


async def resolve_inviter_by_code(db: AsyncSession, code: str) -> User:
    """按邀请码解析邀请人；无效则 fail。"""
    norm = normalize_invite_code(code)
    if len(norm) != _INVITE_CODE_LEN:
        fail(ErrorCode.INVITE_CODE_INVALID)
    row = await db.execute(select(User).filter(User.invite_code == norm))
    inviter = row.scalar_one_or_none()
    if not inviter or not inviter.is_active:
        fail(ErrorCode.INVITE_CODE_INVALID)
    return inviter


def _campaign_is_active(campaign: InviteCampaign, now=None) -> bool:
    now = now or now_cst_aware()
    if campaign.status != CAMPAIGN_STATUS_ACTIVE:
        return False
    starts = as_cst_aware(campaign.starts_at)
    ends = as_cst_aware(campaign.ends_at)
    if starts and now < starts:
        return False
    if ends and now > ends:
        return False
    return True


async def get_active_campaign(db: AsyncSession) -> InviteCampaign | None:
    """当前进行中的邀请活动（最多取一条）。"""
    now = now_cst_naive()
    result = await db.execute(
        select(InviteCampaign)
        .filter(
            InviteCampaign.status == CAMPAIGN_STATUS_ACTIVE,
            InviteCampaign.starts_at <= now,
            InviteCampaign.ends_at >= now,
        )
        .order_by(InviteCampaign.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def campaign_public_dict(campaign: InviteCampaign | None) -> dict[str, Any] | None:
    if not campaign:
        return None
    remaining = None
    if campaign.total_invite_quota is not None:
        remaining = max(0, int(campaign.total_invite_quota) - int(campaign.rewarded_invitee_count or 0))
    return {
        "id": str(campaign.id),
        "title": campaign.title or "",
        "description": campaign.description or "",
        "coverUrl": campaign.cover_url or "",
        "inviterRewardAmount": int(campaign.inviter_reward_amount or 0),
        "inviteeRewardAmount": int(campaign.invitee_reward_amount or 0),
        "rewardCreditType": campaign.reward_credit_type or CREDIT_TYPE_ACTIVITY,
        "rewardValidDays": int(campaign.reward_valid_days or 30),
        "inviterRewardOn": campaign.inviter_reward_on or INVITER_ON_REGISTER,
        "maxRewardsPerInviter": campaign.max_rewards_per_inviter,
        "totalInviteQuota": campaign.total_invite_quota,
        "remainingQuota": remaining,
        "startsAt": to_cst_iso(campaign.starts_at),
        "endsAt": to_cst_iso(campaign.ends_at),
        "status": campaign.status,
    }


def _share_url(invite_code: str) -> str:
    """生成注册邀请链接；须带上当前环境 Web basePath（测服 /canvas，生产 www 为空）。"""
    settings = get_settings()
    origin = (settings.canvas_public_origin or "").rstrip("/")
    if not origin:
        origin = "https://www.example.com"
    base = (settings.canvas_web_base_path or "").strip().rstrip("/")
    if base and not base.startswith("/"):
        base = f"/{base}"
    path = f"{base}/register" if base else "/register"
    return f"{origin}{path}?invite={invite_code}"


def _source_ref(binding_id: int, role: str) -> str:
    return f"invite:{int(binding_id)}:{role}"


async def _count_inviter_granted(db: AsyncSession, inviter_user_id: int, campaign_id: int) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(InviteBinding)
        .filter(
            InviteBinding.inviter_user_id == inviter_user_id,
            InviteBinding.campaign_id == campaign_id,
            InviteBinding.inviter_reward_status == REWARD_STATUS_GRANTED,
        )
    )
    return int(result.scalar() or 0)


async def _grant_invite_role(
    db: AsyncSession,
    *,
    binding: InviteBinding,
    campaign: InviteCampaign,
    role: str,
    beneficiary: User,
    amount: int,
) -> str:
    """对单一角色发奖：reserved 占位 → grant lot → granted。返回最终 reward status。"""
    status_attr = "invitee_reward_status" if role == ROLE_INVITEE else "inviter_reward_status"

    if amount <= 0:
        setattr(binding, status_attr, REWARD_STATUS_SKIPPED)
        return REWARD_STATUS_SKIPPED

    # 已有 granted grant → 幂等
    existing = await db.execute(
        select(InviteRewardGrant)
        .filter(
            InviteRewardGrant.binding_id == binding.id,
            InviteRewardGrant.beneficiary_role == role,
        )
        .with_for_update()
    )
    grant = existing.scalar_one_or_none()
    if grant and grant.status == GRANT_STATUS_GRANTED:
        setattr(binding, status_attr, REWARD_STATUS_GRANTED)
        return REWARD_STATUS_GRANTED

    # 已有 reserved 占位：崩溃/中断后补发，不再抢 Redis claim（锁可能仍在）
    resume_reserved = grant is not None and grant.status == GRANT_STATUS_RESERVED

    if not resume_reserved:
        if await is_invite_reward_in_progress(binding.id, role):
            setattr(binding, status_attr, REWARD_STATUS_PENDING)
            return REWARD_STATUS_PENDING
        if not await claim_invite_reward(binding.id, role):
            setattr(binding, status_attr, REWARD_STATUS_PENDING)
            return REWARD_STATUS_PENDING

    try:
        if grant is None:
            grant = InviteRewardGrant(
                binding_id=binding.id,
                beneficiary_role=role,
                beneficiary_user_id=int(beneficiary.id),
                campaign_id=int(campaign.id),
                amount=int(amount),
                credit_type=campaign.reward_credit_type or CREDIT_TYPE_ACTIVITY,
                status=GRANT_STATUS_RESERVED,
            )
            try:
                async with db.begin_nested():
                    db.add(grant)
                    await db.flush()
            except IntegrityError:
                row = await db.execute(
                    select(InviteRewardGrant)
                    .filter(
                        InviteRewardGrant.binding_id == binding.id,
                        InviteRewardGrant.beneficiary_role == role,
                    )
                    .with_for_update()
                )
                grant = row.scalar_one_or_none()
                if grant and grant.status == GRANT_STATUS_GRANTED:
                    setattr(binding, status_attr, REWARD_STATUS_GRANTED)
                    await seal_invite_reward(binding.id, role)
                    return REWARD_STATUS_GRANTED
                if grant is None:
                    setattr(binding, status_attr, REWARD_STATUS_PENDING)
                    return REWARD_STATUS_PENDING

        if grant.status == GRANT_STATUS_GRANTED:
            setattr(binding, status_attr, REWARD_STATUS_GRANTED)
            await seal_invite_reward(binding.id, role)
            return REWARD_STATUS_GRANTED

        # 幂等：若 lot 已按 source_ref 存在，只回填不重复发放
        from ..models.credit_lot import CreditLot

        existing_lot = await db.execute(
            select(CreditLot)
            .filter(
                CreditLot.user_id == beneficiary.id,
                CreditLot.source == SOURCE_INVITE_REWARD,
                CreditLot.source_ref == _source_ref(binding.id, role),
            )
            .limit(1)
        )
        lot = existing_lot.scalar_one_or_none()
        if lot is None:
            credit_type = grant.credit_type or CREDIT_TYPE_ACTIVITY
            if credit_type not in ALL_CREDIT_TYPES:
                credit_type = CREDIT_TYPE_ACTIVITY
            valid_days = (
                int(campaign.reward_valid_days or 30) if credit_type != CREDIT_TYPE_GENERAL else None
            )
            await grant_credits_with_log(
                db,
                user=beneficiary,
                amount=int(grant.amount),
                credit_type=credit_type,
                source=SOURCE_INVITE_REWARD,
                reason=f"邀请奖励（{role}）",
                valid_days=valid_days,
                source_ref=_source_ref(binding.id, role),
            )
            lot_row = await db.execute(
                select(CreditLot)
                .filter(
                    CreditLot.user_id == beneficiary.id,
                    CreditLot.source == SOURCE_INVITE_REWARD,
                    CreditLot.source_ref == _source_ref(binding.id, role),
                )
                .limit(1)
            )
            lot = lot_row.scalar_one_or_none()
            if lot is None:
                setattr(binding, status_attr, REWARD_STATUS_PENDING)
                logger.error("invite grant missing lot binding=%s role=%s", binding.id, role)
                return REWARD_STATUS_PENDING
            # 仅新发放时递增计数
            if role == ROLE_INVITEE:
                campaign.rewarded_invitee_count = int(campaign.rewarded_invitee_count or 0) + 1
            else:
                campaign.rewarded_inviter_count = int(campaign.rewarded_inviter_count or 0) + 1
            campaign.updated_at = now_cst_naive()

        grant.lot_id = lot.id
        grant.status = GRANT_STATUS_GRANTED
        grant.granted_at = now_cst_naive()
        setattr(binding, status_attr, REWARD_STATUS_GRANTED)

        await db.flush()
        await seal_invite_reward(binding.id, role)
        return REWARD_STATUS_GRANTED
    except AppError:
        setattr(binding, status_attr, REWARD_STATUS_PENDING)
        raise
    except Exception:
        logger.exception("invite grant failed binding=%s role=%s", binding.id, role)
        setattr(binding, status_attr, REWARD_STATUS_PENDING)
        return REWARD_STATUS_PENDING


async def apply_invite_on_register(
    db: AsyncSession,
    *,
    invitee: User,
    invite_code: str | None,
) -> dict[str, Any]:
    """注册成功后归因并发奖。无码则空结果；无效码应在调用方建用户前已校验。"""
    norm = normalize_invite_code(invite_code)
    if not norm:
        return {"bound": False}

    inviter = await resolve_inviter_by_code(db, norm)
    if int(inviter.id) == int(invitee.id):
        fail(ErrorCode.INVITE_CODE_SELF)

    invitee.referred_by_user_id = int(inviter.id)
    await db.flush()

    if await is_invite_bind_in_progress(int(invitee.id)):
        # 并发重复：若已有 binding 则幂等返回
        existing = await db.execute(
            select(InviteBinding).filter(InviteBinding.invitee_user_id == invitee.id)
        )
        if existing.scalar_one_or_none():
            return {"bound": True, "inviteeRewardAmount": 0, "inviterPending": False}
        fail(ErrorCode.INVITE_REWARD_IN_PROGRESS)

    if not await claim_invite_bind(int(invitee.id)):
        fail(ErrorCode.INVITE_REWARD_IN_PROGRESS)

    invitee_amount = 0
    inviter_pending = False
    try:
        campaign = await get_active_campaign(db)
        campaign_id = int(campaign.id) if campaign else None

        binding = InviteBinding(
            invitee_user_id=int(invitee.id),
            inviter_user_id=int(inviter.id),
            invite_code=norm,
            campaign_id=campaign_id,
            invitee_reward_status=REWARD_STATUS_NONE,
            inviter_reward_status=REWARD_STATUS_NONE,
        )
        try:
            async with db.begin_nested():
                db.add(binding)
                await db.flush()
        except IntegrityError:
            row = await db.execute(
                select(InviteBinding).filter(InviteBinding.invitee_user_id == invitee.id)
            )
            binding = row.scalar_one()
            await seal_invite_bind(int(invitee.id))
            return {"bound": True, "inviteeRewardAmount": 0, "inviterPending": False}

        if not campaign or not _campaign_is_active(campaign):
            binding.invitee_reward_status = REWARD_STATUS_SKIPPED
            binding.inviter_reward_status = REWARD_STATUS_SKIPPED
            await db.flush()
            await seal_invite_bind(int(invitee.id))
            return {"bound": True, "inviteeRewardAmount": 0, "inviterPending": False, "campaignInactive": True}

        # 行锁活动，校验配额
        locked = await db.execute(
            select(InviteCampaign).filter(InviteCampaign.id == campaign.id).with_for_update()
        )
        campaign = locked.scalar_one()

        quota_ok = True
        if campaign.total_invite_quota is not None:
            if int(campaign.rewarded_invitee_count or 0) >= int(campaign.total_invite_quota):
                quota_ok = False

        if not quota_ok:
            binding.invitee_reward_status = REWARD_STATUS_SKIPPED
            binding.inviter_reward_status = REWARD_STATUS_SKIPPED
            await db.flush()
            await seal_invite_bind(int(invitee.id))
            return {"bound": True, "inviteeRewardAmount": 0, "inviterPending": False, "quotaExceeded": True}

        # 被邀请人奖励
        st = await _grant_invite_role(
            db,
            binding=binding,
            campaign=campaign,
            role=ROLE_INVITEE,
            beneficiary=invitee,
            amount=int(campaign.invitee_reward_amount or 0),
        )
        if st == REWARD_STATUS_GRANTED:
            invitee_amount = int(campaign.invitee_reward_amount or 0)

        # 邀请人奖励
        inviter_on = campaign.inviter_reward_on or INVITER_ON_REGISTER
        max_per = campaign.max_rewards_per_inviter
        inviter_cap_ok = True
        if max_per is not None:
            granted_n = await _count_inviter_granted(db, int(inviter.id), int(campaign.id))
            if granted_n >= int(max_per):
                inviter_cap_ok = False

        if not inviter_cap_ok:
            binding.inviter_reward_status = REWARD_STATUS_SKIPPED
        elif inviter_on == INVITER_ON_FIRST_RECHARGE:
            binding.inviter_reward_status = REWARD_STATUS_PENDING
            inviter_pending = True
        else:
            inviter_row = await db.execute(
                select(User).filter(User.id == inviter.id).with_for_update()
            )
            inviter_locked = inviter_row.scalar_one()
            st_inv = await _grant_invite_role(
                db,
                binding=binding,
                campaign=campaign,
                role=ROLE_INVITER,
                beneficiary=inviter_locked,
                amount=int(campaign.inviter_reward_amount or 0),
            )
            if st_inv == REWARD_STATUS_PENDING:
                inviter_pending = True

        await db.flush()
        await seal_invite_bind(int(invitee.id))
        return {
            "bound": True,
            "inviteeRewardAmount": invitee_amount,
            "inviterPending": inviter_pending,
        }
    except AppError:
        raise
    except Exception:
        logger.exception("apply_invite_on_register failed invitee=%s", invitee.id)
        await seal_invite_bind(int(invitee.id))
        return {"bound": True, "inviteeRewardAmount": 0, "inviterPending": True, "error": True}


async def try_grant_inviter_on_first_recharge(db: AsyncSession, *, invitee_user_id: int) -> None:
    """充值入账成功后：若规则为首充发奖且本单为真首充，则发邀请人奖励。异常不抛出。"""
    try:
        binding_row = await db.execute(
            select(InviteBinding).filter(InviteBinding.invitee_user_id == int(invitee_user_id))
        )
        binding = binding_row.scalar_one_or_none()
        if not binding:
            return
        if binding.inviter_reward_status == REWARD_STATUS_GRANTED:
            return
        if binding.inviter_reward_status == REWARD_STATUS_SKIPPED:
            return
        if not binding.campaign_id:
            return

        camp_row = await db.execute(
            select(InviteCampaign).filter(InviteCampaign.id == binding.campaign_id).with_for_update()
        )
        campaign = camp_row.scalar_one_or_none()
        if not campaign:
            return
        if (campaign.inviter_reward_on or INVITER_ON_REGISTER) != INVITER_ON_FIRST_RECHARGE:
            return

        # 已完成充值单数（含本单）须为 1
        cnt_row = await db.execute(
            select(func.count())
            .select_from(CreditRechargeOrder)
            .filter(
                CreditRechargeOrder.user_id == int(invitee_user_id),
                CreditRechargeOrder.status == "completed",
                CreditRechargeOrder.order_type == "recharge",
            )
        )
        if int(cnt_row.scalar() or 0) != 1:
            return

        max_per = campaign.max_rewards_per_inviter
        if max_per is not None:
            granted_n = await _count_inviter_granted(db, int(binding.inviter_user_id), int(campaign.id))
            if granted_n >= int(max_per):
                binding.inviter_reward_status = REWARD_STATUS_SKIPPED
                await db.flush()
                return

        inviter_row = await db.execute(
            select(User).filter(User.id == binding.inviter_user_id).with_for_update()
        )
        inviter = inviter_row.scalar_one_or_none()
        if not inviter:
            return

        await _grant_invite_role(
            db,
            binding=binding,
            campaign=campaign,
            role=ROLE_INVITER,
            beneficiary=inviter,
            amount=int(campaign.inviter_reward_amount or 0),
        )
        await db.flush()
    except Exception:
        logger.exception("try_grant_inviter_on_first_recharge failed invitee=%s", invitee_user_id)


async def reconcile_pending_for_user(db: AsyncSession, user_id: int) -> None:
    """补发与当前用户相关的 pending 邀请奖励（打开邀请页时调用）。"""
    result = await db.execute(
        select(InviteBinding).filter(
            (InviteBinding.invitee_user_id == user_id) | (InviteBinding.inviter_user_id == user_id)
        )
    )
    bindings = list(result.scalars().all())
    for binding in bindings:
        if binding.invitee_reward_status != REWARD_STATUS_PENDING and binding.inviter_reward_status != REWARD_STATUS_PENDING:
            continue
        if not binding.campaign_id:
            continue
        camp_row = await db.execute(
            select(InviteCampaign).filter(InviteCampaign.id == binding.campaign_id).with_for_update()
        )
        campaign = camp_row.scalar_one_or_none()
        if not campaign:
            continue

        if binding.invitee_reward_status == REWARD_STATUS_PENDING:
            invitee_row = await db.execute(
                select(User).filter(User.id == binding.invitee_user_id).with_for_update()
            )
            invitee = invitee_row.scalar_one_or_none()
            if invitee:
                await _grant_invite_role(
                    db,
                    binding=binding,
                    campaign=campaign,
                    role=ROLE_INVITEE,
                    beneficiary=invitee,
                    amount=int(campaign.invitee_reward_amount or 0),
                )

        if binding.inviter_reward_status == REWARD_STATUS_PENDING:
            # 首充模式且尚未首充：保持 pending
            if (campaign.inviter_reward_on or INVITER_ON_REGISTER) == INVITER_ON_FIRST_RECHARGE:
                cnt_row = await db.execute(
                    select(func.count())
                    .select_from(CreditRechargeOrder)
                    .filter(
                        CreditRechargeOrder.user_id == binding.invitee_user_id,
                        CreditRechargeOrder.status == "completed",
                        CreditRechargeOrder.order_type == "recharge",
                    )
                )
                if int(cnt_row.scalar() or 0) < 1:
                    continue
            inviter_row = await db.execute(
                select(User).filter(User.id == binding.inviter_user_id).with_for_update()
            )
            inviter = inviter_row.scalar_one_or_none()
            if inviter:
                await _grant_invite_role(
                    db,
                    binding=binding,
                    campaign=campaign,
                    role=ROLE_INVITER,
                    beneficiary=inviter,
                    amount=int(campaign.inviter_reward_amount or 0),
                )
    await db.flush()


def _reward_status_label(status: str, *, inviter_on: str) -> str:
    if status == REWARD_STATUS_GRANTED:
        return "已获奖励"
    if status == REWARD_STATUS_SKIPPED:
        return "已达上限跳过"
    if status == REWARD_STATUS_PENDING:
        if inviter_on == INVITER_ON_FIRST_RECHARGE:
            return "待首充"
        return "待补发"
    return "未发奖"


async def get_referral_me(db: AsyncSession, user: User) -> dict[str, Any]:
    """邀请页聚合：码、链接、活动、人数、明细。"""
    await reconcile_pending_for_user(db, int(user.id))
    code = await ensure_user_invite_code(db, user)
    campaign = await get_active_campaign(db)

    list_row = await db.execute(
        select(InviteBinding, User)
        .join(User, User.id == InviteBinding.invitee_user_id)
        .filter(InviteBinding.inviter_user_id == user.id)
        .order_by(InviteBinding.created_at.desc())
        .limit(200)
    )
    items: list[dict[str, Any]] = []
    rewarded_total = 0
    inviter_on = (campaign.inviter_reward_on if campaign else INVITER_ON_REGISTER) or INVITER_ON_REGISTER
    for binding, invitee in list_row.all():
        if binding.inviter_reward_status == REWARD_STATUS_GRANTED and campaign:
            rewarded_total += int(campaign.inviter_reward_amount or 0)
        items.append(
            {
                "inviteeUserId": str(invitee.id),
                "displayName": (invitee.display_name or "").strip() or "用户",
                "phoneMasked": mask_phone(invitee.phone or ""),
                "registeredAt": to_cst_iso(invitee.created_at),
                "inviteeRewardStatus": binding.invitee_reward_status,
                "inviterRewardStatus": binding.inviter_reward_status,
                "statusLabel": _reward_status_label(binding.inviter_reward_status, inviter_on=inviter_on),
                "createdAt": to_cst_iso(binding.created_at),
            }
        )

    return {
        "inviteCode": code,
        "shareUrl": _share_url(code),
        "inviteCount": len(items),
        "rewardedCredits": rewarded_total,
        "campaign": campaign_public_dict(campaign),
        "invites": items,
    }


# —— 管理端 CRUD ——


def _campaign_admin_dict(c: InviteCampaign) -> dict[str, Any]:
    d = campaign_public_dict(c) or {}
    d.update(
        {
            "rewardedInviteeCount": int(c.rewarded_invitee_count or 0),
            "rewardedInviterCount": int(c.rewarded_inviter_count or 0),
            "createdAt": to_cst_iso(c.created_at),
            "updatedAt": to_cst_iso(c.updated_at),
        }
    )
    return d


async def list_campaigns_admin(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(InviteCampaign).order_by(InviteCampaign.id.desc()))
    return [_campaign_admin_dict(c) for c in result.scalars().all()]


async def get_campaign_admin(db: AsyncSession, campaign_id: int) -> InviteCampaign:
    row = await db.execute(select(InviteCampaign).filter(InviteCampaign.id == campaign_id))
    c = row.scalar_one_or_none()
    if not c:
        fail(ErrorCode.INVITE_CAMPAIGN_NOT_FOUND)
    return c


async def create_campaign(
    db: AsyncSession,
    *,
    title: str,
    description: str = "",
    cover_url: str = "",
    inviter_reward_amount: int = 100,
    invitee_reward_amount: int = 50,
    reward_credit_type: str = CREDIT_TYPE_ACTIVITY,
    reward_valid_days: int = 30,
    inviter_reward_on: str = INVITER_ON_REGISTER,
    max_rewards_per_inviter: int | None = None,
    total_invite_quota: int | None = None,
    starts_at,
    ends_at,
    status: str = CAMPAIGN_STATUS_DRAFT,
) -> dict[str, Any]:
    if as_cst_aware(ends_at) <= as_cst_aware(starts_at):
        fail(ErrorCode.INVITE_CAMPAIGN_INVALID_SCHEDULE)
    if reward_credit_type not in ALL_CREDIT_TYPES:
        fail(ErrorCode.BAD_REQUEST, message="无效的算力类型")
    if inviter_reward_on not in (INVITER_ON_REGISTER, INVITER_ON_FIRST_RECHARGE):
        fail(ErrorCode.BAD_REQUEST, message="无效的邀请人发奖时机")

    if status == CAMPAIGN_STATUS_ACTIVE:
        await _end_other_active(db)

    c = InviteCampaign(
        title=title.strip() or "邀请有礼",
        description=description or "",
        cover_url=cover_url or "",
        inviter_reward_amount=max(0, int(inviter_reward_amount)),
        invitee_reward_amount=max(0, int(invitee_reward_amount)),
        reward_credit_type=reward_credit_type,
        reward_valid_days=max(1, int(reward_valid_days)),
        inviter_reward_on=inviter_reward_on,
        max_rewards_per_inviter=max_rewards_per_inviter,
        total_invite_quota=total_invite_quota,
        starts_at=starts_at,
        ends_at=ends_at,
        status=status,
    )
    db.add(c)
    await db.flush()
    return _campaign_admin_dict(c)


async def _end_other_active(db: AsyncSession, *, keep_id: int | None = None) -> None:
    result = await db.execute(
        select(InviteCampaign).filter(InviteCampaign.status == CAMPAIGN_STATUS_ACTIVE)
    )
    now = now_cst_naive()
    for c in result.scalars().all():
        if keep_id is not None and int(c.id) == int(keep_id):
            continue
        c.status = CAMPAIGN_STATUS_ENDED
        c.updated_at = now


async def update_campaign(db: AsyncSession, campaign_id: int, **fields) -> dict[str, Any]:
    c = await get_campaign_admin(db, campaign_id)
    if "title" in fields and fields["title"] is not None:
        c.title = str(fields["title"]).strip() or c.title
    if "description" in fields and fields["description"] is not None:
        c.description = str(fields["description"])
    if "cover_url" in fields and fields["cover_url"] is not None:
        c.cover_url = str(fields["cover_url"])
    if "inviter_reward_amount" in fields and fields["inviter_reward_amount"] is not None:
        c.inviter_reward_amount = max(0, int(fields["inviter_reward_amount"]))
    if "invitee_reward_amount" in fields and fields["invitee_reward_amount"] is not None:
        c.invitee_reward_amount = max(0, int(fields["invitee_reward_amount"]))
    if "reward_credit_type" in fields and fields["reward_credit_type"] is not None:
        if fields["reward_credit_type"] not in ALL_CREDIT_TYPES:
            fail(ErrorCode.BAD_REQUEST, message="无效的算力类型")
        c.reward_credit_type = fields["reward_credit_type"]
    if "reward_valid_days" in fields and fields["reward_valid_days"] is not None:
        c.reward_valid_days = max(1, int(fields["reward_valid_days"]))
    if "inviter_reward_on" in fields and fields["inviter_reward_on"] is not None:
        if fields["inviter_reward_on"] not in (INVITER_ON_REGISTER, INVITER_ON_FIRST_RECHARGE):
            fail(ErrorCode.BAD_REQUEST, message="无效的邀请人发奖时机")
        c.inviter_reward_on = fields["inviter_reward_on"]
    if "max_rewards_per_inviter" in fields:
        c.max_rewards_per_inviter = fields["max_rewards_per_inviter"]
    if "total_invite_quota" in fields:
        c.total_invite_quota = fields["total_invite_quota"]
    if "starts_at" in fields and fields["starts_at"] is not None:
        c.starts_at = fields["starts_at"]
    if "ends_at" in fields and fields["ends_at"] is not None:
        c.ends_at = fields["ends_at"]
    if as_cst_aware(c.ends_at) <= as_cst_aware(c.starts_at):
        fail(ErrorCode.INVITE_CAMPAIGN_INVALID_SCHEDULE)
    if "status" in fields and fields["status"] is not None:
        new_status = str(fields["status"])
        if new_status not in (CAMPAIGN_STATUS_DRAFT, CAMPAIGN_STATUS_ACTIVE, CAMPAIGN_STATUS_ENDED):
            fail(ErrorCode.BAD_REQUEST, message="无效状态")
        if new_status == CAMPAIGN_STATUS_ACTIVE:
            await _end_other_active(db, keep_id=int(c.id))
        c.status = new_status
    c.updated_at = now_cst_naive()
    await db.flush()
    return _campaign_admin_dict(c)


async def list_bindings_admin(
    db: AsyncSession,
    *,
    inviter_user_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    q = select(InviteBinding, User).join(User, User.id == InviteBinding.invitee_user_id)
    if inviter_user_id is not None:
        q = q.filter(InviteBinding.inviter_user_id == inviter_user_id)
    count_q = select(func.count()).select_from(InviteBinding)
    if inviter_user_id is not None:
        count_q = count_q.filter(InviteBinding.inviter_user_id == inviter_user_id)
    total = int((await db.execute(count_q)).scalar() or 0)
    rows = await db.execute(
        q.order_by(InviteBinding.created_at.desc()).limit(min(200, max(1, limit))).offset(max(0, offset))
    )
    items = []
    for binding, invitee in rows.all():
        items.append(
            {
                "id": str(binding.id),
                "inviteeUserId": str(binding.invitee_user_id),
                "inviterUserId": str(binding.inviter_user_id),
                "inviteCode": binding.invite_code,
                "campaignId": str(binding.campaign_id) if binding.campaign_id else None,
                "inviteeDisplayName": (invitee.display_name or "").strip(),
                "inviteePhoneMasked": mask_phone(invitee.phone or ""),
                "inviteeRewardStatus": binding.invitee_reward_status,
                "inviterRewardStatus": binding.inviter_reward_status,
                "createdAt": to_cst_iso(binding.created_at),
            }
        )
    return {"total": total, "items": items}


async def reconcile_binding_admin(db: AsyncSession, binding_id: int) -> dict[str, Any]:
    row = await db.execute(select(InviteBinding).filter(InviteBinding.id == binding_id))
    binding = row.scalar_one_or_none()
    if not binding:
        fail(ErrorCode.INVITE_BINDING_NOT_FOUND)
    await reconcile_pending_for_user(db, int(binding.invitee_user_id))
    await db.refresh(binding)
    return {
        "id": str(binding.id),
        "inviteeRewardStatus": binding.invitee_reward_status,
        "inviterRewardStatus": binding.inviter_reward_status,
    }
