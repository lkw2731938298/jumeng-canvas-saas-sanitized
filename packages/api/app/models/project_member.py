"""Project collaboration membership."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntPK, BigIntFK

MEMBER_ROLE_EDITOR = "editor"
MEMBER_STATUS_PENDING = "pending"
MEMBER_STATUS_ACTIVE = "active"
MEMBER_STATUS_REMOVED = "removed"


class ProjectMember(Base):
    __tablename__ = "project_members"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    project_id = Column(BigIntFK, nullable=False, index=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    role = Column(String(16), nullable=False, default=MEMBER_ROLE_EDITOR)
    invited_by = Column(BigIntFK, nullable=False)
    invited_at = Column(DateTime, default=now_cst_naive, nullable=False)
    first_accessed_at = Column(DateTime, nullable=True)
    last_accessed_at = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False, default=MEMBER_STATUS_ACTIVE, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
        Index("ix_project_members_user_status", "user_id", "status"),
    )
