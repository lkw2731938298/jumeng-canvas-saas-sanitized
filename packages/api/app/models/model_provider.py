"""模型供应商与加密密钥（P1 DB 化）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntPK, JsonCol


def _cst_now() -> datetime:
    return now_cst_naive()


class ModelProvider(Base):
    """上游供应商元数据（doubao / dashscope / runninghub 等）。"""

    __tablename__ = "model_providers"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    default_api_base = Column(String(512), nullable=True)
    config = Column(JsonCol, nullable=False, default=dict)
    is_enabled = Column(Boolean, nullable=False, default=True)
    sort_order = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=_cst_now, onupdate=_cst_now, nullable=False)


class ProviderCredential(Base):
    """供应商 API 密钥（加密存储）；profile 区分 default / ltx 等。"""

    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint("provider_code", "profile_key", name="uq_provider_credentials_code_profile"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    provider_code = Column(String(64), nullable=False, index=True)
    profile_key = Column(String(64), nullable=False, default="default")
    api_key_enc = Column(Text, nullable=True)
    api_key_hint = Column(String(16), nullable=True)
    endpoint_id = Column(String(256), nullable=True)
    api_base = Column(String(512), nullable=True)
    secrets_enc = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    updated_by = Column(BigIntPK, nullable=True)
    updated_at = Column(DateTime, default=_cst_now, onupdate=_cst_now, nullable=False)
