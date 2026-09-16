from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, Boolean, Numeric
from ..core.datetime_util import now_cst_naive
from .types import BigIntPK, BigIntFK, JsonCol
from .database import Base


def _cst_now() -> datetime:
    return now_cst_naive()

class GenerationJob(Base):
    __tablename__ = "generation_jobs"
    id = Column(BigIntPK, primary_key=True, autoincrement=False)
    user_id = Column(BigIntFK, nullable=False, index=True)
    actor_user_id = Column(BigIntFK, nullable=True, index=True)
    project_id = Column(BigIntFK, nullable=True, index=True)
    workflow_id = Column(BigIntFK, nullable=True, index=True)
    node_id = Column(String(128), nullable=True)
    job_type = Column(String(32), nullable=False, default="image_gen")
    scene = Column(String(32), nullable=True, index=True)
    lane = Column(String(16), nullable=False, default="image")
    status = Column(String(16), nullable=False, default="pending", index=True)
    provider = Column(String(64), nullable=True, index=True)
    request_id = Column(String(64), nullable=True, index=True)
    provider_request_id = Column(String(256), nullable=True)
    provider_task_id = Column(String(256), nullable=True, index=True)
    provider_job_id = Column(String(256), nullable=True)
    processing_id = Column(String(256), nullable=True)
    trace_json = Column(JsonCol, nullable=False, default=dict)
    model = Column(String(256), nullable=True)
    asset_id = Column(String(128), nullable=True, index=True)
    result_text = Column(Text, nullable=True)
    input_params = Column(JsonCol, nullable=False, default=dict)
    output_assets = Column(JsonCol, nullable=True, default=list)
    upstream_job_id = Column(String(256), nullable=True)
    progress_pct = Column(Integer, default=0)
    # 面向用户的算力扣费：支持一位小数
    credit_cost = Column(Numeric(14, 1), default=0)
    upstream_credit_cost = Column(Numeric(14, 1), nullable=True)
    credit_reservation_id = Column(String(128), nullable=True)
    credit_status = Column(String(32), nullable=True)
    pricing_version = Column(Integer, nullable=True)
    credit_breakdown = Column(JsonCol, nullable=True)
    dedupe_key = Column(String(256), unique=True, nullable=True)
    attempt_count = Column(Integer, default=0)
    max_attempts = Column(Integer, default=3)
    error_message = Column(Text, nullable=True)
    anomaly_reason = Column(String(64), nullable=True, index=True)
    anomaly_detected_at = Column(DateTime, nullable=True)
    anomaly_detail = Column(Text, nullable=True)
    last_admin_action = Column(String(32), nullable=True)
    last_admin_action_at = Column(DateTime, nullable=True)
    last_admin_operator_id = Column(BigIntFK, nullable=True)
    last_admin_note = Column(Text, nullable=True)
    worker_claim_id = Column(String(64), nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_cst_now, nullable=False)

class Model(Base):
    __tablename__ = "models"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    name = Column(String(256), unique=True, nullable=False)
    display_name = Column(String(256), nullable=True)
    provider = Column(String(64), nullable=False, default="comfyui")
    model_type = Column(String(32), nullable=False, default="checkpoint")
    category = Column(String(32), nullable=False, default="image")
    description = Column(Text, nullable=True)
    cover_url = Column(Text, nullable=True)
    parameters = Column(JsonCol, nullable=True, default=dict)
    is_available = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=_cst_now, nullable=False)
