"""敏感词表：MySQL 权威存储，生成校验经 Redis 单 key 热读。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, String, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK


class SensitiveWord(Base):
    """后台可增删的敏感词；``word_norm`` 用于去重与匹配。"""

    __tablename__ = "sensitive_words"
    __table_args__ = (
        UniqueConstraint("word_norm", name="uq_sensitive_words_word_norm"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    # 管理员录入的原文
    word = Column(String(128), nullable=False)
    # 规范化后的词（小写、去空白/零宽/标点），UNIQUE
    word_norm = Column(String(128), nullable=False, index=True)
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(BigIntFK, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)
