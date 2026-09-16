"""首次注册赠送占位表：同一用户终身仅一条。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK

# 固定业务键：与 user_id 组成 UNIQUE 两列，满足一生一次
REGISTER_BONUS_KEY = "register_bonus"


class RegisterBonusGrant(Base):
    """注册赠送占位：UNIQUE(bonus_key, user_id)，每人仅一次。"""

    __tablename__ = "register_bonus_grants"
    __table_args__ = (
        UniqueConstraint("bonus_key", "user_id", name="uq_register_bonus_grants_key_user"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    bonus_key = Column(String(32), nullable=False, default=REGISTER_BONUS_KEY)
    user_id = Column(BigIntFK, nullable=False, index=True)
    amount = Column(Integer, nullable=False)
    lot_id = Column(BigIntFK, nullable=True)
    # reserved → granted
    status = Column(String(16), nullable=False, default="reserved", index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    granted_at = Column(DateTime, nullable=True)
