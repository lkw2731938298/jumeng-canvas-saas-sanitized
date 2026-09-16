from ..core.datetime_util import now_cst_naive
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Text, Integer, BigInteger, Numeric
from .types import BigIntPK, BigIntFK, JsonCol
from .database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    source_user_id = Column(String(128), unique=True, nullable=False, index=True)
    phone = Column(String(20), nullable=True)
    password_hash = Column(String(255), nullable=True)
    display_name = Column(String(128), nullable=True, default="")
    avatar_url = Column(Text, nullable=True)
    role = Column(String(16), nullable=False, default="user")
    # 超级管理员：拥有全部后台权限，且可授权；由 SUPER_ADMIN_PHONES 引导，不可经普通接口授予
    is_super_admin = Column(Boolean, nullable=False, default=False)
    # 普通管理员功能权限键列表（JSON）；NULL 表示历史兼容默认权限（不含权限管理）
    admin_permissions = Column(JsonCol, nullable=True)
    # 算力缓存：支持一位小数（与 credit_lots 对齐）
    compute_power = Column(Numeric(14, 1), nullable=False, default=0)
    # 用户名下全部项目 OSS 素材占用缓存（字节），权威来源为 project_assets.file_size 汇总
    storage_used_bytes = Column(BigInteger, nullable=False, default=0)
    credit_consume_priority = Column(JsonCol, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    # 用户专属 6 位邀请码（懒生成）；UNIQUE
    invite_code = Column(String(6), nullable=True, unique=True, index=True)
    # 注册时绑定的邀请人（不可改）
    referred_by_user_id = Column(BigIntFK, nullable=True, index=True)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
    last_login_at = Column(DateTime, default=now_cst_naive, nullable=True)
    last_login_ip = Column(String(64), nullable=False, default="")

class UserSession(Base):
    __tablename__ = "user_sessions"
    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_id = Column(BigIntFK, nullable=False, index=True)
    token = Column(String(512), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=now_cst_naive, nullable=False)
