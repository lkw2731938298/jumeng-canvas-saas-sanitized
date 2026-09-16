from ..core.datetime_util import now_cst_naive
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer
from .types import BigIntPK, BigIntFK
from .database import Base

class Project(Base):
    __tablename__ = "projects"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    owner_id = Column(BigIntFK, nullable=False, index=True)
    storage_folder = Column(String(64), nullable=False, index=True)
    project_no = Column(String(32), nullable=True, unique=True, index=True)
    title = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    cover_url = Column(Text, nullable=True)
    cover_oss_key = Column(Text, nullable=True)
    is_public = Column(Boolean, default=False)
    # 软删除标记：True 表示已隐藏，用户端 API 不可见、不可访问
    isdel = Column(Boolean, default=False, nullable=False, index=True)
    collaborator_daily_cap = Column(Integer, nullable=True)
    collaborator_total_cap = Column(Integer, nullable=True)
    collaborator_approval_threshold = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)

class Workflow(Base):
    __tablename__ = "workflows"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    project_id = Column(BigIntFK, nullable=False, index=True)
    title = Column(String(256), nullable=True, default="未命名工作流")
    description = Column(Text, nullable=True)
    flow_json = Column(Text, nullable=False, default="{}")
    thumbnail_url = Column(Text, nullable=True)
    version = Column(String(32), nullable=False, default="1")
    revision = Column(Integer, nullable=False, default=1)
    node_count = Column(String(32), nullable=False, default="0")
    status = Column(String(16), nullable=False, default="draft")
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)
