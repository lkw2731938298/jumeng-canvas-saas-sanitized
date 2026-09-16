"""ensure_runtime_catalog_fresh 与 resolve_model_configured_status 单测。"""

from __future__ import annotations

import pytest

from app.core.llm_keys import LlmKeysConfig, ProviderKeys
from app.services.credential_service import resolve_model_configured_status
from app.services.runtime_catalog_refresh import ensure_runtime_catalog_fresh


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.store.get(k) for k in keys]

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
async def test_ensure_runtime_catalog_fresh_noop_when_versions_match(monkeypatch):
    calls: list[str] = []

    async def _cred(_db):
        calls.append("cred")
        return False

    async def _model(_db):
        calls.append("model")
        return False

    monkeypatch.setattr(
        "app.services.credential_service.refresh_runtime_if_credential_version_changed",
        _cred,
    )
    monkeypatch.setattr(
        "app.services.model_catalog_runtime.refresh_runtime_model_specs_if_version_changed",
        _model,
    )

    await ensure_runtime_catalog_fresh(None)  # type: ignore[arg-type]
    assert calls == ["cred", "model"]


@pytest.mark.asyncio
async def test_resolve_model_configured_from_redis_snapshot(fake_redis, monkeypatch):
    import json

    from app.services.runtime_shared_cache import CREDENTIAL_SNAPSHOT_KEY

    cfg = LlmKeysConfig(doubao=ProviderKeys(api_key="sk-test", endpoint_id="ep-1"))
    ver = 2
    fake_redis.store["cache:ver:credential"] = str(ver)
    fake_redis.store[CREDENTIAL_SNAPSHOT_KEY] = json.dumps(
        {"ver": ver, "data": cfg.model_dump()},
        ensure_ascii=False,
    )

    async def _ver(domain: str) -> int:
        return 2

    monkeypatch.setattr("app.services.runtime_shared_cache.get_cache_version", _ver)

    ok = await resolve_model_configured_status(None, "doubao_pro")  # type: ignore[arg-type]
    assert ok is True

    missing = await resolve_model_configured_status(None, "unknown_model_xyz")  # type: ignore[arg-type]
    assert missing is False
