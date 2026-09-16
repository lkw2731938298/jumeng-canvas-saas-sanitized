"""平台 Prompt OSS + Redis 共享存储单测。"""

from __future__ import annotations

import json

import pytest

from app.services.prompt_platform_runtime import (
    apply_l1_prompt_config,
    clear_l1_prompt_platform,
    get_l1_prompt_config,
    refresh_prompt_config_if_version_changed,
)
from app.services.prompt_platform_storage import (
    PROMPT_CONFIG_CACHE_KEY,
    publish_prompt_config_to_redis,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None):
        self.store[key] = value
        return True

    async def incr(self, key: str) -> int:
        val = int(self.store.get(key, "0")) + 1
        self.store[key] = str(val)
        return val


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()

    async def _get_redis():
        return client

    monkeypatch.setattr("app.common.utils.redis_store.get_redis", _get_redis)
    monkeypatch.setattr("app.common.utils.redis_client.get_redis", _get_redis, raising=False)
    return client


@pytest.mark.asyncio
async def test_refresh_prompt_config_from_redis(fake_redis, monkeypatch):
    clear_l1_prompt_platform()
    payload = {"version": 2, "tools": {"multi_angle": {"kind": "composer"}}}
    fake_redis.store["cache:ver:prompt_config"] = "4"
    fake_redis.store[PROMPT_CONFIG_CACHE_KEY] = json.dumps(
        {"ver": 4, "data": payload},
        ensure_ascii=False,
    )

    async def _ver(domain: str) -> int:
        assert domain == "prompt_config"
        return 4

    monkeypatch.setattr("app.common.utils.redis_cache.get_cache_version", _ver)

    changed = await refresh_prompt_config_if_version_changed()
    assert changed is True
    l1 = get_l1_prompt_config()
    assert l1 is not None
    assert l1["tools"]["multi_angle"]["kind"] == "composer"

    changed_again = await refresh_prompt_config_if_version_changed()
    assert changed_again is False


@pytest.mark.asyncio
async def test_publish_prompt_config_to_redis(fake_redis, monkeypatch):
    async def _ver(_domain: str) -> int:
        return 1

    monkeypatch.setattr("app.common.utils.redis_cache.get_cache_version", _ver)
    data = {"version": 2, "tools": {}}
    ok = await publish_prompt_config_to_redis(data, ver=1)
    assert ok is True
    assert PROMPT_CONFIG_CACHE_KEY in fake_redis.store
    apply_l1_prompt_config(data, ver=1)
    assert get_l1_prompt_config() == data
