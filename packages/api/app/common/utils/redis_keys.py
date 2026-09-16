"""Redis Key 拼接（新缓存域）；现网强力锁 Key 在各 lock 模块冻结函数中。"""

from __future__ import annotations


def build_cache_key(domain: str, entity_id: str | int, alias: str = "default") -> str:
    """例：build_cache_key('model', 42, 'spec') → cache:model:42:spec"""
    return f"cache:{domain}:{entity_id}:{alias}"


def cache_version_key(domain: str) -> str:
    return f"cache:ver:{domain}"


def sequence_key(name: str) -> str:
    return f"seq:{name}"
