from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, Numeric

from .types import BigIntPK, BigIntFK, JsonCol
from .database import Base


class CreditReservation(Base):
    __tablename__ = "credit_reservations"
    __table_args__ = (
        # 同 job 重队列须 canvas-job-{id}-rq{N} 多次预扣；三列 UNIQUE 防重复 INSERT
        UniqueConstraint("job_id", "user_id", "idempotency_key", name="uq_credit_reservations_job_user_idem"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    job_id = Column(BigIntFK, nullable=True, index=True)
    # 预扣算力点：支持一位小数
    amount = Column(Numeric(14, 1), nullable=False)
    status = Column(String(16), nullable=False, default="reserved", index=True)
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    reference = Column(String(256), nullable=True)
    model_name = Column(String(256), nullable=True)
    allocations = Column(JsonCol, nullable=False, default=list)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, nullable=False)


class CreditRechargeOrder(Base):
    """支付/充值占位单：UNIQUE(user_id, idempotency_key)，先占位再加款或开通会员。"""

    __tablename__ = "credit_recharge_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_credit_recharge_orders_user_idem"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False)
    # 入账算力点（充值单）或套餐赠送算力快照（会员单，仅展示）
    amount = Column(Integer, nullable=False)
    # 支付金额（分）；直连演示充值为 0
    pay_amount_fen = Column(Integer, nullable=False, default=0)
    # recharge | subscription
    order_type = Column(String(32), nullable=False, default="recharge", index=True)
    payment_channel = Column(String(32), nullable=False, default="direct")
    plan_id = Column(BigIntFK, nullable=True, index=True)
    alipay_trade_no = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="pending", index=True)
    # 待支付订单过期时间（创建起 24h）；超时未付标记 expired，禁止入账
    expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    completed_at = Column(DateTime, nullable=True)


class CreditAdminAdjustOrder(Base):
    """管理加扣款占位单：UNIQUE(user_id, idempotency_key)，先占位再改 lot。"""

    __tablename__ = "credit_admin_adjust_orders"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_credit_admin_adjust_orders_user_idem"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    operator_id = Column(BigIntFK, nullable=True, index=True)
    idempotency_key = Column(String(128), nullable=False)
    delta = Column(Numeric(14, 1), nullable=False)
    status = Column(String(16), nullable=False, default="pending", index=True)
    reason = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    completed_at = Column(DateTime, nullable=True)
