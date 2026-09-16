"""Canvas model catalog — 开源空壳。

正式产品目录已清空；仅保留类型与解析 API，便于自行录入模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ModelCategory = Literal["text", "image", "video", "audio", "tool"]
VideoMode = Literal["r2v", "i2v", "lip_sync", "t2v"]
ProviderId = Literal[
    "doubao",
    "ark",
    "deepseek",
    "dashscope",
    "kling",
    "vidu",
    "runninghub",
    "ltx_runninghub",
    "nodyhub",
    "huahu",
    "jumengai",
    "comfyui",
    "openai",
    "qwen",
    "zhipu",
    "moonshot",
]

Capability = Literal[
    "chat_completion",
    "text_to_image",
    "image_to_image",
    "reference_to_video",
    "image_to_video",
    "first_frame_to_video",
    "start_end_to_video",
    "lip_sync",
    "voice_clone",
    "voice_design",
    "text_to_speech",
    "text_to_music",
    "audio_separate",
    "subtitle_erase",
    "comfyui_pipeline",
]


@dataclass(frozen=True)
class CanvasModelSpec:
    name: str
    display_name: str
    provider: ProviderId
    model_type: str
    category: ModelCategory
    description: str
    sort_order: int
    upstream_model: str = ""
    capabilities: tuple[Capability, ...] = ()
    video_mode: VideoMode | None = None
    parameters_extra: dict[str, Any] = field(default_factory=dict)
    implementation: Literal["live", "reserved"] = "live"


# 开源副本：无预置模型
CANVAS_MODEL_SPECS: tuple[CanvasModelSpec, ...] = ()
CANVAS_MODEL_BY_NAME: dict[str, CanvasModelSpec] = {}
MODEL_PROVIDER_MAP: dict[str, str] = {}
ARK_IMAGE_MODEL_IDS: frozenset[str] = frozenset()
LIVE_ARK_IMAGE_MODEL_IDS = ARK_IMAGE_MODEL_IDS
VIDEO_DURATION_LIMITS: dict[str, tuple[int, int]] = {}


def get_model_spec(model_id: str) -> CanvasModelSpec | None:
    """解析模型规格：优先 DB 运行时快照。"""
    from ..services.model_catalog_runtime import resolve_model_spec

    return resolve_model_spec(model_id)


def catalog_dict_for_spec(spec: CanvasModelSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "display_name": spec.display_name,
        "provider": spec.provider,
        "model_type": spec.model_type,
        "category": spec.category,
        "description": spec.description,
        "sort_order": spec.sort_order,
    }


def parameters_metadata_for_spec(spec: CanvasModelSpec) -> dict[str, Any]:
    from ..services.model_availability import provider_group_label

    meta: dict[str, Any] = {
        "upstreamModel": spec.upstream_model,
        "capabilities": list(spec.capabilities),
        "implementation": spec.implementation,
        "providerGroup": provider_group_label(spec.provider),
    }
    if spec.video_mode:
        meta["videoMode"] = spec.video_mode
    limits = video_duration_limits(spec.name)
    if limits:
        meta["videoMinDuration"], meta["videoMaxDuration"] = limits
    if spec.parameters_extra:
        meta.update(spec.parameters_extra)
    meta["compatible_node_types"] = _compatible_nodes_for_category(spec.category)
    return meta


def _compatible_nodes_for_category(category: ModelCategory) -> list[str]:
    if category == "text":
        return ["text_input", "prompt", "llm_text"]
    if category == "image":
        return ["image_input", "ksampler", "model_loader", "image_preview", "upscale"]
    if category == "video":
        return ["video_input"]
    if category == "audio":
        return ["audio_input"]
    return []


def video_duration_limits(model_name: str) -> tuple[int, int] | None:
    return VIDEO_DURATION_LIMITS.get(model_name)


def is_spec_implementation_live(spec: CanvasModelSpec) -> bool:
    return spec.implementation == "live"
