from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Numeric

from .types import BigIntPK, BigIntFK
from .database import Base


class CreditLot(Base):
    __tablename__ = "credit_lots"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    credit_type = Column(String(32), nullable=False, index=True)
    model_name = Column(String(256), nullable=True, index=True)
    # 算力点：支持一位小数
    amount_initial = Column(Numeric(14, 1), nullable=False)
    amount_remaining = Column(Numeric(14, 1), nullable=False)
    expires_at = Column(DateTime, nullable=True, index=True)
    source = Column(String(64), nullable=False)
    source_ref = Column(String(256), nullable=True)
    status = Column(String(16), nullable=False, default="active", index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, nullable=False)
