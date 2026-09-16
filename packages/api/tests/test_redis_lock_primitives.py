"""Redis 标记锁 / Token 锁行为契约（不依赖真实 Redis）。"""

from __future__ import annotations

import pytest

from app.common.utils import redis_mark_lock as mark_mod
from app.common.utils import redis_token_mutex as token_mod
from app.services.credit_operation_lock import (
    MODEL_SUBMIT_LOCK_TTL_SEC,
    claim_job_upstream_submit,
    clear_job_upstream_submit,
    model_submit_lock_key,
    seal_job_upstream_submit,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def delete(self, *keys: str) -> int:
        n = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                self.ttl.pop(key, None)
                n += 1
        return n

    async def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        if self.store.get(key) == token:
            del self.store[key]
            self.ttl.pop(key, None)
            return 1
        return 0


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()

    async def _get_redis():
        return client

    monkeypatch.setattr(mark_mod, "get_redis", _get_redis)
    monkeypatch.setattr(token_mod, "get_redis", _get_redis)
    from app.common.utils import redis_store

    monkeypatch.setattr(redis_store, "get_redis", _get_redis)
    monkeypatch.setattr("app.services.credit_operation_lock.get_redis", _get_redis, raising=False)
    return client


@pytest.mark.asyncio
async def test_mark_claim_survives_after_failed_business(fake_redis):
    """标记锁：claim 后模拟业务异常，key 必须仍在。"""
    key = "lock:model:submit:999"
    assert await mark_mod.mark_claim(key, ttl_sec=3600) is True
    assert await mark_mod.mark_exists(key) is True
    # 模拟失败路径：不 delete
    assert key in fake_redis.store
    assert fake_redis.store[key] == mark_mod.MARK_LOCK_VALUE


@pytest.mark.asyncio
async def test_mark_seal_refreshes_value(fake_redis):
    key = "lock:credit:refund:1"
    await mark_mod.mark_claim(key, ttl_sec=100)
    await mark_mod.mark_seal(key, ttl_sec=200)
    assert fake_redis.store[key] == "1"
    assert fake_redis.ttl[key] == 200


@pytest.mark.asyncio
async def test_concurrent_mark_claim_only_one_wins(fake_redis):
    key = "lock:payment:fulfill:order-1"
    first = await mark_mod.mark_claim(key, ttl_sec=60)
    second = await mark_mod.mark_claim(key, ttl_sec=60)
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_mark_peek_returns_none_without_redis(monkeypatch):
    async def _no_redis():
        return None

    monkeypatch.setattr(mark_mod, "get_redis", _no_redis)
    assert await mark_mod.mark_peek_exists("lock:payment:fulfill:x") is None


@pytest.mark.asyncio
async def test_mark_delete_clears_key(fake_redis):
    key = "lock:generation:42"
    await mark_mod.mark_claim(key, ttl_sec=60)
    await mark_mod.mark_delete(key)
    assert key not in fake_redis.store


@pytest.mark.asyncio
async def test_token_mutex_releases_on_exit(fake_redis):
    key = "lock:worker:execute:7"
    async with token_mod.token_mutex(key, ttl_sec=60):
        assert key in fake_redis.store
        assert len(fake_redis.store[key]) == 32  # uuid hex
    assert key not in fake_redis.store


@pytest.mark.asyncio
async def test_upstream_submit_wrapper_uses_frozen_key(fake_redis):
    job_id = 10575
    assert await claim_job_upstream_submit(job_id) is True
    assert fake_redis.store[model_submit_lock_key(job_id)] == "1"
    await seal_job_upstream_submit(job_id)
    assert fake_redis.ttl[model_submit_lock_key(job_id)] == MODEL_SUBMIT_LOCK_TTL_SEC
    await clear_job_upstream_submit(job_id)
    assert model_submit_lock_key(job_id) not in fake_redis.store
