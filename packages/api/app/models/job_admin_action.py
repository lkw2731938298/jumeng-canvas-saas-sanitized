"""Audit log for admin interventions on generation jobs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntPK, BigIntFK, JsonCol


def _cst_now() -> datetime:
    return now_cst_naive()


class GenerationJobAdminAction(Base):
    __tablename__ = "generation_job_admin_actions"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    job_id = Column(BigIntFK, nullable=False, index=True)
    operator_id = Column(BigIntFK, nullable=False, index=True)
    action = Column(String(32), nullable=False)
    before_status = Column(String(16), nullable=True)
    after_status = Column(String(16), nullable=True)
    before_credit_status = Column(String(32), nullable=True)
    after_credit_status = Column(String(32), nullable=True)
    note = Column(Text, nullable=False)
    payload = Column(JsonCol, nullable=False, default=dict)
    created_at = Column(DateTime, default=_cst_now, nullable=False, index=True)
