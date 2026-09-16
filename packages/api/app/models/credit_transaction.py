from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, Numeric

from .types import BigIntPK, BigIntFK
from .database import Base


class CreditTransaction(Base):
    __tablename__ = "credit_transactions"
    __table_args__ = (
        # 扣费/退流水三列 UNIQUE：reference_key 与 reservation 对齐（含 -rq{N}）
        UniqueConstraint("job_id", "user_id", "reference_key", name="uq_credit_transactions_job_user_ref"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    operator_id = Column(BigIntFK, nullable=True, index=True)
    # 流水变动与余额快照：支持一位小数
    delta = Column(Numeric(14, 1), nullable=False)
    balance_after = Column(Numeric(14, 1), nullable=False)
    source = Column(String(32), nullable=False, index=True)
    credit_type = Column(String(32), nullable=True, index=True)
    lot_id = Column(BigIntFK, nullable=True, index=True)
    model_name = Column(String(256), nullable=True)
    job_id = Column(BigIntFK, nullable=True, index=True)
    reference_key = Column(String(128), nullable=True, index=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False, index=True)
