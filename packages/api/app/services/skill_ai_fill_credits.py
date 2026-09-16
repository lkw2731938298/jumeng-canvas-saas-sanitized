"""创建 Skill「按类型 AI 填充」算力：预扣 → 成功结算 / 失败释放。

无 generation_jobs 行；幂等键 ``skill-ai-fill-{userId}-{uuid}``。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_tx_sources import (
    CREDIT_TX_SOURCE_SKILL_AI_FILL,
    CREDIT_TX_SOURCE_SKILL_AI_FILL_REFUND,
)
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.user import User
from .agent_skill_pricing import get_agent_skill_pricing, quote_skill_ai_fill_cost
from .credit_flow import credits_enabled
from .credit_operation_lock import generation_submit_lock
from .credit_transactions import record_credit_transaction
from .local_credits import (
    commit_local_credits,
    get_local_balance,
    release_local_credits,
    reserve_local_credits,
)

logger = logging.getLogger(__name__)


async def charge_skill_ai_fill(
    db: AsyncSession,
    *,
    user: User,
) -> dict[str, Any]:
    """预扣「按类型 AI 填充」算力；返回 reservation 摘要（含 reservation_id）。"""
    pricing = await get_agent_skill_pricing(db)
    quote = quote_skill_ai_fill_cost(pricing)

    if not credits_enabled():
        return {
            "total": quote.total,
            "creditsEnabled": False,
            "creditStatus": "skipped",
            "reservationId": None,
            "pricingVersion": quote.pricing_version,
        }

    if quote.total <= 0:
        fail(
            ErrorCode.PRICE_NOT_CONFIGURED,
            message="Skill AI 填充算力尚未配置，请联系管理员",
        )

    fill_id = uuid.uuid4().hex[:16]
    idem = f"skill-ai-fill-{int(user.id)}-{fill_id}"

    async with generation_submit_lock(int(user.id)):
        balance = await get_local_balance(db, user)
        if balance < quote.total:
            fail(
                ErrorCode.INSUFFICIENT_CREDITS,
                message=f"算力不足，AI 填充需要 {quote.total}，当前可用 {balance}",
                content={"required": quote.total, "available": balance, "billing": "skill_ai_fill"},
            )

        result = await reserve_local_credits(
            db,
            user_id=int(user.id),
            amount=quote.total,
            idempotency_key=idem,
            reference=f"skill-ai-fill:{fill_id}",
            job_id=None,
            model_name=None,
        )
        if not result or not result.get("reservation_id"):
            fail(ErrorCode.CREDIT_RESERVE_FAILED, message="AI 填充算力预扣失败，请稍后重试")

        rid = str(result["reservation_id"])
        if result.get("created") and quote.total > 0:
            balance_after = await get_local_balance(db, user)
            await record_credit_transaction(
                db,
                user_id=int(user.id),
                delta=-int(quote.total),
                balance_after=balance_after,
                source=CREDIT_TX_SOURCE_SKILL_AI_FILL,
                reason=f"创建 Skill · 按类型 AI 填充 · {fill_id}",
                job_id=None,
                reference_key=f"skill-ai-fill-consume-{int(user.id)}-{fill_id}",
            )

    return {
        "total": quote.total,
        "creditsEnabled": True,
        "creditStatus": "reserved",
        "reservationId": rid,
        "pricingVersion": quote.pricing_version,
        "fillId": fill_id,
    }


async def commit_skill_ai_fill(db: AsyncSession, reservation_id: str | None) -> None:
    """填充成功后结算预扣。"""
    rid = (reservation_id or "").strip()
    if not rid or not credits_enabled():
        return
    ok = await commit_local_credits(db, rid)
    if not ok:
        logger.error("skill ai fill credit commit pending reservation=%s", rid)


async def release_skill_ai_fill(
    db: AsyncSession,
    *,
    user: User,
    reservation_id: str | None,
    amount: int = 0,
    fill_id: str | None = None,
) -> None:
    """填充失败时退还预扣。"""
    rid = (reservation_id or "").strip()
    if not rid or not credits_enabled():
        return
    released = await release_local_credits(db, rid)
    if released and amount > 0:
        try:
            balance_after = await get_local_balance(db, user)
            suffix = (fill_id or rid)[:32]
            await record_credit_transaction(
                db,
                user_id=int(user.id),
                delta=int(amount),
                balance_after=balance_after,
                source=CREDIT_TX_SOURCE_SKILL_AI_FILL_REFUND,
                reason=f"创建 Skill · AI 填充失败退还 · {suffix}",
                job_id=None,
                reference_key=f"skill-ai-fill-refund-{int(user.id)}-{suffix}",
            )
        except Exception:
            logger.exception("skill ai fill refund tx log failed reservation=%s", rid)
