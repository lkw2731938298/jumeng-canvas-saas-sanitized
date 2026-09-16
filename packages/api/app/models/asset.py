from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from .types import BigIntPK, BigIntFK
from .database import Base


class ProjectAsset(Base):
    __tablename__ = "project_assets"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    project_id = Column(BigIntFK, nullable=False, index=True)
    title = Column(String(256), nullable=False, default="未命名")
    category = Column(String(16), nullable=False, index=True)
    subcategory = Column(String(64), nullable=True)
    file_type = Column(String(128), nullable=False, default="application/octet-stream")
    file_size = Column(Integer, nullable=False, default=0)
    oss_key = Column(Text, nullable=False)
    thumbnail_url = Column(Text, nullable=True)
    source = Column(String(16), nullable=False, default="upload")
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)

    __table_args__ = (
        Index("ix_project_assets_project_category_created", "project_id", "category", "created_at"),
    )
