"""百炼 DashScope 集成：万相图片、视频合成、CosyVoice 语音。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from .credentials import dashscope_creds, kling_creds
from .errors import UpstreamError
from .trace_context import note_upstream, maybe_flush_upstream_trace, note_upstream_billing_from_response

logger = logging.getLogger(__name__)

VIDEO_SYNTHESIS = "/services/aigc/video-generation/video-synthesis"
WAN_IMAGE = "/services/aigc/image-generation/generation"
# 千问 Qwen-Image 3.0：同步 multimodal-generation（文生图/图生图）
QWEN_IMAGE = "/services/aigc/multimodal-generation/generation"
SPEECH = "/services/audio/tts/SpeechSynthesizer"
CUSTOMIZATION = "/services/audio/tts/customization"
VOICE_ENROLLMENT_MODEL = "voice-enrollment"
# 声音复刻 target_model / 复刻音色合成：cosyvoice-v3.5-plus（官方无系统预置音色）
CLONE_TARGET_MODEL = "cosyvoice-v3.5-plus"
# 语音合成选官方音色：必须用 cosyvoice-v3-flash（与官方预置音色列表绑定，不可与 v3.5-plus 混用）
PRESET_TTS_MODEL = "cosyvoice-v3-flash"


def _resolution_param(resolution: str) -> str:
    """画布清晰度 → 百炼 resolution 枚举（480P/720P/1080P）。"""
    s = (resolution or "720").strip().lower().replace("p", "")
    if s == "1080":
        return "1080P"
    if s == "480":
        return "480P"
    return "720P"


def _first_image_url(output: dict[str, Any]) -> str:
    urls = _all_image_urls(output)
    return urls[0] if urls else ""


def _all_image_urls(output: dict[str, Any]) -> list[str]:
    """从 output.choices[].message.content[] 提取图片 URL（支持 n>1）。

    兼容两种形态：
    - 千问 Qwen-Image：`{"image": "https://..."}`（无 type）
    - 万相等：`{"type": "image", "image": "https://..."}`
    """
    urls: list[str] = []
    for choice in output.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        msg = choice.get("message") or {}
        content = msg.get("content")
        # 少数响应把 content 写成单个对象而非数组
        blocks = content if isinstance(content, list) else ([content] if isinstance(content, dict) else [])
        for block in blocks:
            if not isinstance(block, dict):
                continue
            raw_type = str(block.get("type") or "").strip().lower()
            # 有 type 时仅接受图片类；无 type 时只要有 image 字段即视为图片（千问）
            if raw_type and raw_type not in ("image", "image_url"):
                continue
            u = str(block.get("image") or block.get("image_url") or block.get("url") or "").strip()
            if u.startswith("http://") or u.startswith("https://"):
                urls.append(u)
    if urls:
        return urls
    # 兜底：results / images 直出 URL 列表
    for key in ("results", "images"):
        for item in output.get(key) or []:
            if isinstance(item, dict):
                u = str(item.get("url") or item.get("image") or "").strip()
            else:
                u = str(item or "").strip()
            if u.startswith("http://") or u.startswith("https://"):
                urls.append(u)
    return urls


async def wan_image_generate(
    *,
    model: str,
    prompt: str,
    reference_urls: list[str] | None = None,
    size: str = "2K",
    watermark: bool = False,
    n: int = 1,
    poll_interval_s: float = 2.0,
    poll_max_s: float = 900.0,
) -> list[str]:
    """万相异步图片生成：提交任务后轮询直至返回图片 URL 列表。"""
    creds = dashscope_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 DASHSCOPE_API_KEY", code="NOT_CONFIGURED")

    count = max(1, min(int(n or 1), 4))
    content: list[dict[str, Any]] = []
    for u in reference_urls or []:
        if u:
            content.append({"image": u})
    content.append({"text": (prompt or "").strip()[:5000]})

    body = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"size": size or "2K", "n": count, "watermark": bool(watermark)},
    }
    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    create_url = f"{creds.api_base}{WAN_IMAGE}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
        logger.info("[upstream] POST %s model=%s kind=image", create_url, model)
        resp = await client.post(create_url, headers=headers, json=body)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code"):
            msg = str(data.get("message") or resp.text or "万相图片提交失败")[:500]
            raise UpstreamError(msg, code=str(data.get("code") or "DASHSCOPE_ERROR"))

        out = data.get("output") or {}
        task_id = str(out.get("task_id") or out.get("taskId") or "").strip()
        if not task_id:
            raise UpstreamError("万相未返回 task_id", code="MISSING_TASK_ID")
        # 提交成功后立即落库 task_id，防止崩溃后重试造成二次提交
        note_upstream(provider="dashscope", provider_task_id=task_id, event="submit")
        await maybe_flush_upstream_trace()

        task_url = f"{creds.api_base}/tasks/{task_id}"
        poll_headers = {"Authorization": f"Bearer {creds.api_key}"}
        deadline = asyncio.get_running_loop().time() + poll_max_s

        while asyncio.get_running_loop().time() < deadline:
            r2 = await client.get(task_url, headers=poll_headers)
            body2 = r2.json() if r2.content else {}
            if r2.status_code >= 400 or body2.get("code"):
                raise UpstreamError(str(body2.get("message") or "轮询失败")[:500], code="POLL_ERROR")
            out2 = body2.get("output") or {}
            st = str(out2.get("task_status") or out2.get("taskStatus") or "").upper()
            if st in ("SUCCEEDED", "SUCCESS"):
                note_upstream_billing_from_response(body2, provider="dashscope", event="image_poll_succeeded")
                urls = _all_image_urls(out2)
                if not urls:
                    raise UpstreamError("任务成功但未返回图片 URL", code="EMPTY_OUTPUT")
                return urls
            if st in ("FAILED", "CANCELED", "CANCELLED"):
                raise UpstreamError(str(out2.get("message") or "万相图片生成失败")[:500], code="TASK_FAILED")
            await asyncio.sleep(poll_interval_s)

    raise UpstreamError("万相图片生成超时", code="TIMEOUT")


async def qwen_image_generate(
    *,
    model: str,
    prompt: str,
    reference_urls: list[str] | None = None,
    size: str | None = None,
    watermark: bool = False,
    n: int = 1,
    prompt_extend: bool = True,
    negative_prompt: str | None = None,
) -> list[str]:
    """千问图像 3.0 同步生成：POST multimodal-generation，返回图片 URL 列表。

    官方模型：`qwen-image-3.0` / `qwen-image-3.0-pro`；参考图最多 3 张；n 为 1–6。
    文档：https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference
    """
    creds = dashscope_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 DASHSCOPE_API_KEY", code="NOT_CONFIGURED")

    count = max(1, min(int(n or 1), 6))
    # 图生图：最多 3 张参考图 + 一条 text
    content: list[dict[str, Any]] = []
    for u in (reference_urls or [])[:3]:
        if u:
            content.append({"image": u})
    text = (prompt or "").strip()[:4500]
    if not text:
        raise UpstreamError("提示词不能为空", code="EMPTY_PROMPT")
    content.append({"text": text})

    parameters: dict[str, Any] = {
        "n": count,
        "watermark": bool(watermark),
        "prompt_extend": bool(prompt_extend),
    }
    size_s = (size or "").strip()
    if size_s:
        parameters["size"] = size_s
    neg = (negative_prompt or "").strip()
    if neg:
        parameters["negative_prompt"] = neg[:500]

    body = {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }
    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{creds.api_base}{QWEN_IMAGE}"

    # 2K + 参考图 + 长提示词常见 >180s；超时须写清文案（httpx.ReadTimeout 默认 message 为空）
    qwen_timeout = httpx.Timeout(360.0, connect=20.0)
    async with httpx.AsyncClient(timeout=qwen_timeout) as client:
        logger.info("[upstream] POST %s model=%s kind=qwen_image n=%s", url, model, count)
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                "千问图像生成超时（上游响应过慢，请稍后重试或缩短提示词/降低清晰度）",
                code="TIMEOUT",
            ) from exc
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code"):
            msg = str(data.get("message") or resp.text or "千问图片生成失败")[:500]
            raise UpstreamError(msg, code=str(data.get("code") or "DASHSCOPE_ERROR"))

        req_id = str(
            data.get("request_id")
            or resp.headers.get("x-request-id")
            or resp.headers.get("request-id")
            or ""
        ).strip()
        if req_id:
            # 同步接口无异步 task_id；记录 request_id 便于对账
            note_upstream(provider="dashscope", provider_request_id=req_id, event="qwen_image_submit")
        note_upstream_billing_from_response(data, provider="dashscope", event="qwen_image_succeeded")
        await maybe_flush_upstream_trace()

        out = data.get("output") or {}
        urls = _all_image_urls(out)
        if not urls:
            raise UpstreamError("千问未返回图片 URL", code="EMPTY_OUTPUT")
        return urls


async def _video_synthesis_submit(
    creds,
    *,
    model: str,
    input_body: dict[str, Any],
    parameters: dict[str, Any],
) -> str:
    if not creds.api_key:
        raise UpstreamError("未配置百炼 API Key", code="NOT_CONFIGURED")
    url = f"{creds.api_base}{VIDEO_SYNTHESIS}"
    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    body = {"model": model, "input": input_body, "parameters": parameters}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        logger.info("[upstream] POST %s model=%s kind=video", url, model)
        resp = await client.post(url, headers=headers, json=body)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code"):
            raise UpstreamError(str(data.get("message") or resp.text or "视频提交失败")[:500], code="SUBMIT_ERROR")
        req_id = str(resp.headers.get("x-request-id") or resp.headers.get("request-id") or "").strip()
        if req_id:
            note_upstream(provider="dashscope", provider_request_id=req_id, event="submit_response")
        out = data.get("output") or {}
        task_id = str(out.get("task_id") or "").strip()
        if not task_id:
            raise UpstreamError("未返回 task_id", code="MISSING_TASK_ID")
        # 记录 submit 事件后立即 flush，把 provider_task_id 落库；
        # 即使随后进程崩溃，重试也能凭该 id 续轮询而不会二次提交
        note_upstream(provider="dashscope", provider_task_id=task_id, event="submit")
        await maybe_flush_upstream_trace()
        return task_id


def _parse_dashscope_video_task(data: dict[str, Any]) -> tuple[str, str | None, str | None]:
    out = data.get("output") if isinstance(data.get("output"), dict) else {}
    raw = str(out.get("task_status") or data.get("task_status") or "").strip().lower()
    st_map = {
        "succeeded": "SUCCEEDED",
        "success": "SUCCEEDED",
        "completed": "SUCCEEDED",
        "failed": "FAILED",
        "canceled": "FAILED",
        "cancelled": "FAILED",
        "running": "RUNNING",
        "processing": "RUNNING",
        "pending": "PENDING",
        "queued": "PENDING",
    }
    st = st_map.get(raw, raw.upper() or "UNKNOWN")
    video = str(out.get("video_url") or out.get("videoUrl") or "").strip() or None
    err = str(out.get("message") or data.get("message") or "").strip() or None
    return st, video, err


async def poll_dashscope_task(task_id: str, *, creds=None, poll_interval_s: float = 3.0, poll_max_s: float = 1200.0) -> str:
    """仅按 task_id 轮询百炼视频任务直至完成，返回视频 URL；不含任何 submit。"""
    creds = creds or dashscope_creds()
    url = f"{creds.api_base}/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {creds.api_key}"}
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            resp = await client.get(url, headers=headers)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400 or data.get("code"):
                raise UpstreamError(str(data.get("message") or "查询失败")[:500], code="POLL_ERROR")
            st, video, err = _parse_dashscope_video_task(data)
            if st == "SUCCEEDED":
                note_upstream_billing_from_response(data, provider="dashscope", event="video_poll_succeeded")
                if not video:
                    raise UpstreamError("任务成功但未返回视频 URL", code="EMPTY_OUTPUT")
                return video
            if st == "FAILED":
                raise UpstreamError(err or "视频生成失败", code="TASK_FAILED")
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("视频生成超时", code="TIMEOUT")


async def dashscope_video_generate(
    *,
    model: str,
    prompt: str,
    media: list[dict[str, Any]],
    parameters: dict[str, Any],
    use_kling_key: bool = False,
    input_body: dict[str, Any] | None = None,
) -> str:
    """百炼视频合成完整链路：提交一次 task_id 后轮询直至返回视频 URL。"""
    creds = kling_creds() if use_kling_key else dashscope_creds()
    body_input = input_body if input_body is not None else {"prompt": (prompt or "").strip()[:5000], "media": media}
    # 全新任务的完整链路：提交一次拿 task_id，再轮询等待结果
    task_id = await _video_synthesis_submit(creds, model=model, input_body=body_input, parameters=parameters)
    return await poll_dashscope_task(task_id, creds=creds)


def _build_kling_reference_video_prompt(
    user_prompt: str, refer_image_count: int, *, has_feature_video: bool
) -> str:
    """Omni 参考生：prompt 中的 <<<video_1>>> / <<<image_n>>> 与 media 数组顺序一致。"""
    up = (user_prompt or "").strip()
    parts: list[str] = []
    if has_feature_video:
        parts.append("特征参考视频<<<video_1>>>")
    if refer_image_count > 0:
        imgs = "、".join([f"<<<image_{i + 1}>>>" for i in range(refer_image_count)])
        parts.append(f"参考图{imgs}")
    head = f"结合{'与'.join(parts)}。" if parts else ""
    return f"{head}{up}"[:2500]


async def kling_reference_video_generate(
    *,
    model: str,
    prompt: str,
    refer_image_urls: list[str],
    duration_sec: int,
    resolution: str,
    aspect_ratio: str,
    audio: bool = False,
    watermark: bool = False,
    feature_video_url: str = "",
    keep_original_sound: str = "no",
    mode: str | None = None,
) -> str:
    """可灵 V3-Omni 参考生：media 项 type 为 refer（非 reference_image）。"""
    fv = (feature_video_url or "").strip()
    refers = [str(u or "").strip() for u in refer_image_urls if str(u or "").strip()]
    if not refers:
        raise UpstreamError("可灵参考生至少需要 1 张参考图", code="INVALID_MEDIA")
    if fv and len(refers) > 4:
        raise UpstreamError("feature+refer 组合下参考图最多 4 张", code="INVALID_MEDIA")
    if not fv and len(refers) > 7:
        raise UpstreamError("仅 refer 组合下参考图最多 7 张", code="INVALID_MEDIA")

    ar = (aspect_ratio or "16:9").strip()
    if ar not in ("16:9", "9:16", "1:1"):
        ar = "16:9"
    res_plain = str(resolution or "720").strip().replace("p", "").replace("P", "") or "720"
    mode_val = (mode or "").strip().lower() or ("pro" if res_plain == "1080" else "std")
    if mode_val not in ("std", "pro"):
        mode_val = "pro" if res_plain == "1080" else "std"
    dur = int(max(3, min(15, int(duration_sec))))
    kos = (keep_original_sound or "no").strip().lower()
    if kos not in ("yes", "no"):
        kos = "no"

    media: list[dict[str, Any]] = []
    if fv:
        media.append({"type": "feature", "url": fv, "keep_original_sound": kos})
    for u in refers:
        media.append({"type": "refer", "url": u})

    prompt_body = _build_kling_reference_video_prompt(prompt, len(refers), has_feature_video=bool(fv))
    input_body = {
        "prompt": prompt_body,
        "multi_shot": False,
        "shot_type": "intelligence",
        "multi_prompt": [],
        "media": media,
        "element_list": [],
    }
    parameters = {
        "mode": mode_val,
        "duration": dur,
        "audio": bool(audio),
        "aspect_ratio": ar,
        "watermark": bool(watermark),
    }
    return await dashscope_video_generate(
        model=model,
        prompt=prompt_body,
        media=media,
        parameters=parameters,
        use_kling_key=True,
        input_body=input_body,
    )


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(lo, min(hi, v))


def cosyvoice_controls_from_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """画布 audioGenParams → CosyVoice HTTP：rate / pitch / volume。

    - 语速 speechRate → rate（0.5～2）
    - 声调 tone + 音高 pitch（半音 -12～12）→ pitch = 2^((tone+pitch)/12)（0.5～2）
    - 音量 volume（0～2，默认 1）→ volume（0～100，默认 50）
    """
    p = params if isinstance(params, dict) else {}
    rate = _clamp_float(p.get("speechRate", p.get("speech_rate", p.get("rate"))), 0.5, 2.0, 1.0)
    tone = _clamp_float(p.get("tone"), -12.0, 12.0, 0.0)
    pitch_semi = _clamp_float(p.get("pitch"), -12.0, 12.0, 0.0)
    semitone = max(-12.0, min(12.0, tone + pitch_semi))
    pitch = max(0.5, min(2.0, 2.0 ** (semitone / 12.0)))
    vol_ui = _clamp_float(p.get("volume"), 0.0, 2.0, 1.0)
    volume = int(round(max(0.0, min(100.0, vol_ui * 50.0))))
    return {"rate": round(rate, 3), "pitch": round(pitch, 4), "volume": volume}


async def cosyvoice_tts(
    *,
    model: str,
    voice: str,
    text: str,
    rate: float | None = None,
    pitch: float | None = None,
    volume: int | None = None,
) -> str:
    """调用 CosyVoice 语音合成并返回音频 URL。"""
    creds = dashscope_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 DASHSCOPE_API_KEY", code="NOT_CONFIGURED")
    inp: dict[str, Any] = {
        "text": (text or "").strip(),
        "voice": (voice or "").strip() or "longanyang",
        "format": "mp3",
        "sample_rate": 24000,
    }
    # 官方可调：语速 / 音调 / 音量（与画布「基础调节」「音高」对齐）
    if rate is not None:
        inp["rate"] = _clamp_float(rate, 0.5, 2.0, 1.0)
    if pitch is not None:
        inp["pitch"] = _clamp_float(pitch, 0.5, 2.0, 1.0)
    if volume is not None:
        try:
            inp["volume"] = int(max(0, min(100, int(volume))))
        except (TypeError, ValueError):
            pass
    body = {
        "model": model,
        "input": inp,
    }
    url = f"{creds.api_base}{SPEECH}"
    headers = {"Authorization": f"Bearer {creds.api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
        logger.info("[upstream] POST %s model=%s kind=tts", url, model)
        resp = await client.post(url, headers=headers, json=body)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code"):
            raise UpstreamError(str(data.get("message") or "语音合成失败")[:500], code="TTS_ERROR")
        note_upstream_billing_from_response(data, provider="dashscope", event="tts_succeeded")
        # 百炼非流式：output.audio.url（非顶层 audio_url）；兼容 SDK/旧字段
        audio_url = _cosyvoice_audio_url_from_response(data)
        if not audio_url:
            raise UpstreamError("未返回音频 URL", code="EMPTY_OUTPUT")
        return audio_url


def _cosyvoice_audio_url_from_response(data: dict[str, Any]) -> str:
    """从 CosyVoice SpeechSynthesizer 响应中提取音频下载 URL。

    官方非流式结构：``output.audio.url``（有效期约 24h）。
    兼容：``output.audio_url`` / ``output.url`` / 顶层同名字段。
    """
    out = data.get("output") if isinstance(data.get("output"), dict) else {}
    audio = out.get("audio") if isinstance(out.get("audio"), dict) else {}
    for candidate in (
        audio.get("url"),
        out.get("audio_url"),
        out.get("url"),
        data.get("audio_url"),
        data.get("url"),
    ):
        s = str(candidate or "").strip()
        if s.startswith("http://") or s.startswith("https://"):
            return s
    return ""


def _normalize_voice_enrollment_prefix(prefix: str) -> str:
    """百炼 create_voice 的 prefix：仅 a-z0-9，2～10 字符。"""
    import re
    import uuid as _uuid

    s = re.sub(r"[^a-z0-9]+", "", (prefix or "").lower())[:10]
    if len(s) >= 2:
        return s
    return "v" + _uuid.uuid4().hex[:9]


def _extract_voice_id(data: dict[str, Any]) -> str:
    out = data.get("output")
    if isinstance(out, dict):
        for key in ("voice_id", "voiceId", "voice", "id"):
            v = str(out.get(key) or "").strip()
            if v:
                return v
    for key in ("voice_id", "voiceId", "voice", "id"):
        v = str(data.get(key) or "").strip()
        if v:
            return v
    return ""


async def cosyvoice_create_voice(
    *,
    audio_url: str,
    prefix: str,
    language_hints: list[str] | None = None,
    target_model: str = CLONE_TARGET_MODEL,
) -> str:
    """百炼声音复刻 create_voice，返回上游 voice_id。

    参考音须为公网可访问 URL（阿里云 OSS 建议开启 X-DashScope-OssResourceResolve）。
    """
    creds = dashscope_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 DASHSCOPE_API_KEY", code="NOT_CONFIGURED")
    url_in = (audio_url or "").strip()
    if not url_in:
        raise UpstreamError("参考音频 URL 不能为空", code="INVALID_INPUT")

    hints = [str(h or "").strip().lower()[:8] for h in (language_hints or ["zh"])]
    hints = [h for h in hints if h] or ["zh"]

    body: dict[str, Any] = {
        "model": VOICE_ENROLLMENT_MODEL,
        "input": {
            "action": "create_voice",
            "target_model": (target_model or CLONE_TARGET_MODEL).strip() or CLONE_TARGET_MODEL,
            "prefix": _normalize_voice_enrollment_prefix(prefix),
            "url": url_in,
            "language_hints": hints[:1],
        },
    }
    url = f"{creds.api_base}{CUSTOMIZATION}"
    headers = {
        "Authorization": f"Bearer {creds.api_key}",
        "Content-Type": "application/json",
        # 便于百炼拉取同账号 OSS 上的参考音
        "X-DashScope-OssResourceResolve": "enable",
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
        logger.info("[upstream] POST %s model=%s kind=voice_clone", url, VOICE_ENROLLMENT_MODEL)
        resp = await client.post(url, headers=headers, json=body)
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {}
        err_code = str(data.get("code") or "").strip()
        if resp.status_code >= 400 or err_code:
            msg = str(data.get("message") or data.get("Message") or resp.text or "声音复刻失败")[:500]
            raise UpstreamError(msg, code=err_code or "VOICE_CLONE_ERROR")
        note_upstream_billing_from_response(data, provider="dashscope", event="voice_clone_succeeded")
        note_upstream(
            action="voice_clone",
            provider="dashscope",
            request_id=str(data.get("request_id") or ""),
        )
        voice_id = _extract_voice_id(data)
        if not voice_id:
            raise UpstreamError("百炼未返回 voice_id", code="MISSING_VOICE_ID")
        return voice_id
