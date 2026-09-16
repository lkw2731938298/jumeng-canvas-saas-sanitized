"""敏感词 Redis 缓存：全站仅一个 key，生成校验热读；改库后 write-through 覆盖。"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .redis_client import get_redis

logger = logging.getLogger(__name__)

# 全站唯一热缓存 key：仅存已启用词的 word_norm 列表；TTL 1 天，过期后回源 MySQL 再加载
SENSITIVE_WORDS_CACHE_KEY = "cache:sensitive_words:active"
SENSITIVE_WORDS_CACHE_TTL_SEC = 24 * 3600


async def get_active_word_norms_from_cache() -> list[str] | None:
    """读 Redis 单 key；miss / 不可用返回 None（由调用方回源 DB）。"""
    client = await get_redis()
    if client is None:
        return None
    raw = await client.get(SENSITIVE_WORDS_CACHE_KEY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid sensitive words cache json")
        return None
    if not isinstance(data, list):
        return None
    return [str(item) for item in data if isinstance(item, str) and item]


async def set_active_word_norms_cache(word_norms: list[str]) -> None:
    """覆盖写入唯一缓存 key（空列表也缓存，避免反复打 DB）。"""
    client = await get_redis()
    if client is None:
        return
    payload = json.dumps(list(word_norms), ensure_ascii=False, separators=(",", ":"))
    await client.set(SENSITIVE_WORDS_CACHE_KEY, payload, ex=SENSITIVE_WORDS_CACHE_TTL_SEC)


async def load_enabled_word_norms_from_db(db: AsyncSession) -> list[str]:
    """从 MySQL 加载全部启用词的规范化形式。"""
    from ..models.sensitive_word import SensitiveWord

    result = await db.execute(
        select(SensitiveWord.word_norm).where(SensitiveWord.enabled.is_(True))
    )
    return [str(row[0]) for row in result.all() if row[0]]


async def publish_active_sensitive_words_cache(db: AsyncSession) -> list[str]:
    """DB 变更后：重载启用词表并覆盖 Redis 单 key。"""
    words = await load_enabled_word_norms_from_db(db)
    await set_active_word_norms_cache(words)
    return words


async def get_active_word_norms(db: AsyncSession) -> list[str]:
    """校验入口：优先 Redis；TTL 过期 / miss 则读 MySQL，再 SET 回唯一 key（TTL 1 天）。"""
    cached = await get_active_word_norms_from_cache()
    if cached is not None:
        return cached
    # 超时或无缓存：回源数据库，再加载到缓存
    words = await load_enabled_word_norms_from_db(db)
    await set_active_word_norms_cache(words)
    return words
