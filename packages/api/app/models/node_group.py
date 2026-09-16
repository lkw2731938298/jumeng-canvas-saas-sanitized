"""画布节点组库：元数据存 MySQL，快照 JSON 存 OSS。"""

from ..core.datetime_util import now_cst_naive
from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from .types import BigIntPK, BigIntFK, JsonCol
from .database import Base


class CanvasNodeGroup(Base):
    __tablename__ = "canvas_node_groups"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    project_id = Column(BigIntFK, nullable=False, index=True)
    owner_id = Column(BigIntFK, nullable=False, index=True)
    title = Column(String(256), nullable=False, default="未命名组")
    cover_asset_id = Column(BigIntFK, nullable=True)
    oss_key = Column(Text, nullable=False, default="")
    # 组引用的项目素材 id 列表（字符串），便于列表展示缩略图
    asset_ids = Column(JsonCol, nullable=False, default=list)
    # 用户标签，如 ["角色","常用"]
    tags = Column(JsonCol, nullable=False, default=list)
    node_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)

    __table_args__ = (
        Index("ix_canvas_node_groups_project_updated", "project_id", "updated_at"),
    )
