"""供应商密钥服务：DB 加密存储 + Redis 短缓存 + env 双读。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..common.utils.credential_crypto import api_key_hint, decrypt_secret, encrypt_secret
from ..common.utils.redis_cache import bump_cache_version, cache_get_or_load
from ..common.utils.redis_keys import build_cache_key
from ..core.config import get_settings
from ..core.datetime_util import cst_iso_now
from ..core.llm_keys import (
    LlmKeysConfig,
    ProviderKeys,
    _build_config,
    _first_non_empty,
    _parse_env_file,
    llm_keys_file,
)
from ..models.job import Model
from ..models.model_provider import ModelProvider, ProviderCredential
from .provider_admin_ui import credential_status, profile_label, provider_ui_schema

logger = logging.getLogger(__name__)

# 进程内快照：供同步 get_llm_keys() 读取（启动 warm + 失效后下次请求前刷新）
_runtime_llm_keys: LlmKeysConfig | None = None

# 默认 credential 缓存 TTL（秒），短于模型 spec
DEFAULT_CREDENTIAL_CACHE_TTL_SEC = 60

# provider.code → LlmKeysConfig 字段名（停用时强制清空，禁止 env fallback 补回）
_PROVIDER_CFG_FIELD: dict[str, str] = {
    "doubao": "doubao",
    "ark": "ark",
    "deepseek": "deepseek",
    "dashscope": "dashscope",
    "kling": "kling",
    "vidu": "vidu",
    "runninghub": "runninghub",
    "ltx_runninghub": "ltx_runninghub",
    "openai": "openai",
    "qwen": "qwen",
    "zhipu": "zhipu",
    "moonshot": "moonshot",
    "nodyhub": "nodyhub",
    "huahu": "huahu",
    "jumengai": "jumengai",
}

_PROVIDER_SEED_META: list[dict[str, Any]] = [
    {"code": "doubao", "display_name": "豆包 / Volcengine Ark", "default_api_base": "https://ark.cn-beijing.volces.com/api/v3"},
    {"code": "ark", "display_name": "方舟 Ark", "default_api_base": "https://ark.cn-beijing.volces.com/api/v3"},
    {"code": "deepseek", "display_name": "DeepSeek", "default_api_base": "https://api.deepseek.com"},
    {"code": "dashscope", "display_name": "阿里云百炼", "default_api_base": "https://dashscope.aliyuncs.com/api/v1"},
    {"code": "kling", "display_name": "可灵", "default_api_base": "https://dashscope.aliyuncs.com/api/v1"},
    {"code": "vidu", "display_name": "Vidu", "default_api_base": "https://api.vidu.cn"},
    {"code": "runninghub", "display_name": "RunningHub", "default_api_base": "https://www.runninghub.cn"},
    {"code": "ltx_runninghub", "display_name": "RunningHub 海外版", "default_api_base": "https://www.runninghub.ai"},
    {"code": "nodyhub", "display_name": "NodyHub", "default_api_base": "https://nodyhub.com/v1"},
    {
        "code": "huahu",
        "display_name": "华狐 AI",
        "default_api_base": "https://api.example.com/v1",
    },
    {
        "code": "jumengai",
        "display_name": "聚梦 AI 网关",
        "default_api_base": "https://api.example.com/v1",
    },
    {"code": "openai", "display_name": "OpenAI", "default_api_base": "https://api.openai.com/v1"},
    {"code": "qwen", "display_name": "通义千问", "default_api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    {"code": "zhipu", "display_name": "智谱", "default_api_base": "https://open.bigmodel.cn/api/paas/v4"},
    {"code": "moonshot", "display_name": "Moonshot", "default_api_base": "https://api.moonshot.cn/v1"},
]


def credential_cache_key(provider_code: str, profile_key: str = "default") -> str:
    return build_cache_key("credential", f"{provider_code}:{profile_key}", "row")


def configured_cache_key(model_name: str) -> str:
    return build_cache_key("model", f"name:{model_name}", "configured")


def get_runtime_llm_keys_snapshot() -> LlmKeysConfig | None:
    return _runtime_llm_keys


def clear_runtime_llm_keys_snapshot() -> None:
    global _runtime_llm_keys
    _runtime_llm_keys = None


def set_runtime_llm_keys_snapshot(cfg: LlmKeysConfig) -> None:
    global _runtime_llm_keys
    _runtime_llm_keys = cfg


async def invalidate_credential_cache() -> int:
    """密钥变更后 bump 版本并清空进程内 / Redis 运行时快照。"""
    from ..core.llm_keys import refresh_llm_keys_cache
    from .runtime_shared_cache import clear_runtime_shared_cache

    clear_runtime_llm_keys_snapshot()
    refresh_llm_keys_cache()
    await clear_runtime_shared_cache()
    return await bump_cache_version("credential")


def _env_llm_keys_config() -> LlmKeysConfig:
    """从 env 文件构建配置（不经过 lru_cache，供双读合并）。"""
    import os

    from ..core.llm_keys import _ENV_MERGE_KEYS

    path = llm_keys_file()
    values = _parse_env_file(path)
    merged = {
        **values,
        **{
            k: v
            for k, v in os.environ.items()
            if k.endswith("_API_KEY")
            or k.endswith("_API_BASE")
            or k.endswith("_BASE_URL")
            or k in _ENV_MERGE_KEYS
        },
    }
    return _build_config(merged)


def merge_db_and_env_keys(db_cfg: LlmKeysConfig, env_cfg: LlmKeysConfig, *, allow_env_fallback: bool) -> LlmKeysConfig:
    """DB 优先；dual 模式下 env 补齐空字段。"""

    def pick(db_pk: ProviderKeys, env_pk: ProviderKeys) -> ProviderKeys:
        if not allow_env_fallback:
            return db_pk
        return ProviderKeys(
            api_key=db_pk.api_key or env_pk.api_key,
            api_base=db_pk.api_base or env_pk.api_base,
            endpoint_id=db_pk.endpoint_id or env_pk.endpoint_id,
        )

    return LlmKeysConfig(
        doubao=pick(db_cfg.doubao, env_cfg.doubao),
        ark=pick(db_cfg.ark, env_cfg.ark),
        deepseek=pick(db_cfg.deepseek, env_cfg.deepseek),
        dashscope=pick(db_cfg.dashscope, env_cfg.dashscope),
        kling=pick(db_cfg.kling, env_cfg.kling),
        vidu=pick(db_cfg.vidu, env_cfg.vidu),
        runninghub=pick(db_cfg.runninghub, env_cfg.runninghub),
        ltx_runninghub=pick(db_cfg.ltx_runninghub, env_cfg.ltx_runninghub),
        openai=pick(db_cfg.openai, env_cfg.openai),
        qwen=pick(db_cfg.qwen, env_cfg.qwen),
        zhipu=pick(db_cfg.zhipu, env_cfg.zhipu),
        moonshot=pick(db_cfg.moonshot, env_cfg.moonshot),
        nodyhub=pick(db_cfg.nodyhub, env_cfg.nodyhub),
        huahu=pick(db_cfg.huahu, env_cfg.huahu),
        jumengai=pick(db_cfg.jumengai, env_cfg.jumengai),
        doubao_text_model=db_cfg.doubao_text_model or env_cfg.doubao_text_model,
        doubao_image_model=db_cfg.doubao_image_model or env_cfg.doubao_image_model,
        doubao_image_endpoint_id=db_cfg.doubao_image_endpoint_id or env_cfg.doubao_image_endpoint_id,
        ark_seedance_20_model=db_cfg.ark_seedance_20_model or env_cfg.ark_seedance_20_model,
        ark_seedance_20_fast_model=db_cfg.ark_seedance_20_fast_model or env_cfg.ark_seedance_20_fast_model,
        ark_seedance_15_pro_i2v_model=db_cfg.ark_seedance_15_pro_i2v_model or env_cfg.ark_seedance_15_pro_i2v_model,
    )


async def _ensure_provider_rows(db: AsyncSession) -> None:
    result = await db.execute(select(ModelProvider))
    existing = {row.code: row for row in result.scalars().all()}
    for meta in _PROVIDER_SEED_META:
        code = meta["code"]
        if code in existing:
            continue
        db.add(
            ModelProvider(
                code=code,
                display_name=meta["display_name"],
                default_api_base=meta["default_api_base"],
                config={},
                is_enabled=True,
            )
        )


def _credential_rows_from_env(cfg: LlmKeysConfig) -> list[tuple[str, str, ProviderKeys, dict[str, str]]]:
    """(provider_code, profile_key, keys, extra_strings)

    注意：不把「回退合并」后的密钥当作独立供应商写入，避免 overwrite 时
    用 CN RunningHub Key 覆盖海外版、或用 DashScope 覆盖空的 Qwen 等。
    """
    import os

    from ..core.llm_keys import _ENV_MERGE_KEYS, _parse_env_file, llm_keys_file

    raw = {
        **_parse_env_file(llm_keys_file()),
        **{
            k: v
            for k, v in os.environ.items()
            if k.endswith("_API_KEY")
            or k.endswith("_API_BASE")
            or k.endswith("_BASE_URL")
            or k in _ENV_MERGE_KEYS
        },
    }
    rows: list[tuple[str, str, ProviderKeys, dict[str, str]]] = []

    def add(code: str, keys: ProviderKeys, *, extra: dict[str, str] | None = None) -> None:
        if not (keys.api_key or "").strip():
            return
        rows.append((code, "default", keys, extra or {}))

    doubao_extra = {
        "doubao_text_model": cfg.doubao_text_model,
        "doubao_image_model": cfg.doubao_image_model,
        "doubao_image_endpoint_id": cfg.doubao_image_endpoint_id,
        "ark_seedance_20_model": cfg.ark_seedance_20_model,
        "ark_seedance_20_fast_model": cfg.ark_seedance_20_fast_model,
        "ark_seedance_15_pro_i2v_model": cfg.ark_seedance_15_pro_i2v_model,
    }
    add("doubao", cfg.doubao, extra=doubao_extra if cfg.doubao.api_key else None)
    # Ark：仅当显式配置了 ARK_API_KEY（勿用豆包 Key 回退覆盖）
    if (raw.get("ARK_API_KEY") or "").strip():
        add("ark", cfg.ark)
    add("deepseek", cfg.deepseek)
    add("dashscope", cfg.dashscope)
    # 可灵：仅当显式配置了独立 Key
    if (raw.get("KLING_DASHSCOPE_API_KEY") or "").strip():
        add("kling", cfg.kling)
    add("vidu", cfg.vidu)
    add("runninghub", cfg.runninghub)
    # 海外版：仅当显式配置了 LTX/海外 Key，禁止用 CN Key 回退覆盖 DB
    if (raw.get("LTX_I2V_RUNNINGHUB_API_KEY") or "").strip():
        add("ltx_runninghub", cfg.ltx_runninghub)
    add("nodyhub", cfg.nodyhub)
    add("huahu", cfg.huahu)
    add("jumengai", cfg.jumengai)
    add("openai", cfg.openai)
    # Qwen：仅显式 QWEN_API_KEY，禁止用 DashScope 回退覆盖
    if (raw.get("QWEN_API_KEY") or "").strip():
        add("qwen", cfg.qwen)
    add("zhipu", cfg.zhipu)
    add("moonshot", cfg.moonshot)
    return rows


async def seed_provider_credentials_from_env(db: AsyncSession) -> dict[str, int]:
    """首次启动：从 llm-keys.env 加密落库（已有行不覆盖，避免覆盖未来后台配置）。"""
    return await resync_provider_credentials_from_env(db, overwrite=False)


async def resync_provider_credentials_from_env(
    db: AsyncSession,
    *,
    overwrite: bool = False,
) -> dict[str, int]:
    """从 llm-keys.env 加密写入 provider_credentials。

    overwrite=False：仅插入缺失行（启动 seed 默认）。
    overwrite=True：用当前加密密钥重写已有行（密钥轮换 / 解密失败修复）。
    """
    await _ensure_provider_rows(db)
    env_cfg = _env_llm_keys_config()
    added = 0
    updated = 0
    skipped = 0

    result = await db.execute(select(ProviderCredential))
    existing = {(row.provider_code, row.profile_key): row for row in result.scalars().all()}

    for provider_code, profile_key, keys, extra in _credential_rows_from_env(env_cfg):
        slot = (provider_code, profile_key)
        endpoint_id = keys.endpoint_id
        if provider_code == "doubao" and not endpoint_id:
            endpoint_id = _first_non_empty(extra.get("doubao_text_model", ""))
        secrets_blob = (
            encrypt_secret(json.dumps(extra, ensure_ascii=False)) if extra else None
        )
        row = existing.get(slot)
        if row is None:
            db.add(
                ProviderCredential(
                    provider_code=provider_code,
                    profile_key=profile_key,
                    api_key_enc=encrypt_secret(keys.api_key),
                    api_key_hint=api_key_hint(keys.api_key),
                    endpoint_id=endpoint_id or None,
                    api_base=keys.api_base or None,
                    secrets_enc=secrets_blob,
                    is_active=True,
                )
            )
            added += 1
            continue
        if not overwrite:
            skipped += 1
            continue
        # 用当前 CREDENTIAL_ENCRYPTION_KEY / jwt_secret 重新加密覆盖
        row.api_key_enc = encrypt_secret(keys.api_key)
        row.api_key_hint = api_key_hint(keys.api_key)
        row.endpoint_id = endpoint_id or row.endpoint_id
        row.api_base = keys.api_base or row.api_base
        if secrets_blob is not None:
            row.secrets_enc = secrets_blob
        row.is_active = True
        updated += 1

    if added or updated:
        await db.flush()
        await invalidate_credential_cache()
        logger.info(
            "Resynced provider credentials from env added=%s updated=%s skipped=%s",
            added,
            updated,
            skipped,
        )
    return {"added": added, "updated": updated, "skipped": skipped}


def _provider_keys_from_row(row: ProviderCredential, provider: ModelProvider | None) -> ProviderKeys:
    api_key = ""
    if row.api_key_enc:
        try:
            api_key = decrypt_secret(row.api_key_enc)
        except ValueError:
            # 加密密钥轮换或历史脏数据：跳过该行，由 env/Redis 快照或 dual 回退补齐
            logger.warning(
                "skip undecryptable provider credential provider=%s profile=%s",
                row.provider_code,
                row.profile_key,
            )
    api_base = (row.api_base or (provider.default_api_base if provider else "") or "").strip()
    endpoint_id = (row.endpoint_id or "").strip()
    return ProviderKeys(api_key=api_key, api_base=api_base, endpoint_id=endpoint_id)


def _apply_doubao_extras(cfg: LlmKeysConfig, row: ProviderCredential | None) -> None:
    if not row or not row.secrets_enc:
        return
    try:
        extra = json.loads(decrypt_secret(row.secrets_enc))
    except Exception:
        return
    if not isinstance(extra, dict):
        return
    cfg.doubao_text_model = extra.get("doubao_text_model") or cfg.doubao_text_model
    cfg.doubao_image_model = extra.get("doubao_image_model") or cfg.doubao_image_model
    cfg.doubao_image_endpoint_id = extra.get("doubao_image_endpoint_id") or cfg.doubao_image_endpoint_id
    cfg.ark_seedance_20_model = extra.get("ark_seedance_20_model") or cfg.ark_seedance_20_model
    cfg.ark_seedance_20_fast_model = extra.get("ark_seedance_20_fast_model") or cfg.ark_seedance_20_fast_model
    cfg.ark_seedance_15_pro_i2v_model = extra.get("ark_seedance_15_pro_i2v_model") or cfg.ark_seedance_15_pro_i2v_model


async def load_llm_keys_config_from_db(db: AsyncSession) -> LlmKeysConfig:
    """从 DB 解密组装 LlmKeysConfig。"""
    providers_result = await db.execute(select(ModelProvider).filter(ModelProvider.is_enabled.is_(True)))
    providers = {p.code: p for p in providers_result.scalars().all()}

    creds_result = await db.execute(select(ProviderCredential).filter(ProviderCredential.is_active.is_(True)))
    credentials = list(creds_result.scalars().all())

    cfg = LlmKeysConfig()
    doubao_row: ProviderCredential | None = None

    for row in credentials:
        provider = providers.get(row.provider_code)
        keys = _provider_keys_from_row(row, provider)
        if row.provider_code == "doubao":
            cfg.doubao = keys
            doubao_row = row
        elif row.provider_code == "ark":
            cfg.ark = keys
        elif row.provider_code == "deepseek":
            cfg.deepseek = keys
        elif row.provider_code == "dashscope":
            cfg.dashscope = keys
        elif row.provider_code == "kling":
            cfg.kling = keys
        elif row.provider_code == "vidu":
            cfg.vidu = keys
        elif row.provider_code == "runninghub":
            cfg.runninghub = keys
        elif row.provider_code == "ltx_runninghub":
            cfg.ltx_runninghub = keys
        elif row.provider_code == "nodyhub":
            cfg.nodyhub = keys
        elif row.provider_code == "huahu":
            cfg.huahu = keys
        elif row.provider_code == "jumengai":
            cfg.jumengai = keys
        elif row.provider_code == "openai":
            cfg.openai = keys
        elif row.provider_code == "qwen":
            cfg.qwen = keys
        elif row.provider_code == "zhipu":
            cfg.zhipu = keys
        elif row.provider_code == "moonshot":
            cfg.moonshot = keys

    _apply_doubao_extras(cfg, doubao_row)
    return cfg


async def list_disabled_provider_codes(db: AsyncSession) -> set[str]:
    """已停用的供应商 code（管理端「启用供应商」关闭）。"""
    result = await db.execute(
        select(ModelProvider.code).filter(ModelProvider.is_enabled.is_(False))
    )
    return {str(code).strip() for code in result.scalars().all() if str(code).strip()}


def apply_disabled_providers(cfg: LlmKeysConfig, disabled: set[str]) -> LlmKeysConfig:
    """停用供应商：强制清空运行时密钥与相关模型覆盖项，禁止 env fallback 补回。"""
    if not disabled:
        return cfg
    out = cfg.model_copy(deep=True)
    for code in disabled:
        field = _PROVIDER_CFG_FIELD.get(code)
        if field and hasattr(out, field):
            setattr(out, field, ProviderKeys())
        # 豆包 / 方舟额外模型覆盖也随供应商停用清掉
        if code == "doubao":
            out.doubao_text_model = ""
            out.doubao_image_model = ""
            out.doubao_image_endpoint_id = ""
        if code == "ark":
            out.ark_seedance_20_model = ""
            out.ark_seedance_20_fast_model = ""
            out.ark_seedance_15_pro_i2v_model = ""
    return out


_runtime_credential_ver: int = -1


async def refresh_runtime_if_credential_version_changed(db: AsyncSession) -> bool:
    """credential 域版本变化时刷新快照（优先 Redis，miss 时回源 DB 并再发布）。"""
    global _runtime_credential_ver
    from ..common.utils.redis_cache import get_cache_version
    from ..core.llm_keys import refresh_llm_keys_cache
    from .runtime_shared_cache import load_llm_keys_from_redis

    ver = await get_cache_version("credential")
    if _runtime_llm_keys is not None and ver == _runtime_credential_ver:
        return False

    redis_cfg = await load_llm_keys_from_redis()
    if redis_cfg is not None:
        set_runtime_llm_keys_snapshot(redis_cfg)
        _runtime_credential_ver = ver
        refresh_llm_keys_cache()
        logger.info("Runtime LLM keys refreshed from Redis, ver=%s", ver)
        return True

    await warm_runtime_llm_keys(db)
    refresh_llm_keys_cache()
    return True


async def list_model_providers_for_admin(db: AsyncSession) -> list[dict[str, Any]]:
    """管理端供应商列表（含密钥状态、UI 模板、引用模型数）。"""
    await _ensure_provider_rows(db)
    providers_result = await db.execute(
        select(ModelProvider).order_by(ModelProvider.sort_order.asc(), ModelProvider.code.asc())
    )
    providers = list(providers_result.scalars().all())

    creds_result = await db.execute(
        select(ProviderCredential).filter(ProviderCredential.is_active.is_(True))
    )
    creds_by_provider: dict[str, list[ProviderCredential]] = {}
    for row in creds_result.scalars().all():
        creds_by_provider.setdefault(row.provider_code, []).append(row)

    # 引用该供应商的模型数量（未软删）
    models_result = await db.execute(select(Model))
    model_count_by_provider: dict[str, int] = {}
    for m in models_result.scalars().all():
        params = m.parameters if isinstance(m.parameters, dict) else {}
        if params.get("adminSoftDeleted"):
            continue
        code = (m.provider or "").strip().lower()
        if not code:
            continue
        model_count_by_provider[code] = model_count_by_provider.get(code, 0) + 1

    items: list[dict[str, Any]] = []
    for provider in providers:
        creds = creds_by_provider.get(provider.code, [])
        has_key = any((c.api_key_enc or "").strip() for c in creds)
        default_cred = next((c for c in creds if c.profile_key == "default" and c.api_key_enc), None)
        if default_cred is None:
            default_cred = next((c for c in creds if c.api_key_enc), None)

        cfg = provider.config if isinstance(provider.config, dict) else {}
        tests = cfg.get("credentialTests") if isinstance(cfg.get("credentialTests"), dict) else {}
        default_test = tests.get("default") if isinstance(tests.get("default"), dict) else None
        last_test_ok: bool | None = None
        last_tested_at: str | None = None
        last_test_message: str | None = None
        if default_test is not None:
            last_test_ok = bool(default_test.get("ok"))
            last_tested_at = str(default_test.get("at") or "") or None
            last_test_message = str(default_test.get("message") or "") or None
        elif tests:
            for _pk, t in tests.items():
                if isinstance(t, dict) and t.get("at"):
                    last_test_ok = bool(t.get("ok"))
                    last_tested_at = str(t.get("at") or "") or None
                    last_test_message = str(t.get("message") or "") or None
                    break

        items.append(
            {
                "code": provider.code,
                "display_name": provider.display_name or provider.code,
                "default_api_base": provider.default_api_base,
                "is_enabled": bool(provider.is_enabled),
                "sort_order": int(provider.sort_order or 0),
                "has_credential": has_key,
                "credential_profiles": [c.profile_key for c in creds if c.api_key_enc],
                "api_key_hint": default_cred.api_key_hint if default_cred else None,
                "model_count": int(model_count_by_provider.get(provider.code, 0)),
                "status": credential_status(has_credential=has_key, last_test_ok=last_test_ok),
                "last_test_ok": last_test_ok,
                "last_tested_at": last_tested_at,
                "last_test_message": last_test_message,
                "default_credential_updated_at": default_cred.updated_at if default_cred else None,
                "ui": provider_ui_schema(provider.code),
            }
        )
    return items


async def list_provider_referenced_models(
    db: AsyncSession,
    provider_code: str,
) -> list[dict[str, Any]]:
    """列出引用该供应商的目录模型（供密钥抽屉展示）。"""
    code = (provider_code or "").strip().lower()
    result = await db.execute(
        select(Model)
        .filter(Model.provider == code)
        .order_by(Model.sort_order.asc(), Model.display_name.asc())
    )
    rows: list[dict[str, Any]] = []
    for m in result.scalars().all():
        params = m.parameters if isinstance(m.parameters, dict) else {}
        if params.get("adminSoftDeleted"):
            continue
        rows.append(
            {
                "id": str(m.id),
                "name": m.name,
                "display_name": m.display_name or m.name,
                "category": m.category,
                "is_available": bool(m.is_available),
            }
        )
    return rows


def _read_credential_tests(provider: ModelProvider | None) -> dict[str, Any]:
    if provider is None or not isinstance(provider.config, dict):
        return {}
    raw = provider.config.get("credentialTests")
    return dict(raw) if isinstance(raw, dict) else {}


async def record_credential_test_result(
    db: AsyncSession,
    provider_code: str,
    profile_key: str,
    *,
    ok: bool,
    message: str,
    tested_by: int | None = None,
) -> dict[str, Any]:
    """将测通结果写入 model_providers.config.credentialTests（不存密钥明文）。"""
    result = await db.execute(select(ModelProvider).filter(ModelProvider.code == provider_code))
    provider = result.scalar_one_or_none()
    if provider is None:
        raise LookupError(provider_code)
    cfg = dict(provider.config) if isinstance(provider.config, dict) else {}
    tests = dict(cfg.get("credentialTests") or {}) if isinstance(cfg.get("credentialTests"), dict) else {}
    entry = {
        "ok": bool(ok),
        "message": (message or "")[:500],
        "at": cst_iso_now(),
        "testedBy": tested_by,
        "profileKey": profile_key,
    }
    tests[profile_key] = entry
    cfg["credentialTests"] = tests
    provider.config = cfg
    flag_modified(provider, "config")
    await db.flush()
    return entry


async def list_provider_credentials_masked(
    db: AsyncSession,
    provider_code: str,
) -> list[dict[str, Any]]:
    """返回掩码后的 credential 列表（不含明文），含 Profile 中文名与最近测通。"""
    provider_result = await db.execute(select(ModelProvider).filter(ModelProvider.code == provider_code))
    provider = provider_result.scalar_one_or_none()
    tests = _read_credential_tests(provider)

    result = await db.execute(
        select(ProviderCredential)
        .filter(ProviderCredential.provider_code == provider_code)
        .order_by(ProviderCredential.profile_key.asc())
    )
    rows = list(result.scalars().all())
    out: list[dict[str, Any]] = []
    for row in rows:
        endpoint = (row.endpoint_id or "").strip()
        test = tests.get(row.profile_key) if isinstance(tests.get(row.profile_key), dict) else None
        out.append(
            {
                "profile_key": row.profile_key,
                "profile_label": profile_label(provider_code, row.profile_key),
                "api_key_hint": row.api_key_hint,
                "endpoint_id_masked": _mask_endpoint(endpoint),
                "api_base": row.api_base,
                "is_active": bool(row.is_active),
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
                "last_test_ok": bool(test["ok"]) if test and "ok" in test else None,
                "last_tested_at": (str(test.get("at") or "") or None) if test else None,
                "last_test_message": (str(test.get("message") or "") or None) if test else None,
            }
        )
    return out


def _mask_endpoint(endpoint_id: str) -> str:
    if not endpoint_id:
        return ""
    if len(endpoint_id) <= 8:
        return endpoint_id[:2] + "****"
    return endpoint_id[:4] + "****" + endpoint_id[-4:]


async def patch_model_provider_row(
    db: AsyncSession,
    provider_code: str,
    *,
    default_api_base: str | None = None,
    is_enabled: bool | None = None,
) -> ModelProvider:
    result = await db.execute(select(ModelProvider).filter(ModelProvider.code == provider_code))
    provider = result.scalar_one_or_none()
    if not provider:
        raise LookupError(provider_code)
    if default_api_base is not None:
        provider.default_api_base = default_api_base.strip() or None
    if is_enabled is not None:
        provider.is_enabled = is_enabled
    await db.flush()
    return provider


async def after_provider_enablement_changed(db: AsyncSession) -> None:
    """启停供应商后：失效缓存并立即 warm，使 API/Worker 立刻停用/恢复密钥。"""
    global _runtime_credential_ver
    from ..common.utils.redis_cache import get_cache_version
    from ..core.llm_keys import refresh_llm_keys_cache

    await invalidate_credential_cache()
    await warm_runtime_llm_keys(db)
    refresh_llm_keys_cache()
    _runtime_credential_ver = await get_cache_version("credential")


async def upsert_provider_credential(
    db: AsyncSession,
    *,
    provider_code: str,
    profile_key: str,
    api_key: str | None = None,
    api_base: str | None = None,
    endpoint_id: str | None = None,
    updated_by: int | None = None,
) -> ProviderCredential:
    """写入或轮换供应商密钥（加密落库）；未传 api_key 时保留已有加密值。"""
    await _ensure_provider_rows(db)
    provider_result = await db.execute(select(ModelProvider).filter(ModelProvider.code == provider_code))
    if provider_result.scalar_one_or_none() is None:
        raise LookupError(provider_code)

    result = await db.execute(
        select(ProviderCredential).filter(
            ProviderCredential.provider_code == provider_code,
            ProviderCredential.profile_key == profile_key,
        )
    )
    row = result.scalar_one_or_none()
    key = (api_key or "").strip()

    if row is None:
        if not key:
            raise ValueError("新建凭证必须提供 api_key")
        row = ProviderCredential(
            provider_code=provider_code,
            profile_key=profile_key,
            is_active=True,
        )
        db.add(row)
        row.api_key_enc = encrypt_secret(key)
        row.api_key_hint = api_key_hint(key)
    elif not key:
        # 编辑已有凭证且未输入新密钥：不轮换 api_key_enc
        pass
    else:
        row.api_key_enc = encrypt_secret(key)
        row.api_key_hint = api_key_hint(key)

    if api_base is not None:
        row.api_base = api_base.strip() or None
    if endpoint_id is not None:
        row.endpoint_id = endpoint_id.strip() or None
    row.is_active = True
    row.updated_by = updated_by
    await db.flush()
    await invalidate_credential_cache()
    await warm_runtime_llm_keys(db)
    from ..core.llm_keys import refresh_llm_keys_cache

    refresh_llm_keys_cache()
    global _runtime_credential_ver
    from ..common.utils.redis_cache import get_cache_version

    _runtime_credential_ver = await get_cache_version("credential")
    return row


async def patch_provider_credential(
    db: AsyncSession,
    *,
    provider_code: str,
    profile_key: str,
    api_key: str | None = None,
    api_base: str | None = None,
    endpoint_id: str | None = None,
    updated_by: int | None = None,
) -> ProviderCredential:
    """部分更新供应商凭证；仅更新请求体中显式传入的字段。"""
    await _ensure_provider_rows(db)
    provider_result = await db.execute(select(ModelProvider).filter(ModelProvider.code == provider_code))
    if provider_result.scalar_one_or_none() is None:
        raise LookupError(provider_code)

    result = await db.execute(
        select(ProviderCredential).filter(
            ProviderCredential.provider_code == provider_code,
            ProviderCredential.profile_key == profile_key,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise LookupError(f"{provider_code}:{profile_key}")

    key = (api_key or "").strip() if api_key is not None else None
    if key:
        row.api_key_enc = encrypt_secret(key)
        row.api_key_hint = api_key_hint(key)
    if api_base is not None:
        row.api_base = api_base.strip() or None
    if endpoint_id is not None:
        row.endpoint_id = endpoint_id.strip() or None
    row.is_active = True
    row.updated_by = updated_by
    await db.flush()
    await invalidate_credential_cache()
    await warm_runtime_llm_keys(db)
    from ..core.llm_keys import refresh_llm_keys_cache

    refresh_llm_keys_cache()
    global _runtime_credential_ver
    from ..common.utils.redis_cache import get_cache_version

    _runtime_credential_ver = await get_cache_version("credential")
    return row


async def warm_runtime_llm_keys(db: AsyncSession) -> LlmKeysConfig:
    """加载 DB 密钥，发布 Redis 共享缓存，并写入本进程 L1 快照。"""
    global _runtime_credential_ver
    from ..common.utils.redis_cache import get_cache_version
    from .runtime_shared_cache import publish_llm_keys_to_redis

    settings = get_settings()
    source = (settings.llm_keys_source or "db").strip().lower()
    if source == "env":
        merged = _env_llm_keys_config()
    elif source == "db":
        db_cfg = await load_llm_keys_config_from_db(db)
        # fallback=true：DB 某供应商密钥为空/解密失败时用 llm-keys.env 补齐
        if settings.llm_keys_fallback:
            merged = merge_db_and_env_keys(
                db_cfg, _env_llm_keys_config(), allow_env_fallback=True
            )
        else:
            merged = db_cfg
    else:
        env_cfg = _env_llm_keys_config()
        db_cfg = await load_llm_keys_config_from_db(db)
        merged = merge_db_and_env_keys(db_cfg, env_cfg, allow_env_fallback=settings.llm_keys_fallback)

    # 管理端停用的供应商：即使 LLM_KEYS_FALLBACK 也不能从 env 补密钥
    disabled = await list_disabled_provider_codes(db)
    if disabled:
        merged = apply_disabled_providers(merged, disabled)
        logger.info("Runtime LLM keys cleared for disabled providers: %s", sorted(disabled))

    ver = await get_cache_version("credential")
    await publish_llm_keys_to_redis(merged, ver=ver)
    set_runtime_llm_keys_snapshot(merged)
    _runtime_credential_ver = ver
    return merged


async def resolve_model_configured_status(
    db: AsyncSession,
    model_name: str,
    *,
    provider: str | None = None,
) -> bool:
    """判断模型是否已配置密钥：优先 Redis 整包快照，miss 时按供应商读 Redis/DB 缓存。"""
    from ..core.llm_keys import (
        ARK_IMAGE_MODEL_IDS,
        ProviderKeys,
        get_provider_for_model,
        is_model_configured,
        is_provider_configured,
    )
    from ..core.model_registry import get_model_spec
    from .runtime_shared_cache import load_llm_keys_from_redis

    if provider == "comfyui":
        return True

    redis_cfg = await load_llm_keys_from_redis()
    if redis_cfg is not None:
        if provider:
            return is_provider_configured(provider, model_id=model_name, cfg=redis_cfg)
        return is_model_configured(model_name, provider=provider, cfg=redis_cfg)

    resolved_provider = provider
    if not resolved_provider:
        try:
            resolved_provider = get_provider_for_model(model_name)
        except ValueError:
            return False
    if resolved_provider == "comfyui":
        return True

    async def _load_keys(provider_code: str) -> ProviderKeys | None:
        return await get_cached_provider_keys(db, provider_code, profile_key="default")

    creds: ProviderKeys | None
    if resolved_provider == "runninghub":
        spec = get_model_spec(model_name)
        if spec and spec.parameters_extra.get("use_ltx_key"):
            creds = await _load_keys("ltx_runninghub")
            if not creds or not creds.api_key:
                creds = await _load_keys("runninghub")
        else:
            creds = await _load_keys("runninghub")
    elif resolved_provider == "ltx_runninghub":
        # RunningHub 海外版：优先读海外密钥，未配置时回退 CN
        creds = await _load_keys("ltx_runninghub")
        if not creds or not creds.api_key:
            creds = await _load_keys("runninghub")
    else:
        creds = await _load_keys(resolved_provider)

    if resolved_provider == "ark" and (not creds or not creds.api_key) and model_name in ARK_IMAGE_MODEL_IDS:
        creds = await _load_keys("doubao")

    if not creds or not creds.api_key:
        return False

    if model_name == "doubao_pro":
        doubao_keys = await _load_keys("doubao")
        endpoint_ok = bool((doubao_keys and doubao_keys.endpoint_id) or creds.endpoint_id)
        if endpoint_ok:
            return True
        return is_model_configured(model_name, provider=resolved_provider)

    return True


async def test_provider_credential(
    db: AsyncSession,
    provider_code: str,
    profile_key: str = "default",
    *,
    tested_by: int | None = None,
) -> dict[str, Any]:
    """连通性测试：校验密钥可解密且非空，并落库测通结果。"""
    keys = await get_cached_provider_keys(db, provider_code, profile_key=profile_key, ttl_sec=5)
    if not keys or not keys.api_key:
        result = {"ok": False, "message": "未配置 API Key", "api_base": None, "has_endpoint_id": False}
    else:
        result = {
            "ok": True,
            "message": "密钥已配置且可解密",
            "api_base": keys.api_base or None,
            "has_endpoint_id": bool(keys.endpoint_id),
        }
    try:
        entry = await record_credential_test_result(
            db,
            provider_code,
            profile_key,
            ok=bool(result["ok"]),
            message=str(result["message"]),
            tested_by=tested_by,
        )
        result["tested_at"] = entry.get("at")
    except LookupError:
        pass
    return result


async def get_cached_provider_keys(
    db: AsyncSession,
    provider_code: str,
    *,
    profile_key: str = "default",
    ttl_sec: int = DEFAULT_CREDENTIAL_CACHE_TTL_SEC,
) -> ProviderKeys | None:
    """按供应商读密钥（Redis 短缓存）。"""

    async def loader() -> dict | None:
        result = await db.execute(
            select(ProviderCredential, ModelProvider)
            .join(ModelProvider, ModelProvider.code == ProviderCredential.provider_code, isouter=True)
            .filter(
                ProviderCredential.provider_code == provider_code,
                ProviderCredential.profile_key == profile_key,
                ProviderCredential.is_active.is_(True),
            )
        )
        row = result.first()
        if not row:
            return None
        cred, provider = row
        # 供应商已停用：不返回密钥（与「启用供应商」开关语义一致）
        if provider is not None and not bool(provider.is_enabled):
            return None
        keys = _provider_keys_from_row(cred, provider)
        return {"api_key": keys.api_key, "api_base": keys.api_base, "endpoint_id": keys.endpoint_id}

    data = await cache_get_or_load(
        credential_cache_key(provider_code, profile_key),
        ttl=ttl_sec,
        loader=loader,
        version_domain="credential",
    )
    if not data:
        return None
    return ProviderKeys(
        api_key=data.get("api_key") or "",
        api_base=data.get("api_base") or "",
        endpoint_id=data.get("endpoint_id") or "",
    )
