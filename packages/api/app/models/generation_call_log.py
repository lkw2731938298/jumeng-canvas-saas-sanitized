"""模型生成单次上游调用的临时日志（submit / response 阶段）。"""

from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from .database import Base
from .types import BigIntPK, BigIntFK, JsonCol


class GenerationCallLog(Base):
    """generation_call_logs 表：记录每次向上游提交与响应的快照。"""
    __tablename__ = "generation_call_logs"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    job_id = Column(BigIntFK, nullable=True, index=True)
    phase = Column(String(16), nullable=False, index=True)  # submit | response | upstream
    submit_source = Column(String(16), nullable=True, index=True)  # manual | dedupe | auto
    lane = Column(String(32), nullable=True, index=True)
    category = Column(String(32), nullable=False, index=True)
    project_id = Column(BigIntFK, nullable=True, index=True)
    node_id = Column(String(128), nullable=True, index=True)
    workflow_id = Column(String(128), nullable=True)
    actor_user_id = Column(BigIntFK, nullable=True, index=True)
    billing_user_id = Column(BigIntFK, nullable=True, index=True)
    model = Column(String(128), nullable=False, index=True)
    outcome = Column(String(16), nullable=False, index=True)  # success | failure
    request_payload = Column(JsonCol, nullable=True)
    response_payload = Column(JsonCol, nullable=True)
    error_message = Column(Text, nullable=True)
    idempotency_key = Column(String(128), nullable=True)
    dedupe_key = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False, index=True)
