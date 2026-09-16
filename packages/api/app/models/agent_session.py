"""Agent Session：站内创作框 / 后续 OpenAPI 共用的 IM 会话。"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, Numeric

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK, JsonCol


class AgentSession(Base):
    """一轮创作对话，绑定项目与可选 Skill。"""

    __tablename__ = "agent_sessions"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    project_id = Column(BigIntFK, nullable=False, index=True)
    skill_id = Column(BigIntFK, nullable=True, index=True)
    # 创作框风格 Chip → visual_style.id；写入 Project Graph.style.styleId
    style_id = Column(String(64), nullable=True)
    # 轨 S 编排算力（预扣/结算）：支持一位小数
    credit_cost = Column(Numeric(14, 1), nullable=False, default=0)
    credit_reservation_id = Column(String(128), nullable=True)
    credit_status = Column(String(32), nullable=True)
    pricing_version = Column(Integer, nullable=True)
    credit_breakdown = Column(JsonCol, nullable=True)
    # 澄清反问摘要：asked / draftStory / idea
    brief_json = Column(JsonCol, nullable=True)
    # active | awaiting_user | completed | failed | cancelled
    status = Column(String(16), nullable=False, default="active", index=True)
    title = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)


class AgentSessionMessage(Base):
    """会话消息（增量 seq 供轮询）。"""

    __tablename__ = "agent_session_messages"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    session_id = Column(BigIntFK, nullable=False, index=True)
    seq = Column(Integer, nullable=False, index=True)
    # user | assistant | agent | system
    role = Column(String(16), nullable=False)
    agent_role = Column(String(64), nullable=True)
    content = Column(Text, nullable=False, default="")
    skill_id = Column(BigIntFK, nullable=True)
    graph_patch = Column(JsonCol, nullable=True)
    job_ids = Column(JsonCol, nullable=True)
    artifacts = Column(JsonCol, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
