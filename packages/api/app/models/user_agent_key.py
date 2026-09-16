"""用户 Agent Access Key（外部 OpenAPI 鉴权）。"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, String, Text

from ..core.datetime_util import now_cst_naive
from .database import Base
from .types import BigIntFK, BigIntPK


class UserAgentKey(Base):
    """Access Key 仅存哈希；明文只在创建时返回一次。"""

    __tablename__ = "user_agent_keys"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    # 展示用前缀，如 jm_ak_ab12…
    key_prefix = Column(String(24), nullable=False, index=True)
    # sha256(hex) of full key
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(64), nullable=False, default="default")
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    note = Column(Text, nullable=True)
