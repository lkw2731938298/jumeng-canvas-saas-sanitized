"""算力写操作与生成/模型提交的 Redis 标记锁（Redis 不可用时 fail-closed）。

标记锁模式（对齐 §8.11 / 媒体上游 POST）：is_(EXISTS) → claim_(SET NX "1") → 业务成功 seal_；
禁止在标记锁路径 finally DELETE。实现收敛至 app.common.utils.redis_mark_lock / redis_token_mutex。

本模块包含：
1. 算力写操作标记锁 — 充值 / 订阅 / 管理调整 / 生成预扣 / 退款
2. model_submit_lock — 文本任务用 Token 互斥锁（与媒体 claim_job_upstream_submit 标记锁共用 Key 函数）
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from ..common.utils.redis_mark_lock import (
    MARK_LOCK_VALUE,
    mark_claim,
    mark_delete,
    mark_exists,
    mark_peek_exists,
    mark_seal,
)
from ..common.utils.redis_token_mutex import token_mutex
from ..core.error_codes import ErrorCode

_MARK = MARK_LOCK_VALUE

# 各域标记锁 TTL（崩溃兜底；正常流程由 seal / clear 控制）
CREDIT_RECHARGE_LOCK_TTL_SEC = 5
PAYMENT_FULFILL_LOCK_TTL_SEC = 5 * 24 * 3600
CREDIT_SUBSCRIPTION_LOCK_TTL_SEC = 60
CREDIT_ADMIN_ADJUST_LOCK_TTL_SEC = 600
GENERATION_SUBMIT_LOCK_TTL_SEC = 7 * 24 * 3600
CREDIT_REFUND_LOCK_TTL_SEC = 7 * 24 * 3600
MODEL_SUBMIT_LOCK_TTL_SEC = 5 * 24 * 3600

GENERATION_CREDIT_REDIS_LOCK_TTL_SEC = CREDIT_REFUND_LOCK_TTL_SEC


def credit_recharge_lock_key(user_id: int) -> str:
    return f"lock:credit:recharge:{user_id}"


def payment_fulfill_lock_key(out_trade_no: str) -> str:
    return f"lock:payment:fulfill:{out_trade_no.strip()}"


def credit_subscription_lock_key(user_id: int) -> str:
    return f"lock:subscription:{user_id}"


def credit_admin_adjust_lock_key(user_id: int) -> str:
    return f"lock:credit:adjust:{user_id}"


def generation_submit_lock_key(user_id: int) -> str:
    return f"lock:generation:{user_id}"


def credit_refund_lock_key(job_id: int) -> str:
    return f"lock:credit:refund:{job_id}"


def agent_session_refund_lock_key(session_id: int, *, suffix: str = "start") -> str:
    """Agent 轨 S 退款锁（按会话 + 对话轮次 suffix，防多轮互撞）。"""
    safe = str(suffix or "start").strip() or "start"
    # 兼容旧键：仅 start 时保留无 suffix 形态，便于历史 pending 退款
    if safe == "start":
        return f"lock:credit:refund:agent-session:{int(session_id)}"
    return f"lock:credit:refund:agent-session:{int(session_id)}:{safe}"


def model_submit_lock_key(job_id: int) -> str:
    return f"lock:model:submit:{job_id}"


# ---------------------------------------------------------------------------
# 充值预下单等用户级写（TTL 5s）
# ---------------------------------------------------------------------------


async def is_credit_recharge_in_progress(user_id: int) -> bool:
    return await mark_exists(credit_recharge_lock_key(user_id))


async def claim_credit_recharge(user_id: int) -> bool:
    result = await mark_claim(
        credit_recharge_lock_key(user_id),
        ttl_sec=CREDIT_RECHARGE_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_credit_recharge(user_id: int) -> None:
    await mark_seal(
        credit_recharge_lock_key(user_id),
        ttl_sec=CREDIT_RECHARGE_LOCK_TTL_SEC,
        value=_MARK,
    )


# ---------------------------------------------------------------------------
# 支付订单入账（按 out_trade_no，TTL 5 天）
# ---------------------------------------------------------------------------


async def is_payment_fulfill_in_progress(out_trade_no: str) -> bool:
    return await mark_exists(payment_fulfill_lock_key(out_trade_no))


async def peek_payment_fulfill_lock(out_trade_no: str) -> bool | None:
    return await mark_peek_exists(payment_fulfill_lock_key(out_trade_no))


async def try_claim_payment_fulfill(out_trade_no: str) -> bool | None:
    return await mark_claim(
        payment_fulfill_lock_key(out_trade_no),
        ttl_sec=PAYMENT_FULFILL_LOCK_TTL_SEC,
        value=_MARK,
        fail_closed=False,
    )


async def claim_payment_fulfill(out_trade_no: str) -> bool:
    result = await mark_claim(
        payment_fulfill_lock_key(out_trade_no),
        ttl_sec=PAYMENT_FULFILL_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_payment_fulfill(out_trade_no: str) -> None:
    await mark_seal(
        payment_fulfill_lock_key(out_trade_no),
        ttl_sec=PAYMENT_FULFILL_LOCK_TTL_SEC,
        value=_MARK,
    )


# ---------------------------------------------------------------------------
# 订阅发放（TTL 60s）
# ---------------------------------------------------------------------------


async def is_credit_subscription_in_progress(user_id: int) -> bool:
    return await mark_exists(credit_subscription_lock_key(user_id))


async def claim_credit_subscription(user_id: int) -> bool:
    result = await mark_claim(
        credit_subscription_lock_key(user_id),
        ttl_sec=CREDIT_SUBSCRIPTION_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_credit_subscription(user_id: int) -> None:
    await mark_seal(
        credit_subscription_lock_key(user_id),
        ttl_sec=CREDIT_SUBSCRIPTION_LOCK_TTL_SEC,
        value=_MARK,
    )


# ---------------------------------------------------------------------------
# 管理加扣款（TTL 10 分钟）
# ---------------------------------------------------------------------------


async def is_credit_admin_adjust_in_progress(user_id: int) -> bool:
    return await mark_exists(credit_admin_adjust_lock_key(user_id))


async def claim_credit_admin_adjust(user_id: int) -> bool:
    result = await mark_claim(
        credit_admin_adjust_lock_key(user_id),
        ttl_sec=CREDIT_ADMIN_ADJUST_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_credit_admin_adjust(user_id: int) -> None:
    await mark_seal(
        credit_admin_adjust_lock_key(user_id),
        ttl_sec=CREDIT_ADMIN_ADJUST_LOCK_TTL_SEC,
        value=_MARK,
    )


# ---------------------------------------------------------------------------
# 生成入队 / 预扣（用户级；finally clear 放行下一笔）
# ---------------------------------------------------------------------------


async def is_generation_submit_in_progress(user_id: int) -> bool:
    return await mark_exists(generation_submit_lock_key(user_id))


async def claim_generation_submit(user_id: int) -> bool:
    result = await mark_claim(
        generation_submit_lock_key(user_id),
        ttl_sec=GENERATION_SUBMIT_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_generation_submit(user_id: int) -> None:
    await mark_seal(
        generation_submit_lock_key(user_id),
        ttl_sec=GENERATION_SUBMIT_LOCK_TTL_SEC,
        value=_MARK,
    )


async def clear_generation_submit(user_id: int) -> None:
    await mark_delete(generation_submit_lock_key(user_id))


# ---------------------------------------------------------------------------
# 退款（按 job_id，失败不 clear）
# ---------------------------------------------------------------------------


async def is_credit_refund_in_progress(job_id: int) -> bool:
    return await mark_exists(credit_refund_lock_key(job_id))


async def claim_credit_refund(job_id: int) -> bool:
    result = await mark_claim(
        credit_refund_lock_key(job_id),
        ttl_sec=CREDIT_REFUND_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_credit_refund(job_id: int) -> None:
    await mark_seal(
        credit_refund_lock_key(job_id),
        ttl_sec=CREDIT_REFUND_LOCK_TTL_SEC,
        value=_MARK,
    )


async def claim_agent_session_refund(session_id: int, *, suffix: str = "start") -> bool:
    result = await mark_claim(
        agent_session_refund_lock_key(session_id, suffix=suffix),
        ttl_sec=CREDIT_REFUND_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_agent_session_refund(session_id: int, *, suffix: str = "start") -> None:
    await mark_seal(
        agent_session_refund_lock_key(session_id, suffix=suffix),
        ttl_sec=CREDIT_REFUND_LOCK_TTL_SEC,
        value=_MARK,
    )


# ---------------------------------------------------------------------------
# 媒体上游 POST（按 job_id，标记锁）
# ---------------------------------------------------------------------------


async def is_job_upstream_submitted(job_id: int) -> bool:
    return await mark_exists(model_submit_lock_key(job_id))


async def claim_job_upstream_submit(job_id: int) -> bool:
    result = await mark_claim(
        model_submit_lock_key(job_id),
        ttl_sec=MODEL_SUBMIT_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_job_upstream_submit(job_id: int) -> None:
    await mark_seal(
        model_submit_lock_key(job_id),
        ttl_sec=MODEL_SUBMIT_LOCK_TTL_SEC,
        value=_MARK,
    )


async def clear_job_upstream_submit(job_id: int) -> None:
    await mark_delete(model_submit_lock_key(job_id))


# ---------------------------------------------------------------------------
# 上下文管理器（对外 API 不变）
# ---------------------------------------------------------------------------


@asynccontextmanager
async def credit_recharge_lock(user_id: int, *, ttl_sec: int = CREDIT_RECHARGE_LOCK_TTL_SEC) -> AsyncIterator[None]:
    from ..core.errors import fail

    if await is_credit_recharge_in_progress(user_id):
        fail(ErrorCode.RECHARGE_IN_PROGRESS)
    if not await claim_credit_recharge(user_id):
        fail(ErrorCode.RECHARGE_IN_PROGRESS, message="充值处理中，请稍后再试")
    try:
        yield
    except Exception:
        raise


@asynccontextmanager
async def credit_subscription_lock(
    user_id: int, *, ttl_sec: int = CREDIT_SUBSCRIPTION_LOCK_TTL_SEC
) -> AsyncIterator[None]:
    from ..core.errors import fail

    if await is_credit_subscription_in_progress(user_id):
        fail(ErrorCode.SUBSCRIPTION_IN_PROGRESS)
    if not await claim_credit_subscription(user_id):
        fail(ErrorCode.SUBSCRIPTION_IN_PROGRESS)
    try:
        yield
    except Exception:
        raise


@asynccontextmanager
async def credit_admin_adjust_lock(
    user_id: int, *, ttl_sec: int = CREDIT_ADMIN_ADJUST_LOCK_TTL_SEC
) -> AsyncIterator[None]:
    from ..core.errors import fail

    if await is_credit_admin_adjust_in_progress(user_id):
        fail(ErrorCode.CREDIT_ADJUST_IN_PROGRESS)
    if not await claim_credit_admin_adjust(user_id):
        fail(ErrorCode.CREDIT_ADJUST_IN_PROGRESS)
    try:
        yield
    except Exception:
        raise


@asynccontextmanager
async def generation_submit_lock(
    user_id: int, *, ttl_sec: int = GENERATION_SUBMIT_LOCK_TTL_SEC
) -> AsyncIterator[None]:
    from ..core.errors import fail

    if await is_generation_submit_in_progress(user_id):
        fail(ErrorCode.RATE_LIMITED, message="生成提交处理中，请稍后再试")
    if not await claim_generation_submit(user_id):
        fail(ErrorCode.RATE_LIMITED, message="生成提交处理中，请稍后再试")
    try:
        yield
    finally:
        await clear_generation_submit(user_id)


@asynccontextmanager
async def model_submit_lock(
    job_id: int, *, ttl_sec: int = MODEL_SUBMIT_LOCK_TTL_SEC
) -> AsyncIterator[None]:
    """文本任务：Token 互斥锁（finally 删键）；禁止改为 mark_claim。"""
    async with token_mutex(
        model_submit_lock_key(job_id),
        ttl_sec=ttl_sec,
        busy_code=ErrorCode.RATE_LIMITED,
        busy_message="模型提交处理中，请稍后再试",
    ):
        yield
