"""向后兼容：Token 互斥锁已迁至 app.common.utils.redis_token_mutex。"""

from ..common.utils.redis_token_mutex import (
    redis_mutex,
    require_redis_for_lock,
    token_mutex,
)

__all__ = ["redis_mutex", "require_redis_for_lock", "token_mutex"]
