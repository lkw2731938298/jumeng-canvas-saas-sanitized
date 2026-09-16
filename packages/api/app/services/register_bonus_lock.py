"""首次注册赠送 Redis 标记锁（is_ → claim SET NX → seal；失败不 DELETE）。"""

from __future__ import annotations

from ..common.utils.redis_mark_lock import MARK_LOCK_VALUE, mark_claim, mark_exists, mark_seal

# 崩溃兜底；UNIQUE(bonus_key, user_id) 才是终身防双发权威
REGISTER_BONUS_LOCK_TTL_SEC = 7 * 24 * 3600
_MARK = MARK_LOCK_VALUE


def register_bonus_lock_key(user_id: int) -> str:
    return f"lock:credit:register-bonus:{int(user_id)}"


async def is_register_bonus_in_progress(user_id: int) -> bool:
    return await mark_exists(register_bonus_lock_key(user_id))


async def claim_register_bonus(user_id: int) -> bool:
    result = await mark_claim(
        register_bonus_lock_key(user_id),
        ttl_sec=REGISTER_BONUS_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_register_bonus(user_id: int) -> None:
    await mark_seal(
        register_bonus_lock_key(user_id),
        ttl_sec=REGISTER_BONUS_LOCK_TTL_SEC,
        value=_MARK,
    )
