"""平台 / 用户 Skill 目录与收藏。"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK, JsonCol


class Skill(Base):
    """Skill 一等公民：输入契约 + 声明式 pipeline（非纯 Prompt）。"""

    __tablename__ = "skills"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    slug = Column(String(64), nullable=False, unique=True, index=True)
    title = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(64), nullable=False, default="通用技能", index=True)
    # public | private | unlisted
    visibility = Column(String(16), nullable=False, default="public", index=True)
    # 平台 Skill 为空；用户自建二期
    owner_user_id = Column(BigIntFK, nullable=True, index=True)
    cover_url = Column(Text, nullable=True)
    # 前端适配入口：viral_remake | overseas_localize | product_cinematic_commercial | null（自由编排）
    entry_kind = Column(String(64), nullable=True)
    inputs = Column(JsonCol, nullable=True)
    pipeline = Column(JsonCol, nullable=True)
    # 我的 Skill / 平台可选：SKILL.md 正文（含 frontmatter）；用户另存时自动生成
    doc_markdown = Column(Text, nullable=True)
    # 多文件技能包（对齐平台 skill_docs：references/*.md 等）：{相对路径: markdown 正文}
    # 与 doc_markdown（作为包内 SKILL.md 主文件）合并后可整包注入 Agent 上下文
    package_files = Column(JsonCol, nullable=True)
    # 执行路径：team=沿用确定性模板投影（默认，兼容存量）；
    # canvas_manual=对齐平台技能包的精细画布操控（agent_followup JSON plan），需配合足够详实的文档/配方使用
    execution_mode = Column(String(16), nullable=False, default="team")
    # 技能自定义的画布规则覆盖（追加进 Agent 画布操控说明书附录，与通用规则冲突时以此为准）
    canvas_rules_markdown = Column(Text, nullable=True)
    default_style_id = Column(String(64), nullable=True)
    pricing_hint = Column(String(32), nullable=True, default="per_pipeline")
    version = Column(Integer, nullable=False, default=1)
    sort_order = Column(Integer, nullable=False, default=0)
    # draft | active | archived
    status = Column(String(16), nullable=False, default="active", index=True)
    # 社区发布审核：none | pending | approved | rejected（平台 Skill 用 none）
    review_status = Column(String(16), nullable=False, default="none", index=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(BigIntFK, nullable=True)
    # 审核动作时间线：[{action, note, at, by, byName}, ...]
    review_history = Column(JsonCol, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)


class SkillFavorite(Base):
    """用户收藏 Skill：每人每 Skill 一条。"""

    __tablename__ = "skill_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_id", name="uq_skill_favorites_user_skill"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    skill_id = Column(BigIntFK, nullable=False, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
