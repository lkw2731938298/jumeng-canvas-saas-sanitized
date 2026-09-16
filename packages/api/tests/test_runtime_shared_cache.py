"""跨进程 Redis 运行时快照：List 索引 + SET 规格 + 密钥整包。"""

from __future__ import annotations

import json

import pytest

from app.core.llm_keys import LlmKeysConfig, ProviderKeys
from app.core.model_registry import CanvasModelSpec
from app.services.runtime_shared_cache import (
    CATALOG_NAMES_LIST_KEY,
    catalog_spec_key,
    CREDENTIAL_SNAPSHOT_KEY,
    load_llm_keys_from_redis,
    load_model_catalog_from_redis,
    model_spec_from_dict,
    model_spec_to_dict,
    publish_llm_keys_to_redis,
    publish_model_catalog_to_redis,
)


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple, dict]] = []

    def set(self, key: str, value: str, *, ex: int | None = None):
        self._ops.append(("set", (key, value), {"ex": ex}))
        return self

    def delete(self, *keys: str):
        self._ops.append(("delete", keys, {}))
        return self

    def rpush(self, key: str, *values: str):
        self._ops.append(("rpush", (key, *values), {}))
        return self

    def expire(self, key: str, ttl: int):
        self._ops.append(("expire", (key, ttl), {}))
        return self

    async def execute(self):
        for op, args, kwargs in self._ops:
            if op == "set":
                await self._redis.set(args[0], args[1], ex=kwargs.get("ex"))
            elif op == "delete":
                await self._redis.delete(*args)
            elif op == "rpush":
                key, *values = args
                await self._redis.rpush(key, *values)
            elif op == "expire":
                await self._redis.expire(args[0], args[1])


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]

    async def delete(self, *keys: str) -> int:
        n = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                n += 1
            if key in self.lists:
                del self.lists[key]
                n += 1
        return n

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]

    async def rpush(self, key: str, *values: str) -> int:
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    async def expire(self, key: str, ttl: int) -> bool:
        return True

    async def incr(self, key: str) -> int:
        raw = self.store.get(key, "0")
        val = int(raw) + 1
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


def _sample_spec(name: str = "doubao_pro") -> CanvasModelSpec:
    return CanvasModelSpec(
        name=name,
        display_name="豆包 Pro",
        provider="doubao",
        model_type="llm",
        category="text",
        description="test",
        sort_order=1,
        upstream_model="ep-test",
        capabilities=("chat_completion",),
    )


@pytest.mark.asyncio
async def test_model_spec_roundtrip_dict():
    spec = _sample_spec()
    restored = model_spec_from_dict(model_spec_to_dict(spec))
    assert restored.name == spec.name
    assert restored.capabilities == spec.capabilities


@pytest.mark.asyncio
async def test_publish_and_load_model_catalog(fake_redis, monkeypatch):
    async def _ver(_domain: str) -> int:
        return 3

    monkeypatch.setattr(
        "app.services.runtime_shared_cache.get_cache_version",
        _ver,
    )

    specs = {"doubao_pro": _sample_spec(), "wanx_v1": _sample_spec("wanx_v1")}
    assert await publish_model_catalog_to_redis(specs, ver=3) is True
    assert fake_redis.lists[CATALOG_NAMES_LIST_KEY] == ["doubao_pro", "wanx_v1"]

    loaded = await load_model_catalog_from_redis()
    assert loaded is not None
    assert set(loaded.keys()) == {"doubao_pro", "wanx_v1"}
    assert loaded["doubao_pro"].upstream_model == "ep-test"


@pytest.mark.asyncio
async def test_model_catalog_version_mismatch_returns_none(fake_redis, monkeypatch):
    async def _ver(_domain: str) -> int:
        return 2

    monkeypatch.setattr(
        "app.services.runtime_shared_cache.get_cache_version",
        _ver,
    )
    await publish_model_catalog_to_redis({"a": _sample_spec("a")}, ver=1)
    assert await load_model_catalog_from_redis() is None


@pytest.mark.asyncio
async def test_publish_and_load_llm_keys(fake_redis, monkeypatch):
    async def _ver(_domain: str) -> int:
        return 5

    monkeypatch.setattr(
        "app.services.runtime_shared_cache.get_cache_version",
        _ver,
    )

    cfg = LlmKeysConfig(doubao=ProviderKeys(api_key="sk-test", api_base="https://example.com"))
    assert await publish_llm_keys_to_redis(cfg, ver=5) is True
    raw = fake_redis.store[CREDENTIAL_SNAPSHOT_KEY]
    payload = json.loads(raw)
    assert payload["ver"] == 5

    loaded = await load_llm_keys_from_redis()
    assert loaded is not None
    assert loaded.doubao.api_key == "sk-test"
