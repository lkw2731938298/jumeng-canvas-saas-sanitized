"""用户认证接口：注册/登录（密码、短信验证码、SSO）、找回密码、会话签发与登出。"""

from ...core.datetime_util import now_cst_naive
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...core.deps import _extract_bearer_token, get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...services.credit_flow import credits_enabled, get_user_credit_balance
from ...core.security import (
    hash_password,
    verify_password,
    verify_sso_token,
)
from ...models.database import get_db
from ...models.user import User, UserSession
from ...schemas.api import (
    AdminLoginRequest,
    AuthConfigOut,
    CaptchaChallengeOut,
    CaptchaStatusOut,
    CaptchaVerifyOut,
    CaptchaVerifyRequest,
    LoginRequest,
    LoginSmsRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SmsCodeSendRequest,
    SmsCodeSendResponse,
    SsoLoginRequest,
    SsoLoginResponse,
    UserOut,
)
from ...services.auth_audit import log_auth_event
from ...services.auth_captcha import (
    captcha_enabled,
    consume_captcha_ticket,
    create_captcha_challenge,
    verify_captcha_and_issue_ticket,
)
from ...services.auth_request import client_ip, client_user_agent
from ...services.auth_login_ip import (
    is_new_location_login,
    login_new_ip_sms_required,
)
from ...services.auth_risk import (
    assert_login_not_locked,
    captcha_required_for_password_login,
    clear_login_failures,
    get_login_fail_count,
    record_login_failure,
)
from ...services.auth_session import create_user_session, revoke_session
from ...services.auth_sms import (
    assert_registration_enabled,
    require_valid_phone,
    send_sms_code,
    verify_sms_code,
)
from ...services.password_policy import require_strong_password
from ...services.platform_settings import is_registration_enabled
from ...core.entity_ids import format_user_display_id

router = APIRouter()
settings = get_settings()

_PHONE_RE = re.compile(r"^1\d{10}$")


def _validate_phone(phone: str) -> str:
    cleaned = phone.strip()
    if not _PHONE_RE.match(cleaned):
        fail(ErrorCode.INVALID_PHONE)
    return cleaned


def _validate_password_present(password: str) -> str:
    if len(password) < 6:
        fail(ErrorCode.PASSWORD_TOO_SHORT)
    return password


async def _user_out(user: User, db: AsyncSession | None = None) -> UserOut:
    from ...core.credit_amount import normalize_credit_amount
    from ...services.storage_urls import normalize_browser_storage_url

    enabled = credits_enabled()
    balance = await get_user_credit_balance(user, db) if enabled else None
    avatar = user.avatar_url
    if avatar:
        # 本站 OSS 头像按当前模式重签；外链头像原样
        avatar = normalize_browser_storage_url(avatar) or avatar
    return UserOut(
        id=str(user.id),
        user_no=format_user_display_id(user.id),
        phone=user.phone,
        display_name=user.display_name or "",
        avatar_url=avatar,
        # 算力已支持一位小数；未启用时不返回余额
        compute_power=normalize_credit_amount(balance) if balance is not None else None,
        credits_enabled=enabled,
    )


async def _issue_session(
    db: AsyncSession,
    user: User,
    *,
    request: Request | None = None,
) -> SsoLoginResponse:
    user.last_login_at = now_cst_naive()
    if request is not None:
        user.last_login_ip = client_ip(request)
    session_token = await create_user_session(db, user)
    return SsoLoginResponse(session_token=session_token, user=await _user_out(user, db))


async def _get_user_by_phone(db: AsyncSession, phone: str) -> User | None:
    result = await db.execute(select(User).filter(User.phone == phone))
    return result.scalar_one_or_none()


def _assert_active_admin(user: User | None) -> User:
    """校验账号存在、未禁用且具备管理员角色。"""
    if not user or user.role != "admin":
        fail(ErrorCode.ADMIN_REQUIRED)
    if user.is_active is False:
        fail(ErrorCode.ACCOUNT_DISABLED)
    return user


@router.get("/config", response_model=AuthConfigOut)
async def get_auth_config(db: AsyncSession = Depends(get_db)):
    """获取认证相关配置（注册开关、滑动验证码策略）。"""
    return AuthConfigOut(
        registration_enabled=await is_registration_enabled(db),
        captcha_enabled=captcha_enabled(),
        captcha_password_after_failures=max(int(settings.captcha_password_after_failures or 2), 1),
    )


@router.get("/captcha", response_model=CaptchaChallengeOut)
async def get_captcha():
    """获取滑动拼图验证码挑战。"""
    if not captcha_enabled():
        # 关闭时仍返回空壳，前端可跳过弹窗
        return CaptchaChallengeOut(captchaId="", expiresIn=0, answer="")
    payload = await create_captcha_challenge()
    return CaptchaChallengeOut(**payload)


@router.post("/captcha/verify", response_model=CaptchaVerifyOut)
async def verify_captcha(req: CaptchaVerifyRequest, request: Request):
    """校验滑动拼图并签发一次性 ticket（须绑定有效手机号与场景）。"""
    if not captcha_enabled():
        # 关闭时签发占位 ticket 无意义；前端应跳过校验
        fail(ErrorCode.CAPTCHA_REQUIRED, message="滑动验证码未启用")
    phone = require_valid_phone(req.phone)
    payload = await verify_captcha_and_issue_ticket(
        captcha_id=req.captcha_id,
        slide_x=req.slide_x,
        captcha_code=req.captcha_code,
        phone=phone,
        scene=(req.scene or "user_login").strip() or "user_login",
        request=request,
    )
    return CaptchaVerifyOut(**payload)


@router.get("/captcha/status", response_model=CaptchaStatusOut)
async def get_captcha_status(phone: str = ""):
    """查询密码登录是否已需滑动验证码。"""
    normalized = "".join(ch for ch in (phone or "") if ch.isdigit())
    threshold = max(int(settings.captcha_password_after_failures or 2), 1)
    fail_count = await get_login_fail_count(normalized) if normalized else 0
    required = captcha_enabled() and fail_count >= threshold
    return CaptchaStatusOut(
        captchaEnabled=captcha_enabled(),
        requiredForPasswordLogin=required,
        failCount=fail_count,
        threshold=threshold,
    )


@router.post("/send-code", response_model=SmsCodeSendResponse)
async def send_code(
    req: SmsCodeSendRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """按场景（注册/登录/找回密码/管理登录）校验手机号后发送短信验证码。"""
    phone = require_valid_phone(req.phone)
    scene = (req.scene or "user_login").strip() or "user_login"

    if scene == "user_register":
        await assert_registration_enabled(db)
        existing = await _get_user_by_phone(db, phone)
        if existing:
            fail(ErrorCode.PHONE_ALREADY_REGISTERED)
    elif scene == "password_reset":
        existing = await _get_user_by_phone(db, phone)
        if not existing:
            fail(ErrorCode.PHONE_NOT_REGISTERED)
    elif scene == "user_login":
        existing = await _get_user_by_phone(db, phone)
        if not existing:
            fail(ErrorCode.USER_NOT_REGISTERED)
    elif scene == "admin_login":
        # 管理后台验证码：仅向已绑定管理员手机号发送
        _assert_active_admin(await _get_user_by_phone(db, phone))
    else:
        fail(ErrorCode.INVALID_SMS_SCENE)

    # 发短信前必须消费滑动验证码票据（防刷短信）
    await consume_captcha_ticket(
        ticket=req.captcha_ticket,
        phone=phone,
        scene=scene,
        required=True,
    )

    payload = await send_sms_code(db, phone=phone, scene=scene, request=request)
    return SmsCodeSendResponse(**payload)


@router.post("/register", response_model=SsoLoginResponse)
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """手机号注册新用户：校验验证码与强密码，创建账号并签发会话。"""
    await assert_registration_enabled(db)
    phone = _validate_phone(req.phone)
    require_strong_password(req.password)

    if settings.sms_registration_required:
        if not (req.code or "").strip():
            fail(ErrorCode.SMS_CODE_REQUIRED)
        await verify_sms_code(db, phone=phone, scene="user_register", code=req.code.strip())

    existing = await db.execute(select(User).filter(User.phone == phone))
    if existing.scalar_one_or_none():
        fail(ErrorCode.PHONE_ALREADY_REGISTERED, http_status=400)

    # 邀请码：建用户前校验，避免无效码产生脏账号
    from ...services.invite_referral import (
        apply_invite_on_register,
        ensure_user_invite_code,
        normalize_invite_code,
        resolve_inviter_by_code,
    )

    invite_code = normalize_invite_code(getattr(req, "invite_code", None))
    if invite_code:
        await resolve_inviter_by_code(db, invite_code)

    user = User(
        source_user_id="pending",
        phone=phone,
        password_hash=hash_password(req.password),
        display_name=(req.display_name or "").strip() or f"用户{phone[-4:]}",
        compute_power=max(settings.default_user_credits, 0),
    )
    db.add(user)
    await db.flush()
    user.source_user_id = str(user.id)
    await db.flush()

    # 为新用户预生成专属邀请码（失败不阻断注册）
    try:
        await ensure_user_invite_code(db, user)
    except Exception:
        pass

    # 归因 + 发奖；Redis 不可用等 AppError 向上抛（fail-closed 防双发）
    invite_reward: dict | None = None
    if invite_code:
        invite_reward = await apply_invite_on_register(db, invitee=user, invite_code=invite_code)

    # 首次注册赠送：每人终身一次（Redis + UNIQUE + 行锁）
    from ...services.register_bonus import try_grant_register_bonus

    register_bonus = await try_grant_register_bonus(db, user)

    await log_auth_event(
        db,
        action="register",
        result="success",
        phone=phone,
        user_id=user.id,
        ip=client_ip(request),
        user_agent=client_user_agent(request),
    )
    session = await _issue_session(db, user, request=request)
    if invite_reward and invite_reward.get("bound"):
        session.invite_reward = {
            "inviteeAmount": int(invite_reward.get("inviteeRewardAmount") or 0),
            "inviterPending": bool(invite_reward.get("inviterPending")),
        }
    if register_bonus and register_bonus.get("granted"):
        session.register_bonus = {
            "amount": int(register_bonus.get("amount") or 0),
        }
    return session


@router.post("/login", response_model=SsoLoginResponse)
async def login(req: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """手机号+密码登录：校验凭证与失败锁定，成功后签发会话。"""
    phone = _validate_phone(req.phone)
    _validate_password_present(req.password)
    await assert_login_not_locked(phone)

    # 失败次数达阈值后须先通过滑动验证码
    if await captcha_required_for_password_login(phone):
        await consume_captcha_ticket(
            ticket=req.captcha_ticket,
            phone=phone,
            scene="user_login",
            required=True,
        )

    user = await _get_user_by_phone(db, phone)
    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        await log_auth_event(
            db,
            action="login_password",
            result="failure",
            phone=phone,
            user_id=user.id if user else None,
            ip=client_ip(request),
            user_agent=client_user_agent(request),
        )
        await record_login_failure(phone)
        fail(ErrorCode.INVALID_PASSWORD)
    if user.is_active is False:
        fail(ErrorCode.ACCOUNT_DISABLED)

    # 密码正确即清除失败计数，异地二次校验前不再要求滑动验证码
    await clear_login_failures(phone)

    # 异地登录二次校验：陌生 IP 需短信验证码（判定基于历史成功登录 IP）
    if login_new_ip_sms_required() and await is_new_location_login(db, user, client_ip(request)):
        submitted_code = (req.sms_code or "").strip()
        if not submitted_code:
            await log_auth_event(
                db,
                action="login_password",
                result="challenge",
                phone=phone,
                user_id=user.id,
                ip=client_ip(request),
                user_agent=client_user_agent(request),
                detail={"reason": "new_ip_sms_required"},
            )
            fail(ErrorCode.LOGIN_SMS_REQUIRED)
        await verify_sms_code(db, phone=phone, scene="user_login", code=submitted_code)

    await log_auth_event(
        db,
        action="login_password",
        result="success",
        phone=phone,
        user_id=user.id,
        ip=client_ip(request),
        user_agent=client_user_agent(request),
    )
    return await _issue_session(db, user, request=request)


@router.post("/login-sms", response_model=SsoLoginResponse)
async def login_sms(req: LoginSmsRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """手机号+短信验证码登录：校验验证码后签发会话。"""
    phone = require_valid_phone(req.phone)
    await verify_sms_code(db, phone=phone, scene="user_login", code=req.code.strip())

    user = await _get_user_by_phone(db, phone)
    if not user:
        await log_auth_event(
            db,
            action="login_sms",
            result="failure",
            phone=phone,
            ip=client_ip(request),
            user_agent=client_user_agent(request),
            detail={"reason": "user_not_found"},
        )
        fail(ErrorCode.USER_NOT_REGISTERED)
    if user.is_active is False:
        fail(ErrorCode.ACCOUNT_DISABLED)

    await clear_login_failures(phone)
    await log_auth_event(
        db,
        action="login_sms",
        result="success",
        phone=phone,
        user_id=user.id,
        ip=client_ip(request),
        user_agent=client_user_agent(request),
    )
    return await _issue_session(db, user, request=request)


@router.post("/admin-login", response_model=SsoLoginResponse)
async def admin_login(req: AdminLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """管理后台登录：手机号 + 密码 + 短信验证码，须具备管理员角色。"""
    phone = require_valid_phone(req.phone)
    password = (req.password or "").strip()
    sms_code = (req.sms_code or req.code or "").strip()
    if not password:
        fail(ErrorCode.PASSWORD_TOO_SHORT)
    if not sms_code:
        fail(ErrorCode.SMS_CODE_REQUIRED)

    await assert_login_not_locked(phone)
    user = await _get_user_by_phone(db, phone)
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        await log_auth_event(
            db,
            action="login_admin_password",
            result="failure",
            phone=phone,
            user_id=user.id if user else None,
            ip=client_ip(request),
            user_agent=client_user_agent(request),
        )
        await record_login_failure(phone)
        fail(ErrorCode.INVALID_PASSWORD)

    user = _assert_active_admin(user)
    await verify_sms_code(db, phone=phone, scene="admin_login", code=sms_code)

    await clear_login_failures(phone)
    await log_auth_event(
        db,
        action="login_admin_password",
        result="success",
        phone=phone,
        user_id=user.id,
        ip=client_ip(request),
        user_agent=client_user_agent(request),
    )
    return await _issue_session(db, user, request=request)


@router.post("/reset-password", response_model=SsoLoginResponse)
async def reset_password(req: ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """通过短信验证码找回密码：重置密码、失效旧会话并签发新会话。"""
    phone = require_valid_phone(req.phone)
    require_strong_password(req.password)
    await verify_sms_code(db, phone=phone, scene="password_reset", code=req.code.strip())

    user = await _get_user_by_phone(db, phone)
    if not user:
        fail(ErrorCode.USER_NOT_FOUND)

    user.password_hash = hash_password(req.password)
    await db.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await clear_login_failures(phone)
    await db.flush()

    await log_auth_event(
        db,
        action="reset_password",
        result="success",
        phone=phone,
        user_id=user.id,
        ip=client_ip(request),
        user_agent=client_user_agent(request),
    )
    return await _issue_session(db, user, request=request)


@router.post("/logout")
async def logout(
    request: Request,
    authorization: str = Header(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """登出当前用户：撤销会话 token 并记录审计事件。"""
    token = _extract_bearer_token(authorization)
    if token:
        await revoke_session(db, token=token, user_id=current_user.id)
    await log_auth_event(
        db,
        action="logout",
        result="success",
        phone=current_user.phone,
        user_id=current_user.id,
        ip=client_ip(request),
        user_agent=client_user_agent(request),
    )
    return {"ok": True}


@router.get("/session", response_model=SsoLoginResponse)
async def auth_session(
    authorization: str = Header(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """校验当前会话并返回会话 token 与用户信息。"""
    token = _extract_bearer_token(authorization) or ""
    return SsoLoginResponse(session_token=token, user=await _user_out(current_user, db))


@router.get("/me", response_model=UserOut)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前登录用户的个人信息与算力余额。"""
    return await _user_out(current_user, db)


@router.post("/sso", response_model=SsoLoginResponse)
async def sso_login(req: SsoLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """SSO 令牌登录：校验令牌后按来源用户创建或更新账号并签发会话。"""
    payload = verify_sso_token(req.token)
    if not payload:
        fail(ErrorCode.SSO_TOKEN_INVALID)

    source_user_id = str(payload.get("sub", ""))
    if not source_user_id:
        fail(ErrorCode.SSO_TOKEN_INVALID, message="SSO 登录凭证 payload 无效")

    phone = str(payload.get("phone", ""))
    display_name = str(payload.get("display_name", ""))

    result = await db.execute(select(User).filter(User.source_user_id == source_user_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            source_user_id=source_user_id,
            phone=phone if phone else None,
            display_name=display_name or f"canvas_{source_user_id[:8]}",
            compute_power=max(settings.default_user_credits, 0),
        )
        db.add(user)
        await db.flush()
    else:
        if phone and phone != user.phone:
            user.phone = phone
        if display_name and display_name != user.display_name:
            user.display_name = display_name

    if user.is_active is False:
        fail(ErrorCode.ACCOUNT_DISABLED)

    await log_auth_event(
        db,
        action="login_sso",
        result="success",
        phone=user.phone,
        user_id=user.id,
        ip=client_ip(request),
        user_agent=client_user_agent(request),
    )
    return await _issue_session(db, user, request=request)
