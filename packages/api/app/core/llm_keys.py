"""
Load LLM API keys from a dedicated env file (config/llm-keys.env).
Keys must never be exposed to the frontend.

Provider layout mirrors main-platform MODELS_AND_API_KEYS.md.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from .model_registry import ARK_IMAGE_MODEL_IDS, MODEL_PROVIDER_MAP, get_model_spec
from .paths import find_project_root


def _default_llm_keys_path() -> Path:
    root = find_project_root(Path(__file__))
    candidate = root / "config" / "llm-keys.env"
    if candidate.is_file() or (root / "packages").is_dir():
        return candidate
    return Path("/config/llm-keys.env")


DEFAULT_LLM_KEYS_FILE = _default_llm_keys_path()


class ProviderKeys(BaseModel):
    api_key: str = ""
    api_base: str = ""
    endpoint_id: str = ""


class LlmKeysConfig(BaseModel):
    doubao: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://ark.cn-beijing.volces.com/api/v3")
    )
    ark: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://ark.cn-beijing.volces.com/api/v3")
    )
    deepseek: ProviderKeys = Field(default_factory=lambda: ProviderKeys(api_base="https://api.deepseek.com"))
    dashscope: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://dashscope.aliyuncs.com/api/v1")
    )
    kling: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://dashscope.aliyuncs.com/api/v1")
    )
    vidu: ProviderKeys = Field(default_factory=lambda: ProviderKeys(api_base="https://api.vidu.cn"))
    runninghub: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://www.runninghub.cn")
    )
    # RunningHub 海外版（AI 站 www.runninghub.ai）：全能图片 Pro/G 与 LTX 图生视频
    ltx_runninghub: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://www.runninghub.ai")
    )
    openai: ProviderKeys = Field(default_factory=lambda: ProviderKeys(api_base="https://api.openai.com/v1"))
    qwen: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://dashscope.aliyuncs.com/compatible-mode/v1")
    )
    zhipu: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://open.bigmodel.cn/api/paas/v4")
    )
    moonshot: ProviderKeys = Field(default_factory=lambda: ProviderKeys(api_base="https://api.moonshot.cn/v1"))
    nodyhub: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://nodyhub.com/v1")
    )
    # 华狐 AI 聚合平台（Ark 兼容 Seedance 网关）
    huahu: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://api.example.com/v1")
    )
    # 聚梦 AI 网关（OpenAI 兼容 /v1/videos，去字幕精准版等）
    jumengai: ProviderKeys = Field(
        default_factory=lambda: ProviderKeys(api_base="https://api.example.com/v1")
    )
    doubao_text_model: str = ""
    doubao_image_model: str = ""
    doubao_image_endpoint_id: str = ""
    ark_seedance_20_model: str = ""
    ark_seedance_20_fast_model: str = ""
    ark_seedance_15_pro_i2v_model: str = ""


def llm_keys_file() -> Path:
    custom = os.getenv("LLM_KEYS_FILE", "").strip()
    if custom:
        return Path(custom)
    return DEFAULT_LLM_KEYS_FILE


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _first_non_empty(*values: str) -> str:
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""


def _build_config(values: dict[str, str]) -> LlmKeysConfig:
    doubao_key = values.get("DOUBAO_API_KEY", "")
    ark_key = _first_non_empty(values.get("ARK_API_KEY", ""), doubao_key)
    dashscope_key = values.get("DASHSCOPE_API_KEY", "")
    kling_key = _first_non_empty(values.get("KLING_DASHSCOPE_API_KEY", ""), dashscope_key)
    rh_key = _first_non_empty(
        values.get("RUNNINGHUB_API_KEY", ""),
        values.get("RUNNINGHUB_RHUB_API_KEY", ""),
    )
    ltx_key = _first_non_empty(values.get("LTX_I2V_RUNNINGHUB_API_KEY", ""), rh_key)

    doubao_base = values.get("DOUBAO_API_BASE") or values.get("DOUBAO_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3"
    ark_base = values.get("ARK_API_BASE") or values.get("ARK_BASE_URL") or doubao_base

    return LlmKeysConfig(
        doubao=ProviderKeys(
            api_key=doubao_key,
            api_base=doubao_base,
            endpoint_id=values.get("DOUBAO_ENDPOINT_ID", "")
            or values.get("DOUBAO_SUBJECT_EXTRACT_MODEL", "")
            or values.get("DOUBAO_CHAT_MODEL", ""),
        ),
        ark=ProviderKeys(api_key=ark_key, api_base=ark_base),
        deepseek=ProviderKeys(
            api_key=values.get("DEEPSEEK_API_KEY", ""),
            api_base=values.get("DEEPSEEK_API_BASE", "") or values.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        ),
        dashscope=ProviderKeys(
            api_key=dashscope_key,
            api_base=values.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"),
        ),
        kling=ProviderKeys(
            api_key=kling_key,
            api_base=values.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"),
        ),
        vidu=ProviderKeys(
            api_key=values.get("VIDU_API_KEY", ""),
            api_base=values.get("VIDU_API_BASE", "https://api.vidu.cn"),
        ),
        runninghub=ProviderKeys(
            api_key=rh_key,
            api_base=values.get("RUNNINGHUB_API_BASE", "https://www.runninghub.cn"),
        ),
        ltx_runninghub=ProviderKeys(
            api_key=ltx_key,
            # RunningHub 海外版地址（AI 站）：独立于 CN 站 RUNNINGHUB_API_BASE
            api_base=(
                values.get("RUNNINGHUB_OVERSEAS_API_BASE")
                or "https://www.runninghub.ai"
            ),
        ),
        openai=ProviderKeys(
            api_key=values.get("OPENAI_API_KEY", ""),
            api_base=values.get("OPENAI_API_BASE", "https://api.openai.com/v1"),
        ),
        qwen=ProviderKeys(
            api_key=values.get("QWEN_API_KEY", "") or dashscope_key,
            api_base=values.get("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ),
        zhipu=ProviderKeys(
            api_key=values.get("ZHIPU_API_KEY", ""),
            api_base=values.get("ZHIPU_API_BASE", "https://open.bigmodel.cn/api/paas/v4"),
        ),
        moonshot=ProviderKeys(
            api_key=values.get("MOONSHOT_API_KEY", ""),
            api_base=values.get("MOONSHOT_API_BASE", "https://api.moonshot.cn/v1"),
        ),
        nodyhub=ProviderKeys(
            api_key=values.get("NODYHUB_API_KEY", ""),
            api_base=values.get("NODYHUB_API_BASE", "https://nodyhub.com/v1"),
        ),
        huahu=ProviderKeys(
            api_key=values.get("HUAHU_API_KEY", ""),
            api_base=values.get("HUAHU_API_BASE", "https://api.example.com/v1"),
        ),
        jumengai=ProviderKeys(
            api_key=values.get("JUMENGAI_API_KEY", ""),
            api_base=values.get("JUMENGAI_API_BASE", "https://api.example.com/v1"),
        ),
        doubao_text_model=values.get("DOUBAO_TEXT_MODEL", ""),
        doubao_image_model=values.get("DOUBAO_IMAGE_MODEL", ""),
        doubao_image_endpoint_id=values.get("DOUBAO_IMAGE_ENDPOINT_ID", ""),
        ark_seedance_20_model=values.get("ARK_SEEDANCE_20_MODEL_ID", ""),
        ark_seedance_20_fast_model=values.get(
            "ARK_SEEDANCE_20_FAST_MODEL_ID", "doubao-seedance-2-0-fast-260128"
        ),
        ark_seedance_15_pro_i2v_model=values.get(
            "ARK_SEEDANCE_15_PRO_I2V_MODEL_ID", "doubao-seedance-1-5-pro-251215"
        ),
    )


_ENV_MERGE_KEYS = frozenset(
    {
        "DOUBAO_ENDPOINT_ID",
        "DOUBAO_TEXT_MODEL",
        "DOUBAO_IMAGE_MODEL",
        "DOUBAO_IMAGE_ENDPOINT_ID",
        "DOUBAO_SUBJECT_EXTRACT_MODEL",
        "DOUBAO_CHAT_MODEL",
        "ARK_SEEDANCE_20_MODEL_ID",
        "ARK_SEEDANCE_20_FAST_MODEL_ID",
        "ARK_SEEDANCE_15_PRO_I2V_MODEL_ID",
        "DEEPSEEK_CHAT_MODEL",
        "DEEPSEEK_SUBJECT_EXTRACT_MODEL",
    }
)


@lru_cache
def get_llm_keys() -> LlmKeysConfig:
    """读取 LLM 密钥配置；dual/db 模式优先用启动时 warm 的 DB 快照。"""
    from .config import get_settings

    settings = get_settings()
    if settings.llm_keys_source in ("db", "dual"):
        from ..services.credential_service import get_runtime_llm_keys_snapshot

        snapshot = get_runtime_llm_keys_snapshot()
        if snapshot is not None:
            return snapshot
        if settings.llm_keys_source == "db":
            return LlmKeysConfig()

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


def refresh_llm_keys_cache() -> None:
    """密钥变更后清空 lru_cache。"""
    get_llm_keys.cache_clear()


def get_provider_for_model(model_id: str) -> str:
    spec = get_model_spec(model_id)
    if spec:
        return spec.provider
    provider = MODEL_PROVIDER_MAP.get(model_id)
    if not provider:
        raise ValueError(f"Unknown model id: {model_id}")
    return provider


def _provider_keys(cfg: LlmKeysConfig, provider: str, *, model_id: str = "") -> ProviderKeys:
    if provider == "ark":
        return cfg.ark
    if provider == "kling":
        return cfg.kling
    if provider == "dashscope":
        return cfg.dashscope
    if provider == "vidu":
        return cfg.vidu
    if provider == "ltx_runninghub":
        # RunningHub 海外版：直接用海外密钥；未配置时回退 CN 主密钥
        return cfg.ltx_runninghub if cfg.ltx_runninghub.api_key else cfg.runninghub
    if provider == "runninghub":
        spec = get_model_spec(model_id)
        if spec and spec.parameters_extra.get("use_ltx_key"):
            return cfg.ltx_runninghub if cfg.ltx_runninghub.api_key else cfg.runninghub
        return cfg.runninghub
    # 未知供应商：返回空密钥，避免 getattr 抛 AttributeError 导致管理端启停模型 500
    keys = getattr(cfg, provider, None)
    if isinstance(keys, ProviderKeys):
        return keys
    return ProviderKeys()


def get_model_credentials(model_id: str) -> ProviderKeys:
    provider = get_provider_for_model(model_id)
    cfg = get_llm_keys()
    creds = _provider_keys(cfg, provider, model_id=model_id)

    # Legacy: ark image models may fall back to doubao key
    if not creds.api_key and model_id in ARK_IMAGE_MODEL_IDS and cfg.doubao.api_key:
        creds = cfg.doubao

    if not creds.api_key:
        raise ValueError(
            f"API key not configured for model '{model_id}' (provider '{provider}'). "
            "请在管理后台配置供应商密钥"
        )
    return creds


def is_provider_configured(provider: str, *, model_id: str = "", cfg: LlmKeysConfig | None = None) -> bool:
    """按供应商检查密钥是否可用（供 DB 自定义模型使用）。"""
    if provider == "comfyui":
        return True

    resolved_cfg = cfg if cfg is not None else get_llm_keys()
    creds = _provider_keys(resolved_cfg, provider, model_id=model_id)

    if provider == "ark" and not creds.api_key and model_id in ARK_IMAGE_MODEL_IDS:
        creds = resolved_cfg.doubao

    if not creds.api_key:
        return False

    if model_id == "doubao_pro":
        return bool(resolved_cfg.doubao.endpoint_id.strip() or resolved_cfg.doubao_text_model.strip())

    return True


def is_model_configured(model_id: str, *, provider: str | None = None, cfg: LlmKeysConfig | None = None) -> bool:
    """模型是否已配置密钥；自定义 DB 模型可传 provider。"""
    resolved_provider = provider
    if not resolved_provider:
        try:
            resolved_provider = get_provider_for_model(model_id)
        except ValueError:
            return False
    return is_provider_configured(resolved_provider, model_id=model_id, cfg=cfg)


def list_configured_model_ids() -> list[str]:
    from ..services.model_catalog_runtime import iter_runtime_model_names, uses_db_catalog_runtime

    if uses_db_catalog_runtime():
        names = iter_runtime_model_names()
        if names:
            return [mid for mid in names if is_model_configured(mid)]
    return [mid for mid in MODEL_PROVIDER_MAP if is_model_configured(mid)]
