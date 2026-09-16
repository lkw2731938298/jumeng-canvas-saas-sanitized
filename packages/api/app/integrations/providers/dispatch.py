"""将画布生成请求路由到对应上游集成（百炼/Ark/Vidu/RunningHub 等）。"""

from __future__ import annotations

import logging
from typing import Any

from ...core.llm_keys import get_llm_keys, get_model_credentials
from ...core.model_registry import ARK_IMAGE_MODEL_IDS, get_model_spec
from ...services.model_availability import is_capability_implemented
from ..llm.chat import chat_completion
from ..llm.image import download_image, generate_image as ark_generate_image
from ..upstream.ark import build_seedance_i2v_body, build_seedance_r2v_body, seedance_video_generate
from ..upstream.dashscope import (
    CLONE_TARGET_MODEL,
    PRESET_TTS_MODEL,
    _resolution_param,
    cosyvoice_controls_from_params,
    cosyvoice_create_voice,
    cosyvoice_tts,
    dashscope_video_generate,
    kling_reference_video_generate,
    qwen_image_generate,
    wan_image_generate,
)
from ..upstream.errors import UpstreamError
from ..upstream.fetch import download_bytes
from ..upstream.huahu import (
    build_seedance_i2v_body as huahu_build_i2v,
    build_seedance_multimodal_body as huahu_build_multimodal,
    build_seedance_t2v_body as huahu_build_t2v,
    huahu_seedance_video_generate,
)
from ..upstream.jumengai import (
    fetch_jumengai_video,
    jumengai_image_generate,
    jumengai_subtitle_erase,
    jumengai_video_generate,
)
from ..upstream.nodyhub import nodyhub_image_generate, nodyhub_video_generate
from ..upstream.reference_url import resolve_reference_urls
from ..upstream.runninghub import (
    runninghub_audio_extract,
    runninghub_hailuo_h3_video,
    runninghub_image_generate,
    runninghub_ltx_i2v,
    runninghub_music_generate,
    runninghub_seedance_video,
    runninghub_subtitle_erase,
)
from ..upstream.vidu import vidu_lip_sync, vidu_reference_to_video, vidu_start_end_to_video
from .stubs import ProviderNotImplementedError
from .types import (
    AudioGenRequest,
    AudioGenResult,
    ImageGenBatchResult,
    ImageGenRequest,
    ImageGenResult,
    TextGenRequest,
    VideoGenRequest,
    VideoGenResult,
)

logger = logging.getLogger(__name__)

_LIVE_TEXT_MODELS = frozenset(
    {
        "doubao_pro",
        "doubao_seed_21_pro",
        "doubao_seed_evolving",
        "deepseek_v3",
        "deepseek_r1",
        "deepseek_v4_flash",
        "deepseek_v4_pro",
        "rh_gemini_31_flash_lite",
        "rh_gemini_35_flash",
        "rh_gpt_56_sol",
        "rh_gpt_56_terra",
        "rh_gpt_55",
        "rh_claude_fable_5",
        "rh_claude_opus_48",
    }
)


def _api_int(params: dict[str, Any], *keys: str, default: int) -> int:
    for k in keys:
        if k in params and params[k] is not None:
            try:
                return int(params[k])
            except (TypeError, ValueError):
                pass
    return default


from ...services.reference_image import (
    _is_probably_audio_url,
    _is_probably_image_url,
    _is_probably_video_url,
    resolve_reference_urls_for_upstream,
)
from ...services.reference_video import resolve_lip_sync_media_urls
from ...services.reference_video import (
    resolve_video_keyframe_for_reference,
    resolve_video_reference_for_multimodal,
    resolve_video_urls_for_upstream,
)


def _assert_upstream_image_url(url: str, *, label: str) -> None:
    from ...core.config import get_settings

    cleaned = (url or "").strip()
    if not cleaned:
        raise UpstreamError(f"{label}不能为空", code="INVALID_MEDIA")
    if cleaned.startswith("data:image/"):
        if get_settings().canvas_storage_local_only:
            return
        raise UpstreamError(
            f"{label}无法使用内联图片，请确认素材已上传到项目 OSS",
            code="INVALID_MEDIA",
        )
    if not cleaned.startswith("http"):
        raise UpstreamError(f"{label}必须是可公网访问的 HTTPS 地址", code="INVALID_MEDIA")


def _resolve_video_media_references(
    req: VideoGenRequest,
    raw_refs: list[str],
) -> tuple[list[str], list[str], list[str]]:
    """按画布参考类型解析图/视频/音频 URL（视频禁止降级为关键帧）。"""
    pid = req.project_id
    folder = req.storage_folder
    if req.reference_image_urls or req.reference_video_urls or req.reference_audio_urls:
        img_raw = [u for u in req.reference_image_urls if u and str(u).strip()]
        vid_raw = [u for u in req.reference_video_urls if u and str(u).strip()]
        aud_raw = [u for u in req.reference_audio_urls if u and str(u).strip()]
    else:
        img_raw = [u for u in raw_refs if _is_probably_image_url(u)]
        vid_raw = [u for u in raw_refs if _is_probably_video_url(u)]
        aud_raw = [u for u in raw_refs if _is_probably_audio_url(u)]
    image_refs = resolve_reference_urls(img_raw, project_id=pid, storage_folder=folder)
    try:
        video_refs = resolve_video_urls_for_upstream(
            vid_raw, project_id=pid, storage_folder=folder
        )
    except ValueError as exc:
        raise UpstreamError(str(exc), code="INVALID_MEDIA") from exc
    audio_refs = resolve_reference_urls(aud_raw, project_id=pid, storage_folder=folder)
    if req.audio_url and str(req.audio_url).strip():
        extra = resolve_reference_urls(
            [str(req.audio_url).strip()],
            project_id=pid,
            storage_folder=folder,
        )
        for u in extra:
            if u not in audio_refs:
                audio_refs.append(u)
    return image_refs, video_refs, audio_refs


def _api_bool(params: dict[str, Any], *keys: str, default: bool = False) -> bool:
    """从 generationOptions / api 映射读取布尔开关（兼容 on/true/1）。"""
    for k in keys:
        if k not in params or params[k] is None:
            continue
        v = params[k]
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n", ""):
            return False
    return default


def _api_str(params: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        val = params.get(k)
        if val is not None and str(val).strip():
            return str(val).strip()
    return default


async def generate_text(req: TextGenRequest) -> str:
    """按 model_id 调用 LLM 聊天补全，返回生成文本。"""
    if req.model_id not in _LIVE_TEXT_MODELS:
        raise ProviderNotImplementedError(req.model_id, "chat_completion")
    return await chat_completion(
        req.model_id,
        req.messages,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
    )


def _image_output_count(params: dict[str, Any]) -> int:
    """从 compose_generation 合并后的 api_params 读取生成张数（1–6，对齐千问上限）。"""
    return max(1, min(_api_int(params, "n", "count", default=1), 6))


# 千问 1K/2K × 画幅 → 官方 size「宽*高」（按像素面积分档计费：≤225万=1K，否则 2K）
_QWEN_SIZE_BY_TIER_RATIO: dict[str, dict[str, str]] = {
    "1k": {
        "1:1": "1024*1024",
        "16:9": "1280*720",
        "9:16": "720*1280",
        "4:3": "1152*864",
        "3:4": "864*1152",
        "3:2": "1248*832",
        "2:3": "832*1248",
        "21:9": "1344*576",
    },
    "2k": {
        "1:1": "2048*2048",
        "16:9": "2048*1152",
        "9:16": "1152*2048",
        "4:3": "1920*1440",
        "3:4": "1440*1920",
        "3:2": "2048*1365",
        "2:3": "1365*2048",
        "21:9": "2048*878",
    },
}


def _qwen_image_size(params: dict[str, Any], fallback_size: str | None) -> str | None:
    """解析千问 size：已是宽*高则直用；否则按 sizeTier + ratio 查表。"""
    raw = (fallback_size or _api_str(params, "size", default="") or "").strip()
    normalized = raw.replace("x", "*").replace("X", "*")
    if "*" in normalized:
        parts = normalized.split("*")
        if len(parts) == 2 and all(p.strip().isdigit() for p in parts):
            return f"{int(parts[0])}*{int(parts[1])}"
    tier = (_api_str(params, "sizeTier", default="") or raw or "1k").strip().lower()
    if tier not in ("1k", "2k"):
        if tier in ("1", "1024"):
            tier = "1k"
        elif tier in ("2", "2048"):
            tier = "2k"
        else:
            tier = "1k"
    ratio = (_api_str(params, "ratio", "aspectRatio", default="1:1") or "1:1").strip()
    table = _QWEN_SIZE_BY_TIER_RATIO.get(tier) or _QWEN_SIZE_BY_TIER_RATIO["1k"]
    return table.get(ratio) or table.get("1:1")


async def _download_image_results(urls: list[str]) -> list[ImageGenResult]:
    """下载多张图片 URL 并统一为 ImageGenResult。"""
    out: list[ImageGenResult] = []
    for img_url in urls:
        if not img_url:
            continue
        data, content_type = await download_bytes(img_url)
        ext = "png"
        if "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        elif "webp" in content_type:
            ext = "webp"
        out.append(ImageGenResult(data=data, content_type=content_type, ext=ext, source_url=img_url))
    if not out:
        raise UpstreamError("未返回可用图片", code="EMPTY_OUTPUT")
    return out


async def generate_image(req: ImageGenRequest) -> ImageGenBatchResult:
    """按模型规格路由到 Ark/万相/RunningHub 等上游图片生成（支持 api.n 多张）。"""
    spec = get_model_spec(req.model_id)
    if not spec:
        raise ProviderNotImplementedError(req.model_id, "text_to_image")

    refs = resolve_reference_urls(
        req.reference_urls or [],
        project_id=req.project_id,
        storage_folder=req.storage_folder,
    )
    params = req.api_params or {}
    size = req.size or _api_str(params, "size", default="2K")
    watermark = bool(req.watermark or params.get("watermark"))
    count = _image_output_count(params)

    try:
        if req.model_id in ARK_IMAGE_MODEL_IDS:
            # Ark/即梦单次接口以一张为主；多张时串行提交（同一 job 内，非 Worker 重试双 POST）
            urls: list[str] = []
            for _ in range(count):
                temp_url = await ark_generate_image(
                    req.model_id,
                    req.prompt,
                    reference_urls=refs or None,
                    size=size,
                    watermark=watermark,
                    project_id=req.project_id,
                    storage_folder=req.storage_folder,
                )
                urls.append(temp_url)
            images: list[ImageGenResult] = []
            for temp_url in urls:
                data, content_type = await download_image(temp_url)
                ext = "png"
                if "jpeg" in content_type or "jpg" in content_type:
                    ext = "jpg"
                elif "webp" in content_type:
                    ext = "webp"
                images.append(
                    ImageGenResult(data=data, content_type=content_type, ext=ext, source_url=temp_url)
                )
            return ImageGenBatchResult(images=images)

        if req.model_id in ("wan27_image", "wan27_image_pro"):
            upstream = spec.upstream_model
            img_urls = await wan_image_generate(
                model=upstream,
                prompt=req.prompt,
                reference_urls=refs,
                size=size,
                watermark=watermark,
                n=count,
            )
            return ImageGenBatchResult(images=await _download_image_results(img_urls))

        # 百炼千问图像 3.0 / 3.0 Pro（同步 multimodal）
        if req.model_id in ("qwen_image_30", "qwen_image_30_pro"):
            upstream = spec.upstream_model or (
                "qwen-image-3.0-pro" if req.model_id.endswith("_pro") else "qwen-image-3.0"
            )
            qwen_size = _qwen_image_size(params, size)
            prompt_extend = _api_bool(params, "prompt_extend", default=True)
            neg = _api_str(params, "negative_prompt", "negativePrompt", default="") or None
            img_urls = await qwen_image_generate(
                model=upstream,
                prompt=req.prompt,
                reference_urls=refs[:3] if refs else None,
                size=qwen_size,
                watermark=watermark,
                n=count,
                prompt_extend=prompt_extend,
                negative_prompt=neg,
            )
            return ImageGenBatchResult(images=await _download_image_results(img_urls))

        # NodyHub OpenAI 兼容图片
        if spec.provider == "nodyhub":
            img_urls = await nodyhub_image_generate(
                model=spec.upstream_model or req.model_id,
                prompt=req.prompt,
                reference_urls=refs if refs else None,
                size=_api_str(params, "size", default=size or "1024x1024"),
                quality=_api_str(params, "quality", default="") or None,
                n=count,
            )
            return ImageGenBatchResult(images=await _download_image_results(img_urls))

        # 聚梦网关 OpenAI 兼容图片（1K/2K/4K）
        if spec.provider == "jumengai":
            res_raw = _api_str(params, "resolution", "size", default="") or size or "2K"
            size_map = {"1k": "1K", "2k": "2K", "4k": "4K"}
            jumeng_size = size_map.get(res_raw.lower(), res_raw)
            img_urls = await jumengai_image_generate(
                model=spec.upstream_model or req.model_id,
                prompt=req.prompt,
                reference_urls=refs if refs else None,
                size=jumeng_size,
                quality=_api_str(params, "quality", default="") or None,
                n=count,
            )
            return ImageGenBatchResult(images=await _download_image_results(img_urls))

        route = (spec.parameters_extra or {}).get("runninghub_route")
        if route:
            resolution = _api_str(params, "resolution", "size", default="2k").lower()
            aspect = _api_str(params, "ratio", "aspectRatio", default="") or None
            # 画质档位（全能图片 G 支持 low/medium/high；悠船 v8.1 为 "1"|"4"）
            quality = _api_str(params, "quality", default="") or None
            # 全能图片 Pro/G、悠船 v8.1/niji7：按 use_ltx_key 走海外站 www.runninghub.ai
            use_overseas = bool((spec.parameters_extra or {}).get("use_ltx_key"))
            # RunningHub 标准图接口无 n 字段：按张数串行提交并轮询
            img_urls = []
            for _ in range(count):
                img_url = await runninghub_image_generate(
                    route=route,
                    prompt=req.prompt,
                    image_urls=refs if refs else None,
                    resolution=resolution,
                    aspect_ratio=aspect,
                    quality=quality,
                    use_overseas=use_overseas,
                    api_params=params,
                )
                img_urls.append(img_url)
            return ImageGenBatchResult(images=await _download_image_results(img_urls))

    except UpstreamError:
        raise

    raise ProviderNotImplementedError(req.model_id, "text_to_image")


async def generate_video(req: VideoGenRequest) -> VideoGenResult:
    """按模型规格路由到 Seedance/百炼/Vidu/RunningHub 等上游视频生成。"""
    spec = get_model_spec(req.model_id)
    if not spec:
        raise ProviderNotImplementedError(req.model_id, "image_to_video")

    params = req.api_params or {}
    raw_refs = [u for u in (req.reference_urls or []) if u and str(u).strip()]
    if spec and spec.video_mode == "i2v":
        first_source = (req.first_frame_url or "").strip() or None
        last_source = (req.last_frame_url or "").strip() or None
    else:
        first_source = req.first_frame_url
        last_source = req.last_frame_url

    refs = resolve_reference_urls(
        raw_refs, project_id=req.project_id, storage_folder=req.storage_folder
    )
    image_refs, video_refs, audio_refs = _resolve_video_media_references(req, raw_refs)
    # 兼容仍从扁平 refs 拆分的路径（不含音频，避免误当图/视频）
    refs = image_refs + video_refs
    first = resolve_reference_urls(
        [first_source] if first_source else [],
        project_id=req.project_id,
        storage_folder=req.storage_folder,
    )
    last = resolve_reference_urls(
        [last_source] if last_source else [],
        project_id=req.project_id,
        storage_folder=req.storage_folder,
    )
    first_url = first[0] if first else (refs[0] if refs else "")
    last_url = last[0] if last else (refs[1] if len(refs) > 1 else None)

    duration = _api_int(params, "duration", "durationSec", default=5)
    resolution = _api_str(params, "resolution", default="720")
    ratio = _api_str(params, "ratio", "aspectRatio", default="16:9")
    watermark = bool(params.get("watermark", False))

    try:
        model_id = req.model_id
        upstream = spec.upstream_model

        # --- 去字幕：聚梦网关精准版 / RunningHub 精细化版 ---
        if "subtitle_erase" in (spec.capabilities or ()):
            route = str((spec.parameters_extra or {}).get("runninghub_route") or model_id).strip()
            video_url_src = (
                _api_str(params, "videoUrl", "video_url", "sourceUrl", "source_url", default="")
                or (req.video_url or "").strip()
            )
            if not video_url_src:
                # 回退参考视频 URL（画布连线）
                for u in refs:
                    if _is_probably_video_url(u):
                        video_url_src = u
                        break
            if not video_url_src:
                raise UpstreamError("去字幕需要源视频 URL（videoUrl）", code="INVALID_MEDIA")
            erase_type = _api_str(params, "eraseType", "erase_type", default="subtitle") or "subtitle"
            encode_mode = _api_str(params, "encodeMode", "encode_mode", default="size") or "size"
            erase_boxes = params.get("eraseRatioLocation")
            if erase_boxes is None:
                erase_boxes = params.get("erase_ratio_location")
            seconds = _api_int(
                params, "seconds", "duration", "durationSec", "outputVideoSeconds", "inputVideoSeconds", default=5
            )
            size = _api_str(params, "size", "resolution", default="720P") or "720P"
            if spec.provider == "jumengai":
                video_url = await jumengai_subtitle_erase(
                    model=upstream or model_id,
                    video_url=video_url_src,
                    prompt=req.prompt,
                    seconds=seconds,
                    size=size,
                    erase_type=erase_type,
                    encode_mode=encode_mode,
                    erase_ratio_location=erase_boxes,
                )
                data, content_type = await fetch_jumengai_video(video_url)
            else:
                video_url = await runninghub_subtitle_erase(
                    route=route,
                    video_url=video_url_src,
                    erase_type=erase_type,
                    encode_mode=encode_mode,
                    erase_ratio_location=erase_boxes,
                )
                data, content_type = await download_bytes(video_url)
            return VideoGenResult(
                data=data,
                content_type=content_type or "video/mp4",
                ext="mp4",
                source_url=video_url,
            )

        # --- 聚梦网关视频（可灵 / Seedance / MiniMax H3 / 图生视频）---
        if spec.provider == "jumengai":
            model_name = (upstream or "").strip()
            if not model_name:
                raise UpstreamError("聚梦网关视频缺少 upstream_model", code="INVALID_MODEL")
            gen_audio = bool(
                params.get("generateAudio", params.get("audio", params.get("generate_audio", True)))
            )
            ratio_api = _api_str(params, "ratio", "aspectRatio", default="16:9") or "16:9"
            limits = None
            try:
                from ...core.model_registry import video_duration_limits

                limits = video_duration_limits(model_id)
            except Exception:
                limits = None
            lo, hi = limits if limits else (4, 15)
            dur = max(int(lo), min(int(hi), int(duration or 5)))
            jm_pid = req.project_id
            jm_folder = req.storage_folder
            try:
                if spec.video_mode == "i2v":
                    if not first_url:
                        raise UpstreamError("聚梦图生视频需要首帧图片", code="INVALID_MEDIA")
                    raw_imgs = [first_url]
                    if last_url and last_url != first_url:
                        raw_imgs.append(last_url)
                    image_refs = resolve_reference_urls_for_upstream(
                        raw_imgs, project_id=jm_pid, storage_folder=jm_folder
                    )
                    video_refs = []
                    audio_refs = []
                else:
                    img_raw = [u for u in req.reference_image_urls if u and str(u).strip()]
                    vid_raw = [u for u in req.reference_video_urls if u and str(u).strip()]
                    aud_raw = [u for u in req.reference_audio_urls if u and str(u).strip()]
                    if not img_raw and image_refs:
                        img_raw = list(image_refs)
                    if not vid_raw and video_refs:
                        vid_raw = list(video_refs)
                    if not aud_raw and audio_refs:
                        aud_raw = list(audio_refs)
                    image_refs = resolve_reference_urls_for_upstream(
                        img_raw, project_id=jm_pid, storage_folder=jm_folder
                    )
                    video_refs = resolve_video_urls_for_upstream(
                        vid_raw,
                        project_id=jm_pid,
                        storage_folder=jm_folder,
                        upstream_presign=True,
                    )
                    audio_refs = resolve_reference_urls_for_upstream(
                        aud_raw, project_id=jm_pid, storage_folder=jm_folder
                    )
            except ValueError as exc:
                raise UpstreamError(str(exc), code="INVALID_MEDIA") from exc
            if model_id in ("jumengai_kling_v3", "jumengai_minimax_h3"):
                if not image_refs and not video_refs:
                    raise UpstreamError("该模型需要至少一张参考图或一段参考视频", code="INVALID_MEDIA")
            real_person = _api_bool(
                params, "realPersonMode", "real_person_mode", "realPerson", default=True
            )
            video_url = await jumengai_video_generate(
                model=model_name,
                prompt=req.prompt,
                duration_sec=dur,
                resolution=resolution,
                ratio=ratio_api,
                generate_audio=gen_audio,
                image_urls=image_refs,
                video_urls=video_refs,
                audio_urls=audio_refs,
                mode=_api_str(params, "mode", default="") or None,
                video_mode=str(spec.video_mode or "r2v"),
                real_person_mode=real_person,
                image_labels=list(req.reference_image_labels or []),
                video_labels=list(req.reference_video_labels or []),
            )
            data, content_type = await fetch_jumengai_video(video_url)
            return VideoGenResult(
                data=data,
                content_type=content_type or "video/mp4",
                ext="mp4",
                source_url=video_url,
            )

        # --- Vidu ---
        if spec.provider == "vidu":
            if spec.video_mode == "lip_sync":
                if not req.video_url or not req.audio_url:
                    raise UpstreamError("对口型需要视频与音频 URL", code="INVALID_MEDIA")
                try:
                    video_ref, audio_ref = resolve_lip_sync_media_urls(
                        req.video_url,
                        req.audio_url,
                        project_id=req.project_id,
                        storage_folder=req.storage_folder,
                    )
                except ValueError as exc:
                    raise UpstreamError(str(exc), code="INVALID_MEDIA") from exc
                video_url = await vidu_lip_sync(video_url=video_ref, audio_url=audio_ref)
            elif spec.video_mode == "r2v":
                if not refs:
                    raise UpstreamError("参考生视频需要至少一张参考图", code="INVALID_MEDIA")
                video_url = await vidu_reference_to_video(
                    model=upstream,
                    prompt=req.prompt,
                    image_urls=refs,
                    duration=duration,
                    resolution=resolution,
                    aspect_ratio=ratio,
                    watermark=watermark,
                    off_peak=bool(params.get("offPeak", params.get("off_peak", False))),
                    audio=bool(params.get("audio", params.get("generateAudio", False))),
                )
            else:
                if not first_url:
                    raise UpstreamError("首尾帧生视频需要首帧图片", code="INVALID_MEDIA")
                video_url = await vidu_start_end_to_video(
                    model=upstream,
                    prompt=req.prompt,
                    first_frame_url=first_url,
                    last_frame_url=last_url,
                    duration=duration,
                    resolution=resolution,
                    watermark=watermark,
                    off_peak=bool(params.get("offPeak", params.get("off_peak", False))),
                    audio=bool(params.get("audio", params.get("generateAudio", False))),
                )
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

        # --- Ark Seedance ---
        if spec.provider == "ark" and spec.video_mode in ("r2v", "i2v"):
            keys = get_llm_keys()
            if model_id.startswith("seedance_15"):
                model_name = keys.ark_seedance_15_pro_i2v_model
            elif "fast" in model_id:
                model_name = keys.ark_seedance_20_fast_model
            else:
                model_name = upstream or keys.ark_seedance_20_model

            if spec.video_mode == "r2v":
                if not refs:
                    raise UpstreamError("Seedance 参考生需要参考图", code="INVALID_MEDIA")
                # duration / generate_audio 来自节点生成选项，禁止写死 -1 / 默认 True 导致与 UI 不一致
                gen_audio = bool(
                    params.get("generateAudio", params.get("audio", params.get("generate_audio", True)))
                )
                real_person = _api_bool(
                    params, "realPersonMode", "real_person_mode", "realPerson", default=True
                )
                body = build_seedance_r2v_body(
                    model=model_name,
                    prompt=req.prompt,
                    reference_urls=refs,
                    resolution=f"{resolution}p",
                    ratio=ratio,
                    duration=duration,
                    watermark=watermark,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                )
            else:
                if not first_url:
                    raise UpstreamError("生图转视频需要首帧图片", code="INVALID_MEDIA")
                _assert_upstream_image_url(first_url, label="首帧图片")
                if last_url:
                    _assert_upstream_image_url(last_url, label="尾帧图片")
                gen_audio = bool(
                    params.get("generateAudio", params.get("audio", params.get("generate_audio", True)))
                )
                real_person = _api_bool(
                    params, "realPersonMode", "real_person_mode", "realPerson", default=True
                )
                body = build_seedance_i2v_body(
                    model=model_name,
                    prompt=req.prompt,
                    first_frame_url=first_url,
                    last_frame_url=last_url,
                    resolution=f"{resolution}p",
                    ratio=ratio,
                    duration=duration,
                    watermark=watermark,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                )
            video_url = await seedance_video_generate(body=body)
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

        # --- RunningHub LTX ---
        if model_id == "ltx_23_i2v":
            if not first_url:
                raise UpstreamError("LTX 生图转视频需要首帧图片", code="INVALID_MEDIA")
            if first_url.startswith("data:"):
                raise UpstreamError(
                    "LTX 需要公网可访问的首帧图片 URL；纯本地模式请换用 Seedance/Vidu 等支持内联图的模型",
                    code="INVALID_MEDIA",
                )
            video_url = await runninghub_ltx_i2v(
                prompt=req.prompt,
                first_frame_url=first_url,
                duration_sec=duration,
                resolution=resolution,
                ratio=ratio,
            )
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

        # --- RunningHub MiniMax-H3 多模态参考生视频 ---
        if model_id.startswith("rh_minimax_hailuo_h3"):
            route = str(
                (spec.parameters_extra or {}).get("runninghub_route") or "rh_minimax_hailuo_h3_r2v"
            ).strip()
            ratio_api = _api_str(params, "ratio", "aspectRatio", default="adaptive") or "adaptive"
            # 上游枚举为 2K / 768P；兼容旧短写
            res_raw = (resolution or "768P").strip()
            image_refs = [u for u in refs if _is_probably_image_url(u)]
            video_refs = [u for u in refs if _is_probably_video_url(u)]
            audio_refs: list[str] = []
            if req.audio_url:
                audio_refs = resolve_reference_urls(
                    [req.audio_url],
                    project_id=req.project_id,
                    storage_folder=req.storage_folder,
                )
            if not image_refs and not video_refs:
                raise UpstreamError("MiniMax-H3 需要至少一张参考图或参考视频", code="INVALID_MEDIA")
            video_url = await runninghub_hailuo_h3_video(
                route=route,
                prompt=req.prompt,
                duration_sec=duration,
                resolution=res_raw,
                ratio=ratio_api,
                image_urls=image_refs,
                video_urls=video_refs,
                audio_urls=audio_refs,
            )
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

        # --- RunningHub Seedance 2.0 / 2.5（CN 站 www.runninghub.cn）---
        if model_id.startswith("rh_seedance_20") or model_id.startswith("rh_seedance_25"):
            route = str((spec.parameters_extra or {}).get("runninghub_route") or model_id).strip()
            use_overseas = bool(
                (spec.parameters_extra or {}).get("use_ltx_key")
            ) or spec.provider == "ltx_runninghub"
            gen_audio = bool(
                params.get("generateAudio", params.get("audio", params.get("generate_audio", True)))
            )
            # 真人模式：默认开启；用户显式关闭时仍可传 off（上游 1505 需开）
            real_person = _api_bool(
                params, "realPersonMode", "real_person_mode", "realPerson", default=True
            )
            ratio_api = _api_str(params, "ratio", "aspectRatio", default="adaptive") or "adaptive"
            bitrate = _api_str(params, "bitrateMode", "bitrate_mode", default="standard") or "standard"
            web_search = _api_bool(params, "webSearch", "web_search", default=False)
            if spec.video_mode == "r2v":
                image_refs = [u for u in refs if _is_probably_image_url(u)]
                video_refs = [u for u in refs if _is_probably_video_url(u)]
                audio_refs: list[str] = []
                if req.audio_url:
                    audio_refs = resolve_reference_urls(
                        [req.audio_url],
                        project_id=req.project_id,
                        storage_folder=req.storage_folder,
                    )
                # 2.5 多模态允许纯文案；2.0 仍要求至少图或视频
                if not model_id.startswith("rh_seedance_25") and not image_refs and not video_refs:
                    raise UpstreamError("Seedance 多模态视频需要至少一张参考图或参考视频", code="INVALID_MEDIA")
                video_url = await runninghub_seedance_video(
                    route=route,
                    prompt=req.prompt,
                    duration_sec=duration,
                    resolution=resolution,
                    ratio=ratio_api,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                    image_urls=image_refs,
                    video_urls=video_refs,
                    audio_urls=audio_refs,
                    bitrate_mode=bitrate,
                    use_overseas=use_overseas,
                )
            elif spec.video_mode == "i2v":
                if not first_url:
                    raise UpstreamError("Seedance 图生视频需要首帧图片", code="INVALID_MEDIA")
                _assert_upstream_image_url(first_url, label="首帧图片")
                if last_url:
                    _assert_upstream_image_url(last_url, label="尾帧图片")
                video_url = await runninghub_seedance_video(
                    route=route,
                    prompt=req.prompt,
                    duration_sec=duration,
                    resolution=resolution,
                    ratio=ratio_api,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                    first_frame_url=first_url,
                    last_frame_url=last_url,
                    bitrate_mode=bitrate,
                    use_overseas=use_overseas,
                )
            elif spec.video_mode == "t2v":
                video_url = await runninghub_seedance_video(
                    route=route,
                    prompt=req.prompt,
                    duration_sec=duration,
                    resolution=resolution,
                    ratio=ratio_api,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                    bitrate_mode=bitrate,
                    web_search=web_search if model_id.startswith("rh_seedance_25") else None,
                    use_overseas=use_overseas,
                )
            else:
                raise UpstreamError(f"不支持的 Seedance 视频模式: {spec.video_mode}", code="INVALID_MODEL")
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

        # --- 华狐 AI Seedance 2.0（New API V1 · /v1/video/generations）---
        if spec.provider == "huahu" and model_id.startswith("huahu_seedance_20"):
            model_name = (upstream or "").strip()
            if not model_name:
                raise UpstreamError("华狐 Seedance 缺少 upstream_model", code="INVALID_MODEL")
            gen_audio = bool(
                params.get("generateAudio", params.get("audio", params.get("generate_audio", True)))
            )
            real_person = _api_bool(
                params, "realPersonMode", "real_person_mode", "realPerson", default=True
            )
            ratio_api = _api_str(params, "ratio", "aspectRatio", default="16:9") or "16:9"
            # 时长：画布选项秒数；上游 duration 为整数秒
            dur = max(4, min(15, int(duration or 5)))
            if spec.video_mode == "r2v":
                image_refs = [u for u in refs if _is_probably_image_url(u)]
                video_refs = [u for u in refs if _is_probably_video_url(u)]
                audio_refs: list[str] = []
                if req.audio_url:
                    audio_refs = resolve_reference_urls(
                        [req.audio_url],
                        project_id=req.project_id,
                        storage_folder=req.storage_folder,
                    )
                if not image_refs and not video_refs:
                    raise UpstreamError("华狐多模态视频需要至少一张参考图或参考视频", code="INVALID_MEDIA")
                body = huahu_build_multimodal(
                    model=model_name,
                    prompt=req.prompt,
                    image_urls=image_refs,
                    video_urls=video_refs,
                    audio_urls=audio_refs,
                    resolution=resolution,
                    ratio=ratio_api,
                    duration=dur,
                    watermark=watermark,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                )
            elif spec.video_mode == "i2v":
                if not first_url:
                    raise UpstreamError("华狐图生视频需要首帧图片", code="INVALID_MEDIA")
                _assert_upstream_image_url(first_url, label="首帧图片")
                if last_url:
                    _assert_upstream_image_url(last_url, label="尾帧图片")
                body = huahu_build_i2v(
                    model=model_name,
                    prompt=req.prompt,
                    first_frame_url=first_url,
                    last_frame_url=last_url,
                    resolution=resolution,
                    ratio=ratio_api,
                    duration=dur,
                    watermark=watermark,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                )
            elif spec.video_mode == "t2v":
                body = huahu_build_t2v(
                    model=model_name,
                    prompt=req.prompt,
                    resolution=resolution,
                    ratio=ratio_api,
                    duration=dur,
                    watermark=watermark,
                    generate_audio=gen_audio,
                    real_person_mode=real_person,
                )
            else:
                raise UpstreamError(f"不支持的华狐视频模式: {spec.video_mode}", code="INVALID_MODEL")
            video_url = await huahu_seedance_video_generate(body=body)
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

        # --- NodyHub SD2.0 全模态 / Fast / Mini（OpenAI 兼容 videos/generations）---
        if spec.provider == "nodyhub" and model_id.startswith("nodyhub_sd20"):
            model_name = (upstream or "").strip()
            if not model_name:
                raise UpstreamError("NodyHub 视频缺少 upstream_model", code="INVALID_MODEL")
            gen_audio = bool(
                params.get("generateAudio", params.get("audio", params.get("generate_audio", True)))
            )
            ratio_api = _api_str(params, "ratio", "aspectRatio", default="16:9") or "16:9"
            dur = max(4, min(15, int(duration or 5)))
            image_refs = [u for u in refs if _is_probably_image_url(u)]
            video_refs = [u for u in refs if _is_probably_video_url(u)]
            audio_refs: list[str] = []
            if req.audio_url:
                audio_refs = resolve_reference_urls(
                    [req.audio_url],
                    project_id=req.project_id,
                    storage_folder=req.storage_folder,
                )
            # 全模态：允许纯文生；有参考则一并提交
            video_url = await nodyhub_video_generate(
                model=model_name,
                prompt=req.prompt,
                duration_sec=dur,
                resolution=resolution,
                ratio=ratio_api,
                generate_audio=gen_audio,
                image_urls=image_refs,
                video_urls=video_refs,
                audio_urls=audio_refs,
            )
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

        # --- DashScope / Kling video-synthesis ---
        if spec.provider in ("dashscope", "kling"):
            use_kling = spec.provider == "kling"
            media: list[dict[str, Any]] = []

            # 万相 3.0 all-in-one（同一 upstream wan3.0-video，按 video_mode 组 media）
            if model_id in ("wan30_r2v", "wan30_i2v", "wan30_t2v"):
                prompt_body = (req.prompt or "").strip()[:20000]
                # 文档 / 网址：OSS 私有链需经 resolve；公网 link 直接透传
                file_refs = resolve_reference_urls(
                    [u for u in (req.file_urls or []) if u and str(u).strip()],
                    project_id=req.project_id,
                    storage_folder=req.storage_folder,
                )
                link_refs = [
                    str(u).strip()
                    for u in (req.link_urls or [])
                    if u and str(u).strip().startswith(("http://", "https://"))
                ]
                if model_id == "wan30_i2v":
                    if not first_url:
                        raise UpstreamError("万相 3.0 图生视频需要首帧", code="INVALID_MEDIA")
                    media = [{"type": "first_frame", "url": first_url}]
                    if last_url and last_url != first_url:
                        media.append({"type": "last_frame", "url": last_url})
                    # 首尾帧与 file/link 互斥；有首帧时不再附文档
                elif model_id == "wan30_r2v":
                    image_refs = [u for u in refs if _is_probably_image_url(u)]
                    video_refs = [u for u in refs if _is_probably_video_url(u)]
                    audio_refs: list[str] = []
                    if req.audio_url:
                        audio_refs = resolve_reference_urls(
                            [req.audio_url],
                            project_id=req.project_id,
                            storage_folder=req.storage_folder,
                        )
                    for u in refs:
                        if _is_probably_audio_url(u) and u not in audio_refs:
                            audio_refs.append(u)
                    # file/link 与 first_frame 互斥；有文档/网址时走参考模式
                    if file_refs or link_refs:
                        for u in file_refs[:5]:
                            media.append({"type": "file", "url": u})
                        for u in link_refs[:5]:
                            media.append({"type": "link", "url": u})
                        for u in image_refs[:5]:
                            media.append({"type": "reference_image", "url": u})
                        for u in video_refs[:3]:
                            media.append({"type": "reference_video", "url": u})
                        for u in audio_refs[:3]:
                            media.append({"type": "reference_audio", "url": u})
                    else:
                        for u in image_refs[:9]:
                            media.append({"type": "reference_image", "url": u})
                        for u in video_refs[:5]:
                            media.append({"type": "reference_video", "url": u})
                        for u in audio_refs[:3]:
                            media.append({"type": "reference_audio", "url": u})
                    if not media and not prompt_body:
                        raise UpstreamError(
                            "万相 3.0 全能参考需要提示词或参考素材（图/视频/音频/文档/网址）",
                            code="INVALID_MEDIA",
                        )
                else:
                    # t2v：纯文本；可附文档/网址作创作素材
                    for u in file_refs[:5]:
                        media.append({"type": "file", "url": u})
                    for u in link_refs[:5]:
                        media.append({"type": "link", "url": u})
                    if not media and first_url and _is_probably_image_url(first_url):
                        media.append({"type": "first_frame", "url": first_url})
                    if not prompt_body and not media:
                        raise UpstreamError("万相 3.0 文生视频需要提示词", code="INVALID_MEDIA")

                dur = max(2, min(30, int(duration or 5)))
                ratio_val = (ratio or "adaptive").strip() or "adaptive"
                if ratio_val not in ("adaptive", "16:9", "9:16", "1:1", "4:3", "3:4"):
                    ratio_val = "adaptive"
                parameters = {
                    "resolution": _resolution_param(resolution),
                    "ratio": ratio_val,
                    "duration": dur,
                    "audio": bool(params.get("audio", params.get("generateAudio", True))),
                    "watermark": watermark,
                }
                input_body: dict[str, Any] = {"prompt": prompt_body}
                if media:
                    input_body["media"] = media
                video_url = await dashscope_video_generate(
                    model=upstream,
                    prompt=prompt_body,
                    media=media,
                    parameters=parameters,
                    input_body=input_body,
                )
                data, content_type = await download_bytes(video_url)
                return VideoGenResult(
                    data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url
                )

            if spec.video_mode == "r2v":
                if model_id == "kling_r2v":
                    video_refs = [u for u in refs if _is_probably_video_url(u)]
                    image_refs = [u for u in refs if _is_probably_image_url(u)]
                    feature_video = ""
                    if video_refs:
                        resolved_video, frame_uri = resolve_video_reference_for_multimodal(
                            video_refs[0], project_id=req.project_id
                        )
                        feature_video = resolved_video or video_refs[0]
                        if not image_refs:
                            image_refs = [frame_uri or resolve_video_keyframe_for_reference(
                                video_refs[0], project_id=req.project_id
                            )]
                    if not image_refs:
                        raise UpstreamError("可灵参考生视频需要参考图或参考视频", code="INVALID_MEDIA")
                    mode_param = _api_str(params, "mode", default="").lower()
                    res_plain = resolution.replace("p", "").replace("P", "")
                    if mode_param not in ("std", "pro"):
                        mode_param = "pro" if res_plain == "1080" else "std"
                    video_url = await kling_reference_video_generate(
                        model=upstream,
                        prompt=req.prompt,
                        refer_image_urls=image_refs[:7],
                        duration_sec=duration,
                        resolution=resolution,
                        aspect_ratio=ratio,
                        audio=bool(params.get("audio", False)),
                        watermark=watermark,
                        mode=mode_param,
                        feature_video_url=feature_video,
                    )
                    data, content_type = await download_bytes(video_url)
                    return VideoGenResult(
                        data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url
                    )
                if model_id == "pixverse_r2v":
                    for i, u in enumerate(refs[:7]):
                        media.append({"type": "image_url", "url": u, "ref_name": f"ref{i + 1}"})
                elif model_id == "happyhorse_r2v":
                    for u in refs[:9]:
                        media.append({"type": "reference_image", "url": u})
                else:
                    for u in refs[:5]:
                        media.append({"type": "reference_image", "url": u})
                if not media:
                    raise UpstreamError("参考生视频需要参考图", code="INVALID_MEDIA")
                parameters = {
                    "resolution": _resolution_param(resolution),
                    "duration": max(3, min(15, duration)) if model_id == "happyhorse_r2v" else max(2, min(15, duration)),
                    "watermark": watermark,
                }
                if model_id in ("wan27_r2v", "happyhorse_r2v", "pixverse_r2v"):
                    ratio_val = (ratio or "16:9").strip()
                    if model_id == "happyhorse_r2v" and ratio_val not in ("16:9", "9:16", "3:4", "4:3", "1:1"):
                        ratio_val = "16:9"
                    parameters["ratio"] = ratio_val
            elif model_id == "kling_i2v" or (use_kling and spec.video_mode == "i2v"):
                if not first_url:
                    raise UpstreamError("可灵图生视频需要首帧", code="INVALID_MEDIA")
                media = [{"type": "first_frame", "url": first_url}]
                if last_url and last_url != first_url:
                    media.append({"type": "last_frame", "url": last_url})
                res_plain = resolution.replace("p", "").replace("P", "")
                parameters = {
                    "mode": "pro" if res_plain == "1080" else "std",
                    "duration": max(3, min(15, duration)),
                    "audio": bool(params.get("audio", False)),
                    "watermark": watermark,
                }
            elif model_id in ("wan27_i2v", "pixverse_i2v"):
                if not first_url:
                    raise UpstreamError("图生视频需要首帧", code="INVALID_MEDIA")
                media = [{"type": "first_frame", "url": first_url}]
                if last_url:
                    media.append({"type": "last_frame", "url": last_url})
                parameters = {
                    "resolution": _resolution_param(resolution),
                    "duration": max(2, min(15, duration)),
                    "watermark": watermark,
                }
                if model_id == "pixverse_i2v":
                    parameters["generate_audio"] = bool(params.get("audio", True))
            elif model_id == "happyhorse_i2v":
                if not first_url:
                    raise UpstreamError("HappyHorse 图生视频需要首帧", code="INVALID_MEDIA")
                media = [{"type": "first_frame", "url": first_url}]
                parameters = {
                    "resolution": _resolution_param(resolution),
                    "duration": max(3, min(15, duration)),
                    "watermark": watermark,
                }
            else:
                raise ProviderNotImplementedError(req.model_id, str(spec.video_mode or "video"))

            video_url = await dashscope_video_generate(
                model=upstream,
                prompt=req.prompt,
                media=media,
                parameters=parameters,
                use_kling_key=use_kling,
            )
            data, content_type = await download_bytes(video_url)
            return VideoGenResult(data=data, content_type=content_type or "video/mp4", ext="mp4", source_url=video_url)

    except UpstreamError:
        raise

    raise ProviderNotImplementedError(req.model_id, str(spec.video_mode or "video"))


async def generate_audio(req: AudioGenRequest) -> AudioGenResult:
    """按模型规格路由到 CosyVoice / RunningHub 音乐等上游。"""
    spec = get_model_spec(req.model_id)
    if not spec:
        raise ProviderNotImplementedError(req.model_id, "text_to_speech")

    params = req.api_params or {}

    route = (spec.parameters_extra or {}).get("runninghub_route")

    # RunningHub CN：分离音频 Vocals / Other（配置必填 videoUrl → 轮询取音频）
    if route and "audio_separate" in (spec.capabilities or ()):
        video_url = (
            _api_str(params, "videoUrl", "video_url", "sourceUrl", "source_url", default="")
            or (req.reference_audio_url or "").strip()
        )
        if not video_url:
            raise UpstreamError("分离音频需要源视频 URL（videoUrl）", code="INVALID_MEDIA")
        audio_url = await runninghub_audio_extract(route=str(route), video_url=video_url)
        data, content_type = await download_bytes(audio_url, max_bytes=50 * 1024 * 1024)
        return AudioGenResult(
            data=data,
            content_type=content_type or "audio/mpeg",
            ext="mp3",
            source_url=audio_url,
        )

    # RunningHub 海外版音乐（MiniMax / Suno）
    if route and "text_to_music" in (spec.capabilities or ()):
        if route == "suno_custom_v55":
            # 对齐 RunningHub suno-v5.5/custom：title / prompt(歌词) / tags 均必填
            title = _api_str(params, "title", default="").strip()
            tags = _api_str(params, "tags", default="").strip()
            # 歌词优先 generationOptions.lyrics，否则主 prompt（req.text）
            lyrics = (
                _api_str(params, "lyrics", default="").strip()
                or (req.text or "").strip()
            )
            if not title:
                raise UpstreamError("Suno 需要填写歌曲标题（title）", code="INVALID_MEDIA")
            if not lyrics:
                raise UpstreamError("Suno 需要填写完整歌词（prompt）", code="INVALID_MEDIA")
            if not tags:
                raise UpstreamError("Suno 需要填写风格标签（tags）", code="INVALID_MEDIA")
            payload = {
                "title": title[:80],
                "prompt": lyrics[:5000],
                "tags": tags[:1000],
            }
        else:
            # MiniMax Music 2.6（配置：prompt 风格 / lyrics 歌词 / 采样率码率格式等）
            is_instrumental = _api_bool(
                params, "isInstrumental", "is_instrumental", default=False
            )
            lyrics_optimizer = _api_bool(
                params, "lyricsOptimizer", "lyrics_optimizer", default=False
            )
            # 风格描述：优先显式字段，否则用提交 prompt（主输入框）
            style_prompt = (
                _api_str(params, "musicPrompt", "stylePrompt", "prompt", default="")
                or (req.text or "").strip()
            )
            # 歌词：歌曲模式必填（由前端 lyrics 字段传入）；纯音乐清空
            lyrics = _api_str(params, "lyrics", default="")
            if is_instrumental:
                lyrics = ""
                if not style_prompt:
                    raise UpstreamError(
                        "纯音乐需要填写风格描述（prompt）",
                        code="INVALID_MEDIA",
                    )
            elif not lyrics:
                raise UpstreamError(
                    "歌曲模式需要填写歌词（lyrics）",
                    code="INVALID_MEDIA",
                )
            sample_rate = _api_str(params, "sampleRate", "sample_rate", default="44100") or "44100"
            if sample_rate not in ("16000", "24000", "32000", "44100"):
                sample_rate = "44100"
            bitrate = _api_str(params, "bitrate", default="256000") or "256000"
            if bitrate not in ("32000", "64000", "128000", "256000"):
                bitrate = "256000"
            fmt = (_api_str(params, "format", default="mp3") or "mp3").lower()
            if fmt not in ("mp3", "wav", "pcm"):
                fmt = "mp3"
            payload = {
                "prompt": style_prompt[:2000],
                "lyrics": lyrics[:20000],
                "sampleRate": sample_rate,
                "bitrate": bitrate,
                "format": fmt,
                "lyricsOptimizer": lyrics_optimizer,
                "isInstrumental": is_instrumental,
            }
        audio_url = await runninghub_music_generate(route=route, payload=payload)
        data, content_type = await download_bytes(audio_url, max_bytes=50 * 1024 * 1024)
        fmt_out = str(payload.get("format") or _api_str(params, "format", default="mp3") or "mp3").lower()
        if fmt_out == "pcm":
            ext = "pcm"
            ctype = content_type or "audio/pcm"
        elif fmt_out == "wav" or "wav" in (content_type or ""):
            ext = "wav"
            ctype = content_type or "audio/wav"
        else:
            ext = "mp3"
            ctype = content_type or "audio/mpeg"
        return AudioGenResult(data=data, content_type=ctype, ext=ext, source_url=audio_url)

    if req.model_id == "cosyvoice_tts":
        try:
            params = req.api_params or {}
            voice = (
                req.voice_id
                or _api_str(params, "voice", default="")
                or _api_str(params, "voiceId", default="")
                or "longanyang"
            )
            # 复刻音色走 v3.5-plus；系统预置音色走 v3-flash
            tts_model = (
                _api_str(params, "ttsModel", default="")
                or _api_str(params, "tts_model", default="")
                or (
                    CLONE_TARGET_MODEL
                    if _api_str(params, "voiceKind", default="") == "cloned"
                    else (spec.upstream_model or PRESET_TTS_MODEL)
                )
            )
            ctrl = cosyvoice_controls_from_params(params)
            audio_url = await cosyvoice_tts(
                model=tts_model,
                voice=voice,
                text=req.text,
                rate=ctrl["rate"],
                pitch=ctrl["pitch"],
                volume=ctrl["volume"],
            )
            data, content_type = await download_bytes(audio_url, max_bytes=30 * 1024 * 1024)
            ext = "mp3" if "mpeg" in content_type or "mp3" in content_type else "wav"
            return AudioGenResult(
                data=data,
                content_type=content_type,
                ext=ext,
                source_url=audio_url,
                voice_id=voice,
            )
        except UpstreamError:
            raise

    if req.model_id == "cosyvoice_clone":
        # 声音复刻：参考音 → voice_id，并用新音色合成一段试听写入素材
        try:
            ref = (req.reference_audio_url or "").strip()
            if not ref:
                ref = _api_str(req.api_params or {}, "sourceUrl", default="") or _api_str(
                    req.api_params or {}, "audioUrl", default=""
                )
            if not ref:
                raise UpstreamError("声音复刻需要参考音频 URL", code="INVALID_INPUT")
            prefix = _api_str(req.api_params or {}, "prefix", default="") or "jm"
            voice_id = await cosyvoice_create_voice(audio_url=ref, prefix=prefix)
            # 用节点文本框内容合成试听；无文本时给默认句
            preview_text = (req.text or "").strip() or "你好，这是用新复刻音色合成的试听。"
            ctrl = cosyvoice_controls_from_params(req.api_params or {})
            audio_url = await cosyvoice_tts(
                model=CLONE_TARGET_MODEL,
                voice=voice_id,
                text=preview_text[:5000],
                rate=ctrl["rate"],
                pitch=ctrl["pitch"],
                volume=ctrl["volume"],
            )
            data, content_type = await download_bytes(audio_url, max_bytes=30 * 1024 * 1024)
            ext = "mp3" if "mpeg" in content_type or "mp3" in content_type else "wav"
            return AudioGenResult(
                data=data,
                content_type=content_type,
                ext=ext,
                source_url=audio_url,
                voice_id=voice_id,
            )
        except UpstreamError:
            raise

    raise ProviderNotImplementedError(req.model_id, "text_to_speech")


__all__ = [
    "ProviderNotImplementedError",
    "generate_audio",
    "generate_image",
    "generate_text",
    "generate_video",
    "is_capability_implemented",
]
