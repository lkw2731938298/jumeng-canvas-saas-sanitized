"""首次注册赠送：开关/点数来自 platform_settings；每人终身一次。

防双发三层（对齐 §8.11）：
1. Redis 标记锁 lock:credit:register-bonus:{userId}（fail-closed）
2. SELECT users FOR UPDATE
3. UNIQUE(bonus_key, user_id) 占位后再 grant_credits_with_log
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_types import CREDIT_TYPE_ACTIVITY
from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import AppError, fail
from ..models.credit_lot import CreditLot
from ..models.register_bonus import REGISTER_BONUS_KEY, RegisterBonusGrant
from ..models.user import User
from .auth_sms import mask_phone
from .credit_transactions import grant_credits_with_log
from .platform_settings import get_register_bonus_settings
from .register_bonus_lock import (
    claim_register_bonus,
    is_register_bonus_in_progress,
    seal_register_bonus,
)

logger = logging.getLogger(__name__)

SOURCE_REGISTER_BONUS = "register_bonus"
GRANT_STATUS_RESERVED = "reserved"
GRANT_STATUS_GRANTED = "granted"
# 注册赠送按活动算力，有效期 30 天（与邀请奖励默认一致）
REGISTER_BONUS_VALID_DAYS = 30
REGISTER_BONUS_AMOUNT_MAX = 1_000_000


def _source_ref(user_id: int) -> str:
    return f"register-bonus:{int(user_id)}"


def normalize_register_bonus_amount(raw: Any) -> int:
    """后台配置的赠送点数：整数、0～上限。"""
    try:
        amount = int(raw)
    except (TypeError, ValueError):
        return 0
    if amount < 0:
        return 0
    if amount > REGISTER_BONUS_AMOUNT_MAX:
        return REGISTER_BONUS_AMOUNT_MAX
    return amount


async def try_grant_register_bonus(db: AsyncSession, user: User) -> dict[str, Any]:
    """注册成功后尝试赠送。关闭/点数为 0 则跳过；已发则幂等。

    Redis 不可用 fail-closed：向上抛 AppError，注册事务回滚。
    """
    cfg = await get_register_bonus_settings(db)
    amount = int(cfg.get("amount") or 0)
    if not cfg.get("enabled") or amount <= 0:
        return {"granted": False, "amount": 0, "skipped": True}

    user_id = int(user.id)

    existing = await db.execute(
        select(RegisterBonusGrant)
        .filter(
            RegisterBonusGrant.bonus_key == REGISTER_BONUS_KEY,
            RegisterBonusGrant.user_id == user_id,
        )
        .with_for_update()
    )
    grant = existing.scalar_one_or_none()
    if grant and grant.status == GRANT_STATUS_GRANTED:
        return {"granted": True, "amount": int(grant.amount or 0), "idempotent": True}

    resume_reserved = grant is not None and grant.status == GRANT_STATUS_RESERVED
    if not resume_reserved:
        if await is_register_bonus_in_progress(user_id):
            fail(ErrorCode.REGISTER_BONUS_IN_PROGRESS)
        if not await claim_register_bonus(user_id):
            fail(ErrorCode.REGISTER_BONUS_IN_PROGRESS)

    try:
        # 锁用户行，避免并发改余额
        locked = await db.execute(select(User).filter(User.id == user_id).with_for_update())
        beneficiary = locked.scalar_one_or_none() or user

        if grant is None:
            grant = RegisterBonusGrant(
                bonus_key=REGISTER_BONUS_KEY,
                user_id=user_id,
                amount=amount,
                status=GRANT_STATUS_RESERVED,
            )
            try:
                async with db.begin_nested():
                    db.add(grant)
                    await db.flush()
            except IntegrityError:
                row = await db.execute(
                    select(RegisterBonusGrant)
                    .filter(
                        RegisterBonusGrant.bonus_key == REGISTER_BONUS_KEY,
                        RegisterBonusGrant.user_id == user_id,
                    )
                    .with_for_update()
                )
                grant = row.scalar_one_or_none()
                if grant and grant.status == GRANT_STATUS_GRANTED:
                    await seal_register_bonus(user_id)
                    return {"granted": True, "amount": int(grant.amount or 0), "idempotent": True}
                if grant is None:
                    fail(ErrorCode.REGISTER_BONUS_IN_PROGRESS)

        if grant.status == GRANT_STATUS_GRANTED:
            await seal_register_bonus(user_id)
            return {"granted": True, "amount": int(grant.amount or 0), "idempotent": True}

        existing_lot = await db.execute(
            select(CreditLot)
            .filter(
                CreditLot.user_id == user_id,
                CreditLot.source == SOURCE_REGISTER_BONUS,
                CreditLot.source_ref == _source_ref(user_id),
            )
            .limit(1)
        )
        lot = existing_lot.scalar_one_or_none()
        if lot is None:
            await grant_credits_with_log(
                db,
                user=beneficiary,
                amount=int(grant.amount),
                credit_type=CREDIT_TYPE_ACTIVITY,
                source=SOURCE_REGISTER_BONUS,
                reason="首次注册赠送",
                valid_days=REGISTER_BONUS_VALID_DAYS,
                source_ref=_source_ref(user_id),
            )
            lot_row = await db.execute(
                select(CreditLot)
                .filter(
                    CreditLot.user_id == user_id,
                    CreditLot.source == SOURCE_REGISTER_BONUS,
                    CreditLot.source_ref == _source_ref(user_id),
                )
                .limit(1)
            )
            lot = lot_row.scalar_one_or_none()
            if lot is None:
                logger.error("register bonus missing lot user=%s", user_id)
                fail(ErrorCode.REGISTER_BONUS_IN_PROGRESS)

        grant.lot_id = lot.id
        grant.status = GRANT_STATUS_GRANTED
        grant.granted_at = now_cst_naive()
        await db.flush()
        await seal_register_bonus(user_id)
        return {"granted": True, "amount": int(grant.amount or 0), "idempotent": False}
    except AppError:
        raise
    except Exception:
        logger.exception("register bonus failed user=%s", user_id)
        fail(ErrorCode.REGISTER_BONUS_IN_PROGRESS)


async def list_register_bonus_grants(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    search: str = "",
) -> dict[str, Any]:
    """管理端发放列表（含用户展示名/手机号）。"""
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    filters = [RegisterBonusGrant.bonus_key == REGISTER_BONUS_KEY]
    keyword = (search or "").strip()
    if keyword:
        like = f"%{keyword}%"
        filters.append(or_(User.phone.like(like), User.display_name.like(like)))

    count_stmt = (
        select(func.count(RegisterBonusGrant.id))
        .join(User, User.id == RegisterBonusGrant.user_id)
        .filter(*filters)
    )
    total = int((await db.execute(count_stmt)).scalar() or 0)
    rows = (
        await db.execute(
            select(RegisterBonusGrant, User)
            .join(User, User.id == RegisterBonusGrant.user_id)
            .filter(*filters)
            .order_by(RegisterBonusGrant.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    items: list[dict[str, Any]] = []
    for grant, user in rows:
        phone = (user.phone or "").strip()
        items.append(
            {
                "id": str(grant.id),
                "userId": str(user.id),
                "displayName": user.display_name or "",
                "phone": phone,
                "phoneMasked": mask_phone(phone) if phone else "",
                "amount": int(grant.amount or 0),
                "status": grant.status,
                "createdAt": to_cst_iso(grant.created_at),
                "grantedAt": to_cst_iso(grant.granted_at) if grant.granted_at else None,
            }
        )
    return {"items": items, "total": total, "page": page, "pageSize": page_size}
