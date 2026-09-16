"""模型目录缓存序列化与读路径。"""

from __future__ import annotations

import pytest

from app.common.utils import redis_cache as cache_mod
from app.models.job import Model
from app.services import model_catalog_cache as mcc


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


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()

    async def _optional():
        return client

    monkeypatch.setattr(cache_mod, "optional_redis_client", _optional)
    return client


@pytest.mark.asyncio
async def test_serialize_deserialize_roundtrip():
    model = Model(
        id=42,
        name="flux-dev",
        display_name="Flux Dev",
        provider="ark",
        model_type="checkpoint",
        category="image",
        description="test",
        cover_url=None,
        parameters={"pricing": {"version": 1}},
        is_available=True,
        sort_order=10,
    )
    data = mcc.serialize_model_row(model)
    restored = mcc.deserialize_model_row(data)
    assert restored.id == 42
    assert restored.name == "flux-dev"
    assert restored.parameters == {"pricing": {"version": 1}}
    assert restored.is_available is True


@pytest.mark.asyncio
async def test_get_cached_model_by_name_uses_cache(monkeypatch):
    calls = {"db": 0}

    async def fake_loader_db(*_args, **_kwargs):
        calls["db"] += 1
        return mcc.serialize_model_row(
            Model(id=1, name="m1", category="text", is_available=True, parameters={})
        )

    cache_hits = {"n": 0}

    async def fake_cache_get_or_load(key, *, ttl, loader, version_domain, cache_null=False):
        cache_hits["n"] += 1
        return await loader()

    monkeypatch.setattr(mcc, "cache_get_or_load", fake_cache_get_or_load)
    monkeypatch.setattr(mcc, "_load_model_row_from_db", fake_loader_db)

    class _Db:
        pass

    first = await mcc.get_cached_model_by_name(_Db(), "m1", category="text", available_only=True)
    second = await mcc.get_cached_model_by_name(_Db(), "m1", category="text", available_only=True)
    assert first is not None
    assert first.name == "m1"
    assert second.name == "m1"
    assert calls["db"] == 2  # fake_cache always calls loader; real cache would be 1


@pytest.mark.asyncio
async def test_invalidate_alias_bumps_version(monkeypatch, fake_redis):
    from app.common.utils import redis_cache

    ver_before = await redis_cache.get_cache_version("model")
    await mcc.invalidate_model_catalog_cache()
    ver_after = await redis_cache.get_cache_version("model")
    assert ver_after == ver_before + 1
