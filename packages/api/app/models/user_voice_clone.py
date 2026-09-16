"""用户百炼声音复刻音色：权威存 MySQL ``user_voice_clones``。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, UniqueConstraint

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK, JsonCol


class UserVoiceClone(Base):
    """用户通过百炼 CosyVoice create_voice 得到的复刻音色。"""

    __tablename__ = "user_voice_clones"
    __table_args__ = (
        UniqueConstraint("user_id", "voice_id", name="uq_user_voice_clones_user_voice"),
        Index("ix_user_voice_clones_user_created", "user_id", "created_at"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    # 百炼返回的 voice_id（合成时作为 voice 参数）
    voice_id = Column(String(128), nullable=False)
    # 用户可见展示名
    display_name = Column(String(64), nullable=False)
    # 合成目标模型，默认 cosyvoice-v3.5-plus
    target_model = Column(String(64), nullable=False, default="cosyvoice-v3.5-plus")
    # 参考音频 OSS key / URL（审计用）
    source_audio_url = Column(Text, nullable=True)
    source_asset_id = Column(String(64), nullable=True)
    language = Column(String(16), nullable=False, default="zh")
    gender = Column(String(8), nullable=True)
    favorite = Column(Boolean, nullable=False, default=False)
    extra = Column(JsonCol, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    updated_at = Column(DateTime, default=now_cst_naive, onupdate=now_cst_naive, nullable=False)
