from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from .types import BigIntPK, BigIntFK, JsonCol
from .database import Base


class AuthEvent(Base):
    __tablename__ = "auth_events"

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=True, index=True)
    phone = Column(String(20), nullable=True, index=True)
    action = Column(String(32), nullable=False, index=True)
    result = Column(String(16), nullable=False, index=True)
    ip = Column(String(64), nullable=False, default="")
    user_agent = Column(Text, nullable=True)
    detail = Column(JsonCol, nullable=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False, index=True)
