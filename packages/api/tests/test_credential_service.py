"""CredentialService 双读与合并逻辑。"""

from __future__ import annotations

from app.core.llm_keys import LlmKeysConfig, ProviderKeys
from app.services.credential_service import merge_db_and_env_keys


def test_merge_db_and_env_prefers_db_api_key():
    db_cfg = LlmKeysConfig(
        dashscope=ProviderKeys(api_key="db-key", api_base="https://db.example", endpoint_id=""),
    )
    env_cfg = LlmKeysConfig(
        dashscope=ProviderKeys(api_key="env-key", api_base="https://env.example", endpoint_id=""),
    )
    merged = merge_db_and_env_keys(db_cfg, env_cfg, allow_env_fallback=True)
    assert merged.dashscope.api_key == "db-key"
    assert merged.dashscope.api_base == "https://db.example"


def test_merge_db_and_env_fills_missing_from_env():
    db_cfg = LlmKeysConfig(
        dashscope=ProviderKeys(api_key="", api_base="", endpoint_id=""),
    )
    env_cfg = LlmKeysConfig(
        dashscope=ProviderKeys(api_key="env-key", api_base="https://env.example", endpoint_id=""),
    )
    merged = merge_db_and_env_keys(db_cfg, env_cfg, allow_env_fallback=True)
    assert merged.dashscope.api_key == "env-key"
    assert merged.dashscope.api_base == "https://env.example"


def test_merge_db_only_no_env_fallback():
    db_cfg = LlmKeysConfig(
        dashscope=ProviderKeys(api_key="", api_base="https://db.example", endpoint_id=""),
    )
    env_cfg = LlmKeysConfig(
        dashscope=ProviderKeys(api_key="env-key", api_base="https://env.example", endpoint_id=""),
    )
    merged = merge_db_and_env_keys(db_cfg, env_cfg, allow_env_fallback=False)
    assert merged.dashscope.api_key == ""
    assert merged.dashscope.api_base == "https://db.example"
