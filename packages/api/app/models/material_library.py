"""平台素材库：风格库 / 特效库 / 角色库 / 提示词库（仅管理员上传，用户只读选用）。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK


# 四类库类别常量（prompt=提示词库：媒体预览 + 提示词正文，画布落锁定文本节点）
MATERIAL_LIBRARY_CATEGORIES = ("style", "effect", "character", "prompt")

# 提示词正文上限（管理端文本框 + 画布文本节点 content）
PROMPT_LIBRARY_TEXT_MAX_LEN = 8000

# 提示词库二级分类名称上限
PROMPT_LIBRARY_CATEGORY_NAME_MAX_LEN = 32


class MaterialLibraryPromptCategory(Base):
    """提示词库二级分类：后台可增删改，用户侧按分类筛选条目。"""

    __tablename__ = "material_library_prompt_categories"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    name = Column(String(32), nullable=False, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_material_library_prompt_cat_name"),
        Index("ix_material_library_prompt_cat_active_sort", "is_active", "sort_order"),
    )


class MaterialLibraryItem(Base):
    """素材库条目：元数据存 MySQL，媒体文件存平台 OSS。"""

    __tablename__ = "material_library_items"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    # style=风格库(图) / effect=特效库(视频或动图) / character=角色库(图) / prompt=提示词库(图或视频+正文)
    category = Column(String(32), nullable=False, index=True)
    title = Column(String(128), nullable=False, default="")
    # image | video
    media_type = Column(String(16), nullable=False, default="image")
    oss_key = Column(Text, nullable=False, default="")
    # 提示词库专用：落画布文本节点的正文；其它类别为空
    prompt_text = Column(Text, nullable=False, default="")
    # 提示词库二级分类；其它库类别为空；分类删除后置空（未分类）
    prompt_category_id = Column(BigIntFK, nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(BigIntFK, nullable=True, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)

    __table_args__ = (
        Index("ix_material_library_cat_active_sort", "category", "is_active", "sort_order"),
    )
