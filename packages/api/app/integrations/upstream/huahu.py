"""华狐 AI（ai.hwdrama.com）上游：Seedance 2.0 走 New API V1 视频接口。

对接文档：http://ai.hwdrama.com/docs
提交：POST {base}/video/generations （base 默认 https://api.example.com/v1）
轮询：GET  {base}/video/generations/{task_id}

历史曾用 Ark 兼容 `/api/v3/contents/generations/tasks`；现统一切到 V1。
画布侧仍用 Ark 风格 content 数组构建请求体，提交前转换为 V1 payload。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx

from .ark import (
    _extract_seedance_error_text,
    _humanize_seedance_error,
    build_seedance_i2v_body,
    build_seedance_multimodal_body,
    build_seedance_r2v_body,
    build_seedance_t2v_body,
)
from .credentials import huahu_creds
from .errors import UpstreamError
from .trace_context import maybe_flush_upstream_trace, note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)

DEFAULT_HUAHU_API_BASE = "https://api.example.com/v1"


def normalize_huahu_api_base(api_base: str | None) -> str:
    """将历史 /api/v3 等基址规范为 V1（…/v1）。"""
    raw = (api_base or DEFAULT_HUAHU_API_BASE).strip().rstrip("/")
    if not raw:
        return DEFAULT_HUAHU_API_BASE
    lower = raw.lower()
    for old in ("/api/v3", "/api/v1"):
        if lower.endswith(old):
            raw = raw[: -len(old)].rstrip("/")
            break
    if raw.lower().endswith("/v1"):
        return raw
    return f"{raw}/v1"


def _huahu_resolved_base() -> tuple[str, str]:
    """返回 (api_key, v1_api_base)。"""
    creds = huahu_creds()
    key = (creds.api_key or "").strip()
    if not key:
        raise UpstreamError("未配置 HUAHU API Key", code="NOT_CONFIGURED")
    return key, normalize_huahu_api_base(creds.api_base)


def _content_item_url(item: dict[str, Any], nested_key: str) -> str:
    nested = item.get(nested_key)
    if isinstance(nested, dict):
        return str(nested.get("url") or "").strip()
    return str(item.get("url") or "").strip()


def ark_seedance_body_to_v1_payload(body: dict[str, Any]) -> dict[str, Any]:
    """把 Ark 风格 Seedance body 转为 New API `/v1/video/generations` JSON。

    同时保留顶层 content / metadata.content，兼容仍透传 Ark content 的网关。
    """
    content = body.get("content") if isinstance(body.get("content"), list) else []
    prompt = ""
    images: list[str] = []
    image_roles: list[str] = []
    videos: list[str] = []
    audios: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        t = str(item.get("type") or "")
        if t == "text":
            text = str(item.get("text") or "").strip()
            if text:
                prompt = text
        elif t == "image_url":
            url = _content_item_url(item, "image_url")
            if url:
                images.append(url)
                image_roles.append(str(item.get("role") or "reference_image"))
        elif t == "video_url":
            url = _content_item_url(item, "video_url")
            if url:
                videos.append(url)
        elif t == "audio_url":
            url = _content_item_url(item, "audio_url")
            if url:
                audios.append(url)

    resolution = str(body.get("resolution") or "720p").strip() or "720p"
    ratio = str(body.get("ratio") or "16:9").strip() or "16:9"
    duration = body.get("duration", 5)
    try:
        duration_i = int(duration)
    except (TypeError, ValueError):
        duration_i = 5
    generate_audio = bool(body.get("generate_audio", True))
    watermark = bool(body.get("watermark", False))
    real_person = bool(body.get("real_person") or body.get("realPersonMode"))

    metadata: dict[str, Any] = {
        "ratio": ratio,
        "resolution": resolution,
        "duration": duration_i,
        "generate_audio": generate_audio,
        "watermark": watermark,
        "content": content,
    }
    payload: dict[str, Any] = {
        "model": str(body.get("model") or "").strip(),
        "prompt": prompt,
        "duration": duration_i,
        "seconds": str(duration_i),
        "size": resolution,
        "resolution": resolution,
        "ratio": ratio,
        "aspect_ratio": ratio,
        "generate_audio": generate_audio,
        "watermark": watermark,
        "content": content,
        "metadata": metadata,
    }
    if real_person:
        payload["real_person"] = True
        payload["realPersonMode"] = True
        metadata["real_person"] = True
        metadata["realPersonMode"] = True

    if images:
        payload["images"] = images
        payload["image"] = images[0]
        payload["image_urls"] = images
        # 多模态参考图（非首尾帧）需标注 imageMode=reference
        if any(r == "reference_image" for r in image_roles) and not any(
            r in ("first_frame", "last_frame") for r in image_roles
        ):
            metadata["imageMode"] = "reference"
    if videos:
        payload["videos"] = videos
        payload["video"] = videos[0]
        payload["video_urls"] = videos
        metadata["referenceVideos"] = videos
    if audios:
        payload["audios"] = audios
        payload["audio"] = audios[0]
        payload["audio_urls"] = audios
        metadata["referenceAudio"] = audios
    return payload


def _error_message(body: dict[str, Any], fallback: str) -> str:
    extracted = _extract_seedance_error_text(body)
    if extracted:
        return extracted
    data = body.get("data")
    if isinstance(data, dict):
        nested = _extract_seedance_error_text(data)
        if nested:
            return nested
        fail = str(data.get("fail_reason") or "").strip()
        # 成功时 fail_reason 可能是视频 URL，不当作错误
        if fail and not fail.startswith("http"):
            return fail[:500]
    return str(body.get("message") or body.get("msg") or fallback)[:500]


def _extract_task_id(body: dict[str, Any]) -> str:
    for key in ("task_id", "taskId", "id"):
        val = str(body.get(key) or "").strip()
        if val:
            return val
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("task_id", "taskId", "id"):
            val = str(data.get(key) or "").strip()
            if val:
                return val
    return ""


def _extract_video_url(body: dict[str, Any]) -> str:
    """从 V1 轮询/提交响应中提取视频 URL（兼容 New API / Unode 嵌套结构）。"""
    for key in ("url", "video_url", "videoUrl", "output_url", "result_url"):
        val = str(body.get(key) or "").strip()
        if val.startswith("http"):
            return val
    content = body.get("content")
    if isinstance(content, dict):
        val = str(content.get("video_url") or content.get("videoUrl") or "").strip()
        if val.startswith("http"):
            return val
    data = body.get("data")
    if isinstance(data, dict):
        # Unode：成功时 data.fail_reason 可能直接是视频 URL
        fail = str(data.get("fail_reason") or "").strip()
        if fail.startswith("http"):
            return fail
        nested = data.get("data")
        if isinstance(nested, dict):
            found = _extract_video_url(nested)
            if found:
                return found
        for key in ("url", "video_url", "videoUrl", "output", "fail_reason"):
            val = str(data.get(key) or "").strip()
            if val.startswith("http"):
                return val
        content = data.get("content")
        if isinstance(content, dict):
            val = str(content.get("video_url") or content.get("videoUrl") or "").strip()
            if val.startswith("http"):
                return val
    return ""


def _status_of(body: dict[str, Any]) -> str:
    for key in ("status", "task_status", "state"):
        val = str(body.get(key) or "").strip().lower()
        if val:
            return val
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("status", "task_status", "state"):
            val = str(data.get(key) or "").strip().lower()
            if val:
                return val
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("status", "task_status", "state"):
                val = str(nested.get(key) or "").strip().lower()
                if val:
                    return val
    return ""


def _normalize_status(raw: str) -> str:
    st = (raw or "").strip().lower()
    if st in ("succeeded", "success", "completed", "done", "finished"):
        return "SUCCEEDED"
    if st in ("failed", "error", "cancelled", "canceled", "expired"):
        return "FAILED"
    if st in ("running", "processing", "in_progress", "in-progress"):
        return "RUNNING"
    if st in ("queued", "pending", "submitted"):
        return "PENDING"
    return st.upper() or "UNKNOWN"


async def poll_huahu_seedance_task(
    task_id: str,
    *,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 1200.0,
) -> str:
    """华狐 Seedance V1：仅按 task_id 续轮询，禁止二次 submit。"""
    api_key, api_base = _huahu_resolved_base()
    tid = quote(str(task_id).strip(), safe="")
    # 主路径 video/generations；兼容 OpenAI videos 查询
    query_urls = [
        f"{api_base}/video/generations/{tid}",
        f"{api_base}/videos/{tid}",
    ]
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            last_err = ""
            for qurl in query_urls:
                resp = await client.get(qurl, headers={"Authorization": f"Bearer {api_key}"})
                body = resp.json() if resp.content else {}
                if resp.status_code == 404:
                    continue
                if resp.status_code >= 400:
                    last_err = _error_message(body, resp.text or "华狐查询失败")
                    continue
                st = _normalize_status(_status_of(body))
                video = _extract_video_url(body)
                if st == "SUCCEEDED":
                    note_upstream_billing_from_response(body, provider="huahu", event="poll_succeeded")
                    if not video:
                        raise UpstreamError("任务成功但未返回视频 URL", code="EMPTY_OUTPUT")
                    return video
                if st == "FAILED":
                    raise UpstreamError(
                        _humanize_seedance_error(_error_message(body, "华狐视频任务失败"), provider="huahu"),
                        code="TASK_FAILED",
                    )
                # 处理中 / 未知：有 URL 也不提前返回，等终态
                break
            else:
                if last_err:
                    raise UpstreamError(
                        _humanize_seedance_error(last_err, provider="huahu"),
                        code="POLL_ERROR",
                    )
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("华狐 Seedance 视频生成超时", code="TIMEOUT")


async def huahu_seedance_video_generate(*, body: dict[str, Any]) -> str:
    """华狐 Seedance：V1 提交并轮询，provider 标记为 huahu。"""
    api_key, api_base = _huahu_resolved_base()
    payload = ark_seedance_body_to_v1_payload(body)
    create_url = f"{api_base}/video/generations"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        logger.info(
            "[upstream] POST %s model=%s kind=video provider=huahu",
            create_url,
            payload.get("model"),
        )
        resp = await client.post(create_url, headers=headers, json=payload)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raw_err = _error_message(data, resp.text or "华狐 Seedance 提交失败")
            raise UpstreamError(
                _humanize_seedance_error(raw_err, provider="huahu"),
                code="SUBMIT_ERROR",
            )

    # 少数网关同步直接回视频 URL（无 task_id）
    immediate = _extract_video_url(data)
    task_id = _extract_task_id(data)
    st = _normalize_status(_status_of(data))
    if immediate and not task_id and st != "FAILED":
        note_upstream_billing_from_response(data, provider="huahu", event="submit_sync_succeeded")
        return immediate
    if not task_id:
        raise UpstreamError("未返回任务 ID", code="MISSING_TASK_ID")
    note_upstream(provider="huahu", provider_task_id=task_id, event="submit")
    await maybe_flush_upstream_trace()
    return await poll_huahu_seedance_task(task_id)


__all__ = [
    "ark_seedance_body_to_v1_payload",
    "build_seedance_i2v_body",
    "build_seedance_multimodal_body",
    "build_seedance_r2v_body",
    "build_seedance_t2v_body",
    "huahu_seedance_video_generate",
    "normalize_huahu_api_base",
    "poll_huahu_seedance_task",
]
