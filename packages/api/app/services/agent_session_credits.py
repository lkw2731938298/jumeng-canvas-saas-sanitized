"""Agent 会话编排算力（轨 S）预扣 / 结算 / 释放。

- 计费单位：一次对话（用户发一条并触发 AI）
- 预扣：``reserve_local_credits``（job_id=None），幂等键 ``agent-session-{id}-{suffix}``
  - 首轮 / 续聊均用 ``turn-{seq}``（或兼容旧 ``start``）
- 成功后 commit；失败且未写 Graph 则 release
- 用户锁复用 ``generation_submit_lock``；退款锁按 session+suffix
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_amount import normalize_credit_amount
from ..core.credit_status import CreditStatus
from ..core.credit_tx_sources import (
    CREDIT_TX_SOURCE_AGENT_ORCHESTRATE,
    CREDIT_TX_SOURCE_AGENT_ORCHESTRATE_REFUND,
)
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.agent_session import AgentSession
from ..models.user import User
from .agent_skill_pricing import get_agent_skill_pricing, quote_agent_session_cost
from .credit_flow import credits_enabled
from .credit_operation_lock import (
    claim_agent_session_refund,
    generation_submit_lock,
    seal_agent_session_refund,
)
from .credit_transactions import record_credit_transaction
from .local_credits import (
    commit_local_credits,
    get_local_balance,
    release_local_credits,
    reserve_local_credits,
)

logger = logging.getLogger(__name__)


def _consume_ref(session_id: int, *, suffix: str = "start") -> str:
    return f"agent-session-consume-{session_id}-{suffix}"


def _refund_ref(session_id: int, *, suffix: str = "start") -> str:
    return f"agent-session-refund-{session_id}-{suffix}"


def _idem_key(session_id: int, *, suffix: str = "start") -> str:
    return f"agent-session-{session_id}-{suffix}"


def get_session_credit_turn_suffix(session: AgentSession) -> str:
    brief = session.brief_json if isinstance(session.brief_json, dict) else {}
    raw = str(brief.get("creditTurnSuffix") or "").strip()
    return raw or "start"


def set_session_credit_turn_suffix(session: AgentSession, suffix: str) -> None:
    brief: dict[str, Any] = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    brief["creditTurnSuffix"] = str(suffix or "start").strip() or "start"
    session.brief_json = brief


async def reserve_orchestration_credits(
    db: AsyncSession,
    *,
    user: User,
    session: AgentSession,
    skill_slug: str | None = None,
    suffix: str = "start",
) -> dict[str, Any]:
    """为即将跑的一次对话预扣轨 S；返回报价摘要。"""
    pricing = await get_agent_skill_pricing(db)
    quote = quote_agent_session_cost(skill_slug=skill_slug, pricing=pricing)

    set_session_credit_turn_suffix(session, suffix)
    session.pricing_version = quote.pricing_version
    session.credit_breakdown = [item.to_dict() for item in quote.breakdown]
    session.credit_cost = int(quote.total)

    if not credits_enabled():
        session.credit_status = CreditStatus.SKIPPED.value
        await db.flush()
        return {
            "total": quote.total,
            "creditsEnabled": False,
            "creditStatus": "skipped",
            "billing": "conversation_turn",
        }

    if quote.total <= 0:
        fail(ErrorCode.PRICE_NOT_CONFIGURED, message="AI 操控对话算力尚未配置，请联系管理员")

    async with generation_submit_lock(int(user.id)):
        balance = await get_local_balance(db, user)
        if balance < quote.total:
            fail(
                ErrorCode.INSUFFICIENT_CREDITS,
                message=f"算力不足，本次对话需要 {quote.total}，当前可用 {balance}",
                content={"required": quote.total, "available": balance, "track": "S"},
            )

        idem = _idem_key(int(session.id), suffix=suffix)
        result = await reserve_local_credits(
            db,
            user_id=int(user.id),
            amount=quote.total,
            idempotency_key=idem,
            reference=f"agent-session:{session.id}:{suffix}",
            job_id=None,
            model_name=None,
        )
        if not result or not result.get("reservation_id"):
            fail(ErrorCode.CREDIT_RESERVE_FAILED, message="对话算力预扣失败，请稍后重试")

        session.credit_reservation_id = str(result["reservation_id"])
        session.credit_status = CreditStatus.RESERVED.value
        if result.get("created") and quote.total > 0:
            balance_after = await get_local_balance(db, user)
            await record_credit_transaction(
                db,
                user_id=int(user.id),
                delta=-int(quote.total),
                balance_after=balance_after,
                source=CREDIT_TX_SOURCE_AGENT_ORCHESTRATE,
                reason=f"AI 操控对话预扣 · 会话 {session.id} · {suffix}",
                job_id=None,
                reference_key=_consume_ref(int(session.id), suffix=suffix),
            )
        await db.flush()

    return {
        "total": quote.total,
        "creditsEnabled": True,
        "creditStatus": session.credit_status,
        "pricingVersion": quote.pricing_version,
        "billing": "conversation_turn",
        "suffix": suffix,
    }


async def commit_orchestration_credits(db: AsyncSession, session: AgentSession) -> bool:
    """一次对话成功后结算轨 S。"""
    if not credits_enabled():
        if session.credit_status != CreditStatus.SKIPPED.value:
            session.credit_status = CreditStatus.SKIPPED.value
        return True
    rid = (session.credit_reservation_id or "").strip()
    if not rid or session.credit_status == CreditStatus.SKIPPED.value:
        if (session.credit_cost or 0) <= 0:
            session.credit_status = CreditStatus.SKIPPED.value
        return True
    if session.credit_status == CreditStatus.COMMITTED.value:
        return True
    if session.credit_status == CreditStatus.RELEASED.value:
        return False

    ok = await commit_local_credits(db, rid)
    session.credit_status = (
        CreditStatus.COMMITTED.value if ok else CreditStatus.COMMIT_PENDING.value
    )
    await db.flush()
    if not ok:
        logger.error(
            "agent session %s credit commit pending reservation=%s",
            session.id,
            rid,
        )
    return ok


async def release_orchestration_credits(
    db: AsyncSession,
    session: AgentSession,
    *,
    suffix: str | None = None,
) -> bool:
    """对话失败且未写 Graph：释放轨 S（失败不 clear Redis 退款锁）。"""
    if not credits_enabled():
        return True
    rid = (session.credit_reservation_id or "").strip()
    if not rid or session.credit_status in (
        CreditStatus.SKIPPED.value,
        CreditStatus.RELEASED.value,
        CreditStatus.COMMITTED.value,
        None,
        "",
    ):
        return True

    sid = int(session.id)
    turn = (suffix or get_session_credit_turn_suffix(session)).strip() or "start"
    if not await claim_agent_session_refund(sid, suffix=turn):
        # 已有退款标记：尝试幂等 release
        logger.info("agent session %s refund lock busy suffix=%s; try idempotent release", sid, turn)

    ok = await release_local_credits(db, rid)
    if ok:
        session.credit_status = CreditStatus.RELEASED.value
        amount = normalize_credit_amount(session.credit_cost or 0)
        if amount > 0:
            from ..models.user import User as UserModel
            from sqlalchemy import select

            user = (
                await db.execute(
                    select(UserModel).where(UserModel.id == int(session.user_id)).limit(1)
                )
            ).scalar_one_or_none()
            balance_after = await get_local_balance(db, user) if user else 0
            await record_credit_transaction(
                db,
                user_id=int(session.user_id),
                delta=amount,
                balance_after=balance_after,
                source=CREDIT_TX_SOURCE_AGENT_ORCHESTRATE_REFUND,
                reason=f"AI 操控对话失败退还 · 会话 {session.id} · {turn}",
                job_id=None,
                reference_key=_refund_ref(sid, suffix=turn),
            )
        await seal_agent_session_refund(sid, suffix=turn)
        await db.flush()
        return True

    session.credit_status = CreditStatus.RELEASE_PENDING.value
    await db.flush()
    logger.error("agent session %s credit release pending reservation=%s", sid, rid)
    return False
