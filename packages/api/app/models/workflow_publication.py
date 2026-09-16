"""工作流发布：作品广场条目、点赞与复制使用记录。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK, JsonCol


class WorkflowPublication(Base):
    """用户发布到作品广场的工作流条目（一项目一条）。"""

    __tablename__ = "workflow_publications"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    author_id = Column(BigIntFK, nullable=False, index=True)
    source_project_id = Column(BigIntFK, nullable=False, unique=True, index=True)
    title = Column(String(20), nullable=False, default="")
    description = Column(String(100), nullable=False, default="")
    category = Column(String(32), nullable=False, default="")
    # 源项目视频资产 id（发布时校验）；预览视频另拷到平台路径
    video_asset_id = Column(String(64), nullable=False, default="")
    video_oss_key = Column(Text, nullable=False, default="")
    # 封面图：源项目图片资产 id + 平台拷贝；广场默认展示，悬浮再播视频
    cover_asset_id = Column(String(64), nullable=True)
    cover_oss_key = Column(Text, nullable=True)
    # 发布时冻结的工作流快照（平台 OSS：flow.json）；复制/预览只读此快照，不跟随后续源项目改动
    flow_oss_key = Column(Text, nullable=False, default="")
    # 公开画布：true 表示用户申请公开；广场仅展示 is_public 且 review_status=approved
    is_public = Column(Boolean, nullable=False, default=False)
    # pending | approved | rejected
    review_status = Column(String(16), nullable=False, default="approved", index=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(BigIntFK, nullable=True)
    # 审核动作时间线：[{action, note, at, by, byName}, ...]
    review_history = Column(JsonCol, nullable=True)
    like_count = Column(Integer, nullable=False, default=0)
    use_count = Column(Integer, nullable=False, default=0)
    published_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)

    __table_args__ = (
        Index("ix_workflow_publications_public_published", "is_public", "published_at"),
        Index("ix_workflow_publications_public_review", "is_public", "review_status", "published_at"),
        Index("ix_workflow_publications_author_updated", "author_id", "updated_at"),
        Index("ix_workflow_publications_category", "category"),
    )


class WorkflowPublicationLike(Base):
    """发布条目点赞：每用户每条目最多一条。"""

    __tablename__ = "workflow_publication_likes"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    publication_id = Column(BigIntFK, nullable=False, index=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint("publication_id", "user_id", name="uq_workflow_pub_like_pub_user"),
    )


class WorkflowPublicationUse(Base):
    """复制使用记录：驱动「我的使用」列表。"""

    __tablename__ = "workflow_publication_uses"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    publication_id = Column(BigIntFK, nullable=False, index=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    target_project_id = Column(BigIntFK, nullable=False, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "publication_id",
            "user_id",
            "target_project_id",
            name="uq_workflow_pub_use_pub_user_project",
        ),
        Index("ix_workflow_publication_uses_user_created", "user_id", "created_at"),
    )
