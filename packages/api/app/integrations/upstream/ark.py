"""火山引擎 Ark 上游集成：Seedance 视频任务提交、轮询与请求体构建。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from ...core.llm_keys import get_llm_keys
from .credentials import ark_creds
from .errors import UpstreamError
from .trace_context import maybe_flush_upstream_trace, note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)


def _extract_seedance_error_text(data: dict[str, Any]) -> str | None:
    """从 Ark/华狐失败响应中提取可读错误文案（兼容 dict / JSON 字符串 / 顶层 message）。"""
    err = data.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or err.get("Message") or "").strip()
        code = str(err.get("code") or err.get("Code") or "").strip()
        if msg and code and code not in msg:
            return f"{code}: {msg}"
        if msg:
            return msg
        if code:
            return code
    if isinstance(err, str) and err.strip():
        raw = err.strip()
        # 华狐偶发把整段 {"code":"fail_to_fetch_task","message":"..."} 塞进 error 字符串
        if raw.startswith("{") and "message" in raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    nested = _extract_seedance_error_text(parsed)
                    if nested:
                        return nested
            except Exception:
                pass
        return raw[:800]
    top = str(data.get("message") or data.get("Message") or "").strip()
    return top or None


def _humanize_seedance_error(err: str | None, *, provider: str = "ark") -> str:
    """将上游隐私/真人拦截转为用户可操作的中文说明。"""
    raw = (err or "").strip()
    if not raw:
        return "Seedance 视频生成失败"
    low = raw.lower()
    # 华狐/方舟：真人参考图/视频隐私硬拦；与 RH realPersonMode（1505）不是同一套机制
    if (
        "privacyinformation" in low.replace("_", "")
        or "may contain real person" in low
        or "contain real person" in low
    ):
        channel = "华狐" if provider == "huahu" else "方舟"
        return (
            f"{channel}渠道拒绝：参考图/视频被判定含真人肖像（隐私审核）。"
            "当前「真人模式」开关无法绕过该限制；"
            "请改用「RH Seedance 2.0 多模态」并保持真人模式开启后重试。"
        )
    return raw[:500]


def _parse_seedance_task(data: dict[str, Any]) -> tuple[str, str | None, str | None]:
    raw = str(data.get("status") or data.get("task_status") or "").strip().lower()
    # 华狐 fail_to_fetch_task 可能只出现在 error.code，status 仍为空
    err_preview = _extract_seedance_error_text(data) or ""
    if not raw and (
        str(data.get("code") or "").strip().lower() == "fail_to_fetch_task"
        or "fail_to_fetch_task" in err_preview
    ):
        raw = "failed"
    st_map = {
        "succeeded": "SUCCEEDED",
        "success": "SUCCEEDED",
        "completed": "SUCCEEDED",
        "failed": "FAILED",
        "canceled": "FAILED",
        "cancelled": "FAILED",
        "expired": "FAILED",
        "running": "RUNNING",
        "processing": "RUNNING",
        "queued": "PENDING",
        "pending": "PENDING",
    }
    st = st_map.get(raw, raw.upper() or "UNKNOWN")
    video = None
    content = data.get("content")
    if isinstance(content, dict):
        video = str(content.get("video_url") or content.get("videoUrl") or "").strip() or None
    if not video and isinstance(data.get("output"), dict):
        video = str(data["output"].get("video_url") or "").strip() or None
    err = None
    if st == "FAILED":
        err = _extract_seedance_error_text(data)
    return st, video, err


async def poll_seedance_task(
    task_id: str,
    *,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 1200.0,
    creds: Any | None = None,
    provider: str = "ark",
) -> str:
    """仅按 task_id 轮询 Seedance 任务直至完成，返回视频 URL；不含任何 submit。

    creds/provider 可注入华狐等 Ark 兼容网关，默认走方舟 ARK。
    """
    resolved = creds or ark_creds()
    if not resolved.api_key:
        raise UpstreamError(
            "未配置 ARK_API_KEY" if provider == "ark" else f"未配置 {provider.upper()} API Key",
            code="NOT_CONFIGURED",
        )
    poll_url = f"{resolved.api_base.rstrip('/')}/contents/generations/tasks/{quote(task_id, safe='')}"
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            r2 = await client.get(poll_url, headers={"Authorization": f"Bearer {resolved.api_key}"})
            body2 = r2.json() if r2.content else {}
            if r2.status_code >= 400:
                raise UpstreamError(str(r2.text or "查询失败")[:500], code="POLL_ERROR")
            st, video, err = _parse_seedance_task(body2)
            if st == "SUCCEEDED":
                note_upstream_billing_from_response(body2, provider=provider, event="poll_succeeded")
                if not video:
                    raise UpstreamError("任务成功但未返回视频 URL", code="EMPTY_OUTPUT")
                return video
            if st == "FAILED":
                raise UpstreamError(
                    _humanize_seedance_error(err, provider=provider),
                    code="TASK_FAILED",
                )
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("Seedance 视频生成超时", code="TIMEOUT")


async def seedance_video_generate(
    *,
    body: dict[str, Any],
    poll_interval_s: float = 5.0,
    poll_max_s: float = 1200.0,
    creds: Any | None = None,
    provider: str = "ark",
) -> str:
    """提交 Seedance 视频生成任务并轮询直至完成，返回视频 URL。"""
    resolved = creds or ark_creds()
    if not resolved.api_key:
        raise UpstreamError(
            "未配置 ARK_API_KEY" if provider == "ark" else f"未配置 {provider.upper()} API Key",
            code="NOT_CONFIGURED",
        )

    create_url = f"{resolved.api_base.rstrip('/')}/contents/generations/tasks"
    headers = {"Authorization": f"Bearer {resolved.api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        logger.info(
            "[upstream] POST %s model=%s kind=video provider=%s",
            create_url,
            body.get("model"),
            provider,
        )
        resp = await client.post(create_url, headers=headers, json=body)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            # 提交阶段也可能直接返回真人隐私拦截
            raw_err = _extract_seedance_error_text(data) if isinstance(data, dict) else None
            if not raw_err:
                raw_err = str(resp.text or "Seedance 提交失败")
            raise UpstreamError(
                _humanize_seedance_error(raw_err, provider=provider),
                code="SUBMIT_ERROR",
            )
        task_id = str(data.get("id") or data.get("task_id") or "").strip()
        if not task_id:
            raise UpstreamError("未返回任务 ID", code="MISSING_TASK_ID")
        # 提交成功后立即落库 task_id，保证崩溃/重试只续轮询，不二次提交
        note_upstream(provider=provider, provider_task_id=task_id, event="submit")
        await maybe_flush_upstream_trace()

    # 全新任务：提交后接着轮询；重试任务由上层直接调用 poll_seedance_task
    return await poll_seedance_task(
        task_id,
        poll_interval_s=poll_interval_s,
        poll_max_s=poll_max_s,
        creds=resolved,
        provider=provider,
    )


def normalize_seedance_resolution(resolution: str | None) -> str:
    """将画布分辨率选项规范为 Ark/华狐 Seedance 枚举（480p/720p/1080p/4k）。"""
    raw = str(resolution or "720p").strip().lower().replace(" ", "")
    aliases = {
        "480": "480p",
        "720": "720p",
        "1080": "1080p",
        "native1080p": "1080p",
        "native4k": "4k",
        "2k": "2k",
        "4k": "4k",
    }
    if raw in aliases:
        return aliases[raw]
    if raw.endswith("p") and raw[:-1].isdigit():
        return raw
    if raw.isdigit():
        return f"{raw}p"
    return "720p"


def _mark_seedance_content_real_person(content: list[dict[str, Any]]) -> None:
    """真人模式：为图/视频/音频条目标注 subject_type=person（Ark 真人过白约定）。"""
    for item in content:
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "")
        if t in ("image_url", "video_url", "audio_url"):
            item["subject_type"] = "person"


def _finalize_seedance_body(
    body: dict[str, Any],
    *,
    real_person_mode: bool = False,
) -> dict[str, Any]:
    """统一补齐华狐/Ark 真人模式字段。"""
    if real_person_mode:
        content = body.get("content")
        if isinstance(content, list):
            _mark_seedance_content_real_person(content)
        # 聚合网关透传字段（与 RH realPersonMode 语义对齐）
        body["real_person"] = True
        body["realPersonMode"] = True
    return body


def build_seedance_t2v_body(
    *,
    model: str,
    prompt: str,
    resolution: str = "720p",
    ratio: str = "16:9",
    duration: int = 5,
    watermark: bool = False,
    generate_audio: bool = True,
    real_person_mode: bool = False,
) -> dict[str, Any]:
    """构建 Seedance 文生视频（T2V）请求体。"""
    res = normalize_seedance_resolution(resolution)
    body = {
        "model": model,
        "content": [{"type": "text", "text": (prompt or "").strip()}],
        "resolution": res,
        "ratio": ratio or "16:9",
        "duration": int(duration),
        "generate_audio": bool(generate_audio),
        "watermark": bool(watermark),
    }
    return _finalize_seedance_body(body, real_person_mode=real_person_mode)


def build_seedance_multimodal_body(
    *,
    model: str,
    prompt: str,
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
    resolution: str = "720p",
    ratio: str = "16:9",
    duration: int = 5,
    watermark: bool = False,
    generate_audio: bool = True,
    real_person_mode: bool = False,
) -> dict[str, Any]:
    """构建 Seedance 多模态参考生（图/视频/音频 + 文本）请求体。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": (prompt or "").strip()}]
    for u in image_urls or []:
        url = str(u or "").strip()
        if url:
            content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    for u in video_urls or []:
        url = str(u or "").strip()
        if url:
            content.append({"type": "video_url", "video_url": {"url": url}, "role": "reference_video"})
    for u in audio_urls or []:
        url = str(u or "").strip()
        if url:
            content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})
    res = normalize_seedance_resolution(resolution)
    body = {
        "model": model,
        "content": content,
        "resolution": res,
        "ratio": ratio or "16:9",
        "duration": int(duration),
        "generate_audio": bool(generate_audio),
        "watermark": bool(watermark),
    }
    return _finalize_seedance_body(body, real_person_mode=real_person_mode)


def build_seedance_r2v_body(
    *,
    model: str,
    prompt: str,
    reference_urls: list[str],
    resolution: str = "720p",
    ratio: str = "16:9",
    duration: int = -1,
    watermark: bool = False,
    generate_audio: bool = True,
    real_person_mode: bool = False,
) -> dict[str, Any]:
    """构建 Seedance 参考图生视频（R2V）请求体。"""
    content: list[dict[str, Any]] = [{"type": "text", "text": (prompt or "").strip()}]
    for u in reference_urls:
        content.append({"type": "image_url", "image_url": {"url": u}, "role": "reference_image"})
    res = normalize_seedance_resolution(resolution)
    body = {
        "model": model,
        "content": content,
        "resolution": res,
        "ratio": ratio or "16:9",
        "duration": duration,
        "generate_audio": bool(generate_audio),
        "watermark": bool(watermark),
    }
    return _finalize_seedance_body(body, real_person_mode=real_person_mode)


def build_seedance_i2v_body(
    *,
    model: str,
    prompt: str,
    first_frame_url: str,
    last_frame_url: str | None = None,
    resolution: str = "720p",
    ratio: str = "16:9",
    duration: int = -1,
    watermark: bool = False,
    generate_audio: bool = True,
    real_person_mode: bool = False,
) -> dict[str, Any]:
    """构建 Seedance 首尾帧生视频（I2V）请求体。"""
    content: list[dict[str, Any]] = [
        {"type": "text", "text": (prompt or "").strip()},
        {"type": "image_url", "image_url": {"url": first_frame_url}, "role": "first_frame"},
    ]
    if last_frame_url and last_frame_url != first_frame_url:
        content.append({"type": "image_url", "image_url": {"url": last_frame_url}, "role": "last_frame"})
    res = normalize_seedance_resolution(resolution)
    body = {
        "model": model,
        "content": content,
        "resolution": res,
        "ratio": ratio or "16:9",
        "duration": duration,
        "generate_audio": bool(generate_audio),
        "watermark": bool(watermark),
    }
    return _finalize_seedance_body(body, real_person_mode=real_person_mode)
