"""get_llm_keys 双读：DB 快照优先。"""

from __future__ import annotations

from app.core.llm_keys import LlmKeysConfig, ProviderKeys, get_llm_keys, refresh_llm_keys_cache
from app.services.credential_service import clear_runtime_llm_keys_snapshot, set_runtime_llm_keys_snapshot


def test_get_llm_keys_uses_runtime_snapshot(monkeypatch):
    monkeypatch.setenv("LLM_KEYS_SOURCE", "dual")
    monkeypatch.setenv("MODEL_CATALOG_SOURCE", "dual")
    from app.core.config import get_settings

    get_settings.cache_clear()
    refresh_llm_keys_cache()
    clear_runtime_llm_keys_snapshot()

    snapshot = LlmKeysConfig(
        dashscope=ProviderKeys(api_key="snapshot-key", api_base="https://snap.example", endpoint_id=""),
    )
    set_runtime_llm_keys_snapshot(snapshot)
    refresh_llm_keys_cache()

    cfg = get_llm_keys()
    assert cfg.dashscope.api_key == "snapshot-key"

    clear_runtime_llm_keys_snapshot()
    refresh_llm_keys_cache()
