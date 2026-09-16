from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

from .types import BigIntPK, BigIntFK
from .database import Base


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    monthly_credits = Column(Integer, nullable=False)
    # 会员有效期内额外云存储配额（GiB），与通用配额相加，不叠加多套餐
    storage_gb = Column(Integer, nullable=False, default=0)
    period_days = Column(Integer, nullable=False, default=30)
    price_cents = Column(Integer, nullable=False, default=0)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, nullable=False)


class UserSubscription(Base):
    __tablename__ = "user_subscriptions"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    plan_id = Column(BigIntFK, nullable=False, index=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    auto_renew = Column(Boolean, nullable=False, default=True)
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, nullable=False)


class SubscriptionGrant(Base):
    __tablename__ = "subscription_grants"
    __table_args__ = (
        # 每用户每账期仅发放一次（先写 grant 再加款）
        UniqueConstraint("user_id", "period_key", name="uq_subscription_grants_user_period"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    subscription_id = Column(BigIntFK, nullable=False, index=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    lot_id = Column(BigIntFK, nullable=True)
    period_key = Column(String(64), nullable=False)
    amount = Column(Integer, nullable=False)
    granted_at = Column(DateTime, default=now_cst_naive, nullable=False)
