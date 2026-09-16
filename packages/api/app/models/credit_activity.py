from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from .types import BigIntPK, BigIntFK, JsonCol
from .database import Base


class CreditActivity(Base):
    __tablename__ = "credit_activities"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    title = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    # 活动展示封面（平台 OSS 签名/公开 URL）
    cover_url = Column(Text, nullable=True)
    credit_type = Column(String(32), nullable=False, default="activity")
    amount = Column(Integer, nullable=False)
    model_name = Column(String(256), nullable=True)
    valid_days = Column(Integer, nullable=False, default=30)
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    # 每人每活动仅允许领取 1 次（业务固定；由 UNIQUE(activity_id, user_id) 兜底）
    per_user_limit = Column(Integer, nullable=False, default=1)
    total_quota = Column(Integer, nullable=True)
    claimed_count = Column(Integer, nullable=False, default=0)
    # 领取资格规则（JSON）：注册时间窗 / 累计充值 / 是否有效会员；空=无额外限制
    claim_rules = Column(JsonCol, nullable=True)
    status = Column(String(16), nullable=False, default="draft", index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, nullable=False)


class CreditActivityClaim(Base):
    __tablename__ = "credit_activity_claims"
    __table_args__ = (
        # 每人每活动唯一一条领取记录（永久防重复）
        UniqueConstraint(
            "activity_id",
            "user_id",
            name="uq_credit_activity_claims_activity_user",
        ),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    activity_id = Column(BigIntFK, nullable=False, index=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    # 保留列以兼容历史库；业务已去掉多行 claim_seq，固定为 1
    claim_seq = Column(Integer, nullable=False, default=1)
    lot_id = Column(BigIntFK, nullable=True)
    amount = Column(Integer, nullable=False)
    claimed_at = Column(DateTime, default=now_cst_naive, nullable=False)
