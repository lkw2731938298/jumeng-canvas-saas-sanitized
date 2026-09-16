"""Model availability: keys configured + upstream integration live."""

from __future__ import annotations

from ..core.config import get_settings
from ..core.llm_keys import is_model_configured, is_provider_configured
from ..core.model_registry import Capability, get_model_spec, is_spec_implementation_live
from ..models.job import Model


def is_model_implemented(model_id: str) -> bool:
    spec = get_model_spec(model_id)
    if not spec:
        return False
    if spec.provider == "comfyui":
        return bool(get_settings().comfyui_base_url.strip())
    if not is_spec_implementation_live(spec):
        return False
    return is_model_configured(model_id)


def is_catalog_model_implemented(model: Model) -> bool:
    """结合 registry 与 DB parameters 判断模型是否可接入（含后台自定义模型）。"""
    spec = get_model_spec(model.name)
    if spec:
        return is_model_implemented(model.name)

    params = model.parameters if isinstance(model.parameters, dict) else {}
    if params.get("adminSoftDeleted"):
        return False
    if params.get("implementation", "reserved") != "live":
        return False
    if model.provider == "comfyui":
        return bool(get_settings().comfyui_base_url.strip())
    return is_provider_configured(model.provider, model_id=model.name)


def is_capability_implemented(model_id: str, capability: Capability) -> bool:
    spec = get_model_spec(model_id)
    if not spec or capability not in spec.capabilities:
        return False
    return is_model_implemented(model_id)


def provider_group_label(provider: str) -> str:
    return PROVIDER_GROUP_LABELS.get(provider, provider)


PROVIDER_GROUP_LABELS: dict[str, str] = {
    "doubao": "火山方舟 · 豆包文本",
    "ark": "火山方舟 · 即梦",
    "deepseek": "DeepSeek",
    # 可灵走百炼兼容地址，与万相等同属百炼分组（管理后台同一 section）
    "dashscope": "百炼 · 万相 / HappyHorse / PixVerse / CosyVoice / 可灵",
    "kling": "百炼 · 万相 / HappyHorse / PixVerse / CosyVoice / 可灵",
    "vidu": "Vidu 企业版",
    "runninghub": "RunningHub · CN 站（MJ 等）",
    "ltx_runninghub": "RunningHub 海外版 · 图片 / 视频 / 音乐 / LLM",
    "nodyhub": "NodyHub · 图片 / 视频",
    "huahu": "华狐 AI · Seedance",
    "jumengai": "聚梦 AI 网关 · 图片 / 视频 / 去字幕",
    "comfyui": "ComfyUI 本地推理",
    "openai": "OpenAI",
    "qwen": "百炼兼容 · 通义 / DeepSeek",
    "zhipu": "智谱",
    "moonshot": "Moonshot",
}
