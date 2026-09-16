"""活动领取 Redis 强力锁（is_ / claim_ / seal_；失败不 DELETE）。"""

from __future__ import annotations

from ..common.utils.redis_mark_lock import MARK_LOCK_VALUE, mark_claim, mark_exists, mark_seal

CREDIT_ACTIVITY_CLAIM_TTL_SEC = 60
_CREDIT_ACTIVITY_CLAIM_MARK = MARK_LOCK_VALUE


def credit_claim_lock_key(activity_id: int, user_id: int) -> str:
    return f"lock:credit:claim:{activity_id}:{user_id}"


async def is_credit_activity_claimed(activity_id: int, user_id: int) -> bool:
    return await mark_exists(credit_claim_lock_key(activity_id, user_id))


async def claim_credit_activity(activity_id: int, user_id: int) -> bool:
    result = await mark_claim(
        credit_claim_lock_key(activity_id, user_id),
        ttl_sec=CREDIT_ACTIVITY_CLAIM_TTL_SEC,
        value=_CREDIT_ACTIVITY_CLAIM_MARK,
    )
    return bool(result)


async def seal_credit_activity_claim(activity_id: int, user_id: int) -> None:
    await mark_seal(
        credit_claim_lock_key(activity_id, user_id),
        ttl_sec=CREDIT_ACTIVITY_CLAIM_TTL_SEC,
        value=_CREDIT_ACTIVITY_CLAIM_MARK,
    )
