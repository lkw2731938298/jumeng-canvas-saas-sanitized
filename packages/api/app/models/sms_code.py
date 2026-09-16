from ..core.datetime_util import now_cst_naive
"""Legacy ORM for sms_login_codes (deprecated).

SMS verification codes are stored in Redis only; see app.services.auth_sms.
The table may remain for historical migrations but is no longer written at runtime.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, String

from .database import Base


class SmsLoginCode(Base):
    __tablename__ = "sms_login_codes"

    phone = Column(String(11), primary_key=True)
    scene = Column(String(32), primary_key=True)
    code = Column(String(6), nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_cst_naive, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
