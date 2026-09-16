"""异地登录识别：基于历史成功登录 IP 判定是否需要短信二次验证。

判定权威来源为 MySQL（用户最近登录 IP + auth_events 历史成功登录 IP），
不依赖 Redis，避免缓存不可用时把正常用户误判为异地并锁死登录。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..models.auth_event import AuthEvent
from ..models.user import User

# 视为“已知设备/位置”的成功认证行为（注册也算，避免注册设备首次登录被拦）
_KNOWN_LOGIN_ACTIONS = (
    "login_password",
    "login_sms",
    "login_admin_password",
    "login_sso",
    "register",
)


def login_new_ip_sms_required() -> bool:
    """全局开关：异地登录是否要求短信验证码。"""
    return bool(get_settings().login_new_ip_sms_required)


def _normalize_ip(ip: str | None) -> str:
    return (ip or "").strip()


async def is_new_location_login(db: AsyncSession, user: User, ip: str) -> bool:
    """判断本次登录 IP 是否为陌生 IP（此前无成功登录记录）。

    - 无法解析客户端 IP 时按“非异地”处理，避免误锁正常用户；
    - 与最近一次成功登录 IP 相同即视为常用地；
    - 否则查询 auth_events 是否存在同 IP 的历史成功登录（含注册）。
    """
    current = _normalize_ip(ip)
    if not current:
        return False
    if _normalize_ip(user.last_login_ip) == current:
        return False
    stmt = (
        select(AuthEvent.id)
        .where(
            AuthEvent.user_id == user.id,
            AuthEvent.result == "success",
            AuthEvent.action.in_(_KNOWN_LOGIN_ACTIONS),
            AuthEvent.ip == current,
        )
        .limit(1)
    )
    found = (await db.execute(stmt)).first()
    return found is None
