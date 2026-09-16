"""短信验证码 —— 注册/登录/找回密码（验证码仅存 Redis，不落 MySQL）。"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .auth_request import client_ip
from .auth_volatile import volatile_delete, volatile_get, volatile_incr, volatile_set, volatile_ttl
from .platform_settings import is_registration_enabled as db_registration_enabled
from .redis_client import get_redis

logger = logging.getLogger(__name__)

USER_SMS_SCENES = frozenset({"user_register", "user_login", "password_reset", "admin_login"})
SCENE_KEY_SUFFIX = {
    "user_register": "register",
    "user_login": "login",
    "password_reset": "reset",
    "admin_login": "admin_login",
}


@dataclass(frozen=True)
class SmsCodeState:
    """Redis 中缓存的验证码状态。"""

    code: str
    expires_at: datetime
    created_at: datetime | None = None


def normalize_phone(raw: str) -> str:
    """归一化手机号为纯数字。"""
    return "".join(ch for ch in (raw or "") if ch.isdigit())


def mask_phone(phone: str) -> str:
    """脱敏手机号用于日志展示。"""
    if len(phone) == 11 and phone.isdigit():
        return f"{phone[:3]}****{phone[-4:]}"
    return phone or ""


def require_valid_phone(phone: str) -> str:
    """校验中国大陆 11 位手机号，非法则 fail。"""
    normalized = normalize_phone(phone)
    if len(normalized) != 11 or not normalized.startswith("1"):
        fail(ErrorCode.INVALID_PHONE)
    return normalized


def _sms_state_key(scene: str, phone: str) -> str:
    suffix = SCENE_KEY_SUFFIX.get(scene, scene)
    return f"sms:{suffix}:{phone}"


def _sms_fail_key(phone: str) -> str:
    return f"fail:sms:{phone}"


def _phone_lock_key(phone: str) -> str:
    return f"lock:sms:{phone}"


def _request_ip(request: Request | None) -> str:
    return client_ip(request)


def _parse_utc_timestamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


async def _require_redis_for_sms() -> None:
    if await get_redis() is None:
        fail(ErrorCode.SMS_REDIS_UNAVAILABLE)


async def clear_phone_sms_risk(phone: str) -> None:
    normalized = normalize_phone(phone)
    if not normalized:
        return
    await volatile_delete(
        _phone_lock_key(normalized),
        _sms_fail_key(normalized),
        f"rate:sms:{normalized}",
    )


async def assert_registration_enabled(db: AsyncSession | None = None) -> None:
    """校验平台是否开放注册，关闭则 fail。"""
    enabled = await db_registration_enabled(db) if db is not None else bool(get_settings().registration_enabled)
    if not enabled:
        fail(ErrorCode.REGISTRATION_DISABLED)


def _sms_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.alibaba_cloud_access_key_id
        and settings.alibaba_cloud_access_key_secret
        and settings.sms_sign_name
        and settings.sms_template_code
    )


def _send_sms_via_alibaba(phone: str, code: str) -> None:
    settings = get_settings()
    try:
        from alibabacloud_dysmsapi20170525 import models as sms_models
        from alibabacloud_dysmsapi20170525.client import Client as SmsClient
        from alibabacloud_tea_openapi import models as open_api_models
    except ImportError as exc:
        raise RuntimeError("短信 SDK 未安装，请配置 SMS 调试模式或安装 alibabacloud_dysmsapi20170525") from exc

    config = open_api_models.Config(
        access_key_id=settings.alibaba_cloud_access_key_id,
        access_key_secret=settings.alibaba_cloud_access_key_secret,
        region_id=settings.alibaba_cloud_region_id or "cn-hangzhou",
    )
    client = SmsClient(config)
    req = sms_models.SendSmsRequest(
        phone_numbers=phone,
        sign_name=settings.sms_sign_name,
        template_code=settings.sms_template_code,
        template_param=json.dumps({settings.sms_template_param_key: code}, ensure_ascii=False),
    )
    resp = client.send_sms(req)
    body = getattr(resp, "body", None)
    sms_code = str(getattr(body, "code", "") or "")
    if sms_code != "OK":
        message = str(getattr(body, "message", "") or "")
        raise RuntimeError(message or sms_code or "短信发送失败")


def _sms_phone_rate_key(phone: str) -> str:
    return f"rate:sms:{phone}"


def _is_sms_provider_rate_limited(message: str) -> bool:
    """识别阿里云短信分钟/日流控等限流文案。"""
    text = str(message or "")
    needles = ("流控", "Permits", "FREQUENCY", "限流", "频繁", "BUSINESS_LIMIT")
    return any(n in text for n in needles)


async def _deliver_sms_code(phone: str, code: str) -> bool:
    settings = get_settings()
    if settings.sms_force_local_debug or not _sms_configured():
        logger.info("[sms] debug mode code for %s: %s", mask_phone(phone), code)
        return False
    try:
        await asyncio.to_thread(_send_sms_via_alibaba, phone, code)
        return True
    except Exception as err:
        if settings.sms_fallback_on_provider_error:
            logger.warning("[sms] provider failed, fallback to debug: %s", err)
            return False
        raise


async def check_sms_send_limits(phone: str, request: Request | None) -> None:
    """发送前校验：手机号锁定、冷却、日配额等限流规则。"""
    settings = get_settings()
    locked_seconds = await volatile_ttl(_phone_lock_key(phone))
    if locked_seconds > 0:
        fail(
            ErrorCode.PHONE_LOCKED,
            content={"waitSeconds": locked_seconds},
            http_status=423,
        )
    phone_rate_key = _sms_phone_rate_key(phone)
    wait_seconds = await volatile_ttl(phone_rate_key)
    if wait_seconds > 0:
        fail(
            ErrorCode.SMS_COOLDOWN,
            content={"waitSeconds": wait_seconds},
        )
    await volatile_set(phone_rate_key, "1", max(1, settings.sms_send_cooldown_seconds))
    ip = _request_ip(request) or "unknown"
    ip_hits = await volatile_incr(f"rate:sms:ip:{ip}", 60)
    if ip_hits > 10:
        fail(ErrorCode.IP_RATE_LIMITED)


def _parse_sms_code_state(cached: object) -> SmsCodeState | None:
    if not isinstance(cached, dict):
        return None
    code = str(cached.get("code") or "").strip()
    expires_at = _parse_utc_timestamp(cached.get("expires_at"))
    if not code or expires_at is None:
        return None
    return SmsCodeState(
        code=code,
        expires_at=expires_at,
        created_at=_parse_utc_timestamp(cached.get("created_at")),
    )


async def _read_sms_code(phone: str, scene: str) -> tuple[SmsCodeState | None, bool]:
    """Return (state, was_expired). Expired or corrupt keys are deleted from Redis."""
    key = _sms_state_key(scene, phone)
    cached = await volatile_get(key)
    state = _parse_sms_code_state(cached)
    if state is None:
        if cached is not None:
            await volatile_delete(key)
        return None, False
    if state.expires_at < datetime.now(timezone.utc):
        await volatile_delete(key)
        logger.info("[sms] expired code removed for %s scene=%s", mask_phone(phone), scene)
        return None, True
    return state, False


async def send_sms_code(
    db: AsyncSession,
    *,
    phone: str,
    scene: str,
    request: Request | None = None,
) -> dict:
    """发送短信验证码：写 Redis、调阿里云（或开发环境返回 debug code）。"""
    settings = get_settings()
    normalized = require_valid_phone(phone)
    if scene not in USER_SMS_SCENES:
        fail(ErrorCode.INVALID_SMS_SCENE)

    if scene == "user_register":
        await assert_registration_enabled(db)

    await _require_redis_for_sms()
    await check_sms_send_limits(normalized, request)

    code = f"{random.randint(0, 999999):06d}"
    now = datetime.now(timezone.utc)
    ttl_seconds = max(int(settings.sms_code_ttl_seconds), 60)
    expires_at = now + timedelta(seconds=ttl_seconds)
    try:
        delivered = await _deliver_sms_code(normalized, code)
    except Exception as err:
        # 上游失败时释放本机冷却，避免「没发出去却锁 60 秒」；限流返回明确 429
        await volatile_delete(_sms_phone_rate_key(normalized))
        msg = str(err).strip() or "短信发送失败"
        logger.warning("[sms] provider send failed phone=%s: %s", mask_phone(normalized), msg)
        if _is_sms_provider_rate_limited(msg):
            fail(
                ErrorCode.SMS_COOLDOWN,
                message="短信通道繁忙（运营商分钟级限流），请约 1 分钟后再试",
                content={"waitSeconds": 60},
            )
        fail(ErrorCode.SMS_SEND_FAILED, message=msg)

    await volatile_set(
        _sms_state_key(scene, normalized),
        {
            "code": code,
            "expires_at": expires_at.timestamp(),
            "created_at": now.timestamp(),
        },
        ttl_seconds,
    )

    return_code = ""
    if settings.sms_debug_return_code or not delivered:
        return_code = code

    return {
        "ok": True,
        "maskedPhone": mask_phone(normalized),
        "code": return_code,
        "scene": scene,
    }


async def verify_sms_code(db: AsyncSession, *, phone: str, scene: str, code: str) -> None:
    """校验短信验证码：失败计数、过期、错误码统一返回。"""
    _ = db  # 保留参数以兼容 API；验证码仅存 Redis
    normalized = require_valid_phone(phone)
    submitted = (code or "").strip()
    if not submitted:
        fail(ErrorCode.INVALID_CODE, message="验证码不能为空")

    await _require_redis_for_sms()

    lock_key = _phone_lock_key(normalized)
    locked_seconds = await volatile_ttl(lock_key)
    if locked_seconds > 0:
        fail(
            ErrorCode.SMS_LOCKED,
            content={"waitSeconds": locked_seconds},
            http_status=423,
        )

    row, was_expired = await _read_sms_code(normalized, scene)
    if was_expired:
        fail(ErrorCode.CODE_EXPIRED)

    expected = row.code if row else ""
    code_key = _sms_state_key(scene, normalized)

    if not row or expected != submitted:
        fails = await volatile_incr(_sms_fail_key(normalized), 1800)
        if fails >= 5:
            await volatile_set(lock_key, "1", 1800)
            await volatile_delete(_sms_fail_key(normalized))
            fail(
                ErrorCode.SMS_LOCKED,
                content={"waitSeconds": 1800},
                http_status=423,
            )
        fail(
            ErrorCode.INVALID_CODE,
            content={"remainingAttempts": max(0, 5 - fails)},
            http_status=401,
        )

    await volatile_delete(code_key, _sms_fail_key(normalized))
