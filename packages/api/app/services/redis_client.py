"""向后兼容：连接层已迁至 app.common.utils.redis_client。"""

from ..common.utils.redis_client import close_redis, get_redis

__all__ = ["get_redis", "close_redis"]
