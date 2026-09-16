"""用户站内通知：充值结果、任务失败、技能/工作流审核等。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK


class UserNotification(Base):
    """面向用户的站内消息（权威存 MySQL）。"""

    __tablename__ = "user_notifications"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    # recharge | job | skill_review | workflow_review
    category = Column(String(32), nullable=False, index=True)
    title = Column(String(128), nullable=False, default="")
    body = Column(Text, nullable=False, default="")
    # 可选跳转（站内路径或空）
    link_url = Column(String(512), nullable=True)
    ref_type = Column(String(32), nullable=True)
    ref_id = Column(String(64), nullable=True)
    # 幂等键：同用户同 key 只插一条（如 recharge:success:{outTradeNo}）
    dedupe_key = Column(String(160), nullable=True)
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("user_id", "dedupe_key", name="uq_user_notifications_user_dedupe"),
        Index("ix_user_notifications_user_created", "user_id", "created_at"),
        Index("ix_user_notifications_user_unread", "user_id", "is_read", "created_at"),
    )
