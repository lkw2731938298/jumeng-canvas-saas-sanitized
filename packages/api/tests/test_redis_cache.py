"""Cache-Aside 读穿透与版本失效（不依赖真实 Redis）。"""

from __future__ import annotations

import json

import pytest

from app.common.utils import redis_cache as cache_mod


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None):
        self.store[key] = value
        return True

    async def incr(self, key: str) -> int:
        raw = self.store.get(key, "0")
        n = int(raw) + 1
        self.store[key] = str(n)
        return n

    async def delete(self, *keys: str) -> int:
        n = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                n += 1
        return n


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()

    async def _optional():
        return client

    monkeypatch.setattr(cache_mod, "optional_redis_client", _optional)
    return client


@pytest.mark.asyncio
async def test_cache_get_or_load_miss_then_hit(fake_redis):
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return {"id": 1}

    first = await cache_mod.cache_get_or_load(
        "cache:model:1:row",
        ttl=60,
        loader=loader,
        version_domain="model",
    )
    second = await cache_mod.cache_get_or_load(
        "cache:model:1:row",
        ttl=60,
        loader=loader,
        version_domain="model",
    )
    assert first == {"id": 1}
    assert second == {"id": 1}
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_bump_version_forces_reload(fake_redis):
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return calls["n"]

    key = "cache:model:list:available"
    await cache_mod.cache_get_or_load(key, ttl=60, loader=loader, version_domain="model")
    await cache_mod.bump_cache_version("model")
    result = await cache_mod.cache_get_or_load(key, ttl=60, loader=loader, version_domain="model")
    assert result == 2
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_cache_null_false_skips_none(fake_redis):
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return None

    await cache_mod.cache_get_or_load("cache:model:missing:row", ttl=60, loader=loader, version_domain="model")
    await cache_mod.cache_get_or_load("cache:model:missing:row", ttl=60, loader=loader, version_domain="model")
    assert calls["n"] == 2
    assert "cache:model:missing:row" not in fake_redis.store


@pytest.mark.asyncio
async def test_redis_unavailable_degrades_to_loader(monkeypatch):
    async def _none():
        return None

    monkeypatch.setattr(cache_mod, "optional_redis_client", _none)
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return "ok"

    out = await cache_mod.cache_get_or_load("k", ttl=60, loader=loader, version_domain="model")
    assert out == "ok"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_cached_envelope_has_version(fake_redis):
    async def loader():
        return {"x": 1}

    await cache_mod.cache_get_or_load("k", ttl=30, loader=loader, version_domain="model")
    raw = fake_redis.store["k"]
    payload = json.loads(raw)
    assert payload["ver"] == 0
    assert payload["data"] == {"x": 1}
