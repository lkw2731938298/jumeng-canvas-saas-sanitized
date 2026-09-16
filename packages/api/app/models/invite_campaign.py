"""用户邀请活动、绑定关系与发奖占位表。"""

from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from .types import BigIntPK, BigIntFK
from .database import Base


class InviteCampaign(Base):
    """邀请活动配置（同时通常仅一条 active）。"""

    __tablename__ = "invite_campaigns"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    title = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    cover_url = Column(Text, nullable=True)
    # 邀请人 / 被邀请人奖励算力点
    inviter_reward_amount = Column(Integer, nullable=False, default=0)
    invitee_reward_amount = Column(Integer, nullable=False, default=0)
    reward_credit_type = Column(String(32), nullable=False, default="activity")
    reward_valid_days = Column(Integer, nullable=False, default=30)
    # register=注册即发邀请人奖；invitee_first_recharge=被邀请人首充后再发
    inviter_reward_on = Column(String(32), nullable=False, default="register")
    max_rewards_per_inviter = Column(Integer, nullable=True)
    total_invite_quota = Column(Integer, nullable=True)
    rewarded_invitee_count = Column(Integer, nullable=False, default=0)
    rewarded_inviter_count = Column(Integer, nullable=False, default=0)
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    status = Column(String(16), nullable=False, default="draft", index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, nullable=False)


class InviteBinding(Base):
    """邀请归因：一名被邀请人终身仅一条。"""

    __tablename__ = "invite_bindings"
    __table_args__ = (
        UniqueConstraint("invitee_user_id", name="uq_invite_bindings_invitee"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    invitee_user_id = Column(BigIntFK, nullable=False, index=True)
    inviter_user_id = Column(BigIntFK, nullable=False, index=True)
    invite_code = Column(String(6), nullable=False)
    campaign_id = Column(BigIntFK, nullable=True, index=True)
    # none / pending / granted / skipped
    invitee_reward_status = Column(String(16), nullable=False, default="none")
    inviter_reward_status = Column(String(16), nullable=False, default="none")
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)


class InviteRewardGrant(Base):
    """发奖占位：同一 binding × 角色终身只发一次（防双加）。"""

    __tablename__ = "invite_reward_grants"
    __table_args__ = (
        UniqueConstraint(
            "binding_id",
            "beneficiary_role",
            name="uq_invite_reward_grants_binding_role",
        ),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    binding_id = Column(BigIntFK, nullable=False, index=True)
    beneficiary_role = Column(String(16), nullable=False)  # invitee | inviter
    beneficiary_user_id = Column(BigIntFK, nullable=False, index=True)
    campaign_id = Column(BigIntFK, nullable=True)
    amount = Column(Integer, nullable=False)
    credit_type = Column(String(32), nullable=False, default="activity")
    lot_id = Column(BigIntFK, nullable=True)
    # reserved → granted
    status = Column(String(16), nullable=False, default="reserved")
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    granted_at = Column(DateTime, nullable=True)
