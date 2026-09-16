"""登录失败计数与临时锁定（基于 Redis volatile 存储）。"""

from __future__ import annotations

from ..core.config import get_settings
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .auth_volatile import volatile_delete, volatile_get, volatile_incr, volatile_set, volatile_ttl


def _phone_key(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def _fail_key(phone: str) -> str:
    return f"auth:fail:login:{_phone_key(phone)}"


def _lock_key(phone: str) -> str:
    return f"auth:lock:login:{_phone_key(phone)}"


async def assert_login_not_locked(phone: str) -> None:
    """校验手机号未被临时登录锁定，否则抛出 423。"""
    normalized = _phone_key(phone)
    if not normalized:
        return
    locked_seconds = await volatile_ttl(_lock_key(normalized))
    if locked_seconds > 0:
        fail(
            ErrorCode.LOGIN_LOCKED,
            content={"waitSeconds": locked_seconds},
            http_status=423,
        )


async def record_login_failure(phone: str) -> None:
    """累加登录失败次数，达阈值则锁定并抛出对应错误。"""
    settings = get_settings()
    normalized = _phone_key(phone)
    if not normalized:
        return
    fail_key = _fail_key(normalized)
    lock_key = _lock_key(normalized)
    fails = await volatile_incr(fail_key, 600)
    max_failures = max(int(settings.login_max_failures or 6), 1)
    lock_seconds = max(int(settings.login_lock_seconds or 1200), 60)
    if fails >= max_failures:
        await volatile_set(lock_key, "1", lock_seconds)
        await volatile_delete(fail_key)
        fail(
            ErrorCode.LOGIN_LOCKED,
            content={"waitSeconds": lock_seconds},
            http_status=423,
        )
    fail(
        ErrorCode.INVALID_PASSWORD,
        content={"failCount": fails},
        http_status=401,
    )


async def get_login_fail_count(phone: str) -> int:
    """读取当前手机号密码登录失败次数（不递增）。"""
    normalized = _phone_key(phone)
    if not normalized:
        return 0
    raw = await volatile_get(_fail_key(normalized))
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


async def captcha_required_for_password_login(phone: str) -> bool:
    """密码登录是否已达到需滑动验证码的失败阈值。"""
    settings = get_settings()
    if not settings.captcha_enabled:
        return False
    threshold = max(int(settings.captcha_password_after_failures or 2), 1)
    return await get_login_fail_count(phone) >= threshold


async def clear_login_failures(phone: str) -> None:
    """登录成功后清除该手机号的失败计数与锁定。"""
    normalized = _phone_key(phone)
    if not normalized:
        return
    await volatile_delete(_fail_key(normalized), _lock_key(normalized))


async def clear_all_phone_auth_risk(phone: str) -> None:
    """清除手机号相关的登录与短信风控状态。"""
    from .auth_sms import clear_phone_sms_risk

    await clear_login_failures(phone)
    await clear_phone_sms_risk(phone)
