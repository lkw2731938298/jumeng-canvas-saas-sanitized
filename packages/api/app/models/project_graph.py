"""项目图（Project Graph）：Agent Team 共享权威中间态。"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK, JsonCol


class ProjectGraph(Base):
    """每个项目一份 Graph；revision 供前端 Projector 增量应用。"""

    __tablename__ = "project_graphs"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_graphs_project"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    project_id = Column(BigIntFK, nullable=False, unique=True, index=True)
    revision = Column(Integer, nullable=False, default=1)
    graph_json = Column(JsonCol, nullable=False, default=dict)
    # 待前端投影的画布操作（应用后可清空或随 revision 覆盖）
    canvas_ops = Column(JsonCol, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)
