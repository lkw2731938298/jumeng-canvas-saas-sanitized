"""邀请绑定/发奖 Redis 强力锁（is_ / claim_ / seal_；失败不 DELETE）。"""

from __future__ import annotations

from ..common.utils.redis_mark_lock import MARK_LOCK_VALUE, mark_claim, mark_exists, mark_seal

# 与活动领取同量级；崩溃兜底，非业务重试窗口
INVITE_BIND_LOCK_TTL_SEC = 7 * 24 * 3600
INVITE_REWARD_LOCK_TTL_SEC = 7 * 24 * 3600
_MARK = MARK_LOCK_VALUE


def invite_bind_lock_key(invitee_user_id: int) -> str:
    return f"lock:invite:bind:{int(invitee_user_id)}"


def invite_reward_lock_key(binding_id: int, role: str) -> str:
    return f"lock:invite:reward:{int(binding_id)}:{str(role)}"


async def is_invite_bind_in_progress(invitee_user_id: int) -> bool:
    return await mark_exists(invite_bind_lock_key(invitee_user_id))


async def claim_invite_bind(invitee_user_id: int) -> bool:
    result = await mark_claim(
        invite_bind_lock_key(invitee_user_id),
        ttl_sec=INVITE_BIND_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_invite_bind(invitee_user_id: int) -> None:
    await mark_seal(
        invite_bind_lock_key(invitee_user_id),
        ttl_sec=INVITE_BIND_LOCK_TTL_SEC,
        value=_MARK,
    )


async def is_invite_reward_in_progress(binding_id: int, role: str) -> bool:
    return await mark_exists(invite_reward_lock_key(binding_id, role))


async def claim_invite_reward(binding_id: int, role: str) -> bool:
    result = await mark_claim(
        invite_reward_lock_key(binding_id, role),
        ttl_sec=INVITE_REWARD_LOCK_TTL_SEC,
        value=_MARK,
    )
    return bool(result)


async def seal_invite_reward(binding_id: int, role: str) -> None:
    await mark_seal(
        invite_reward_lock_key(binding_id, role),
        ttl_sec=INVITE_REWARD_LOCK_TTL_SEC,
        value=_MARK,
    )
