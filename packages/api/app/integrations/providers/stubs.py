"""Provider capability stubs — upstream API surfaces reserved for future wiring."""

from __future__ import annotations

from typing import Any

from ...core.model_registry import get_model_spec


class ProviderNotImplementedError(NotImplementedError):
    """Raised when a model is registered but upstream call is not yet implemented."""

    def __init__(self, model_id: str, capability: str, upstream_hint: str = "") -> None:
        spec = get_model_spec(model_id)
        upstream = upstream_hint or (spec.upstream_model if spec else "")
        msg = (
            f"Model '{model_id}' capability '{capability}' is registered but not yet implemented. "
            f"Upstream: {upstream or 'n/a'}. Configure keys in admin model-providers."
        )
        super().__init__(msg)
        self.model_id = model_id
        self.capability = capability


# ---------------------------------------------------------------------------
# DashScope (百炼): wan image, video-synthesis family, CosyVoice
# ---------------------------------------------------------------------------


async def dashscope_text_to_image(req: dict[str, Any]) -> str:
    """POST /services/aigc/image-generation/generation + poll /tasks/{id}"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "text_to_image", "dashscope image-generation")


async def dashscope_image_to_image(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "image_to_image", "dashscope image-generation")


async def dashscope_reference_to_video(req: dict[str, Any]) -> str:
    """POST /services/aigc/video-generation/video-synthesis"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "reference_to_video", "dashscope video-synthesis")


async def dashscope_first_frame_to_video(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "first_frame_to_video", "dashscope video-synthesis")


async def dashscope_start_end_to_video(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "start_end_to_video", "dashscope video-synthesis")


async def dashscope_voice_clone(req: dict[str, Any]) -> dict[str, Any]:
    """POST /services/audio/tts/customization action=create_voice"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "voice_clone", "dashscope customization")


async def dashscope_voice_design(req: dict[str, Any]) -> dict[str, Any]:
    raise ProviderNotImplementedError(req.get("model_id", ""), "voice_design", "dashscope customization")


async def dashscope_text_to_speech(req: dict[str, Any]) -> bytes:
    """POST /services/audio/tts/SpeechSynthesizer"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "text_to_speech", "dashscope SpeechSynthesizer")


# ---------------------------------------------------------------------------
# Volcengine Ark: Seedream images, Seedance video tasks
# ---------------------------------------------------------------------------


async def ark_text_to_image(req: dict[str, Any]) -> str:
    """POST /images/generations"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "text_to_image", "ark images/generations")


async def ark_image_to_image(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "image_to_image", "ark images/generations")


async def ark_reference_to_video(req: dict[str, Any]) -> str:
    """POST /contents/generations/tasks"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "reference_to_video", "ark contents/generations/tasks")


async def ark_start_end_to_video(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "start_end_to_video", "ark contents/generations/tasks")


async def ark_first_frame_to_video(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "first_frame_to_video", "ark contents/generations/tasks")


# ---------------------------------------------------------------------------
# Vidu enterprise
# ---------------------------------------------------------------------------


async def vidu_reference_to_video(req: dict[str, Any]) -> str:
    """POST /ent/v2/reference2video"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "reference_to_video", "vidu reference2video")


async def vidu_start_end_to_video(req: dict[str, Any]) -> str:
    """POST /ent/v2/start-end2video"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "start_end_to_video", "vidu start-end2video")


async def vidu_lip_sync(req: dict[str, Any]) -> str:
    """POST /ent/v2/lip-sync"""
    raise ProviderNotImplementedError(req.get("model_id", ""), "lip_sync", "vidu lip-sync")


# ---------------------------------------------------------------------------
# RunningHub
# ---------------------------------------------------------------------------


async def runninghub_text_to_image(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "text_to_image", "runninghub openapi/v2")


async def runninghub_image_to_image(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "image_to_image", "runninghub openapi/v2")


async def runninghub_first_frame_to_video(req: dict[str, Any]) -> str:
    raise ProviderNotImplementedError(req.get("model_id", ""), "first_frame_to_video", "runninghub ltx-2.3 i2v")


async def runninghub_poll_task(task_id: str) -> dict[str, Any]:
    """POST /openapi/v2/query"""
    raise ProviderNotImplementedError("", "poll", f"runninghub query task_id={task_id}")
