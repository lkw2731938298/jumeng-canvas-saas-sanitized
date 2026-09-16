"""Vidu 企业版上游集成：参考图生视频、首尾帧生视频与对口型。"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from .credentials import vidu_creds
from .errors import UpstreamError
from .trace_context import maybe_flush_upstream_trace, note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)

_TASK_ID = re.compile(r"^[a-zA-Z0-9_-]{4,512}$")
_VIDU_REF_IMAGE_LIMIT = 7


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}


def _resolution_api(resolution: str) -> str:
    r = str(resolution or "720").strip().replace("p", "").replace("P", "")
    if r == "1080":
        return "1080p"
    if r == "540":
        return "540p"
    return "720p"


def _clamp_duration(model: str, duration: int) -> int:
    m = (model or "").lower()
    if "q3" in m:
        return max(3, min(16, int(duration)))
    if "q2" in m:
        if int(duration) in (5, 10):
            return int(duration)
        return 5
    return max(3, min(16, int(duration)))


def _normalize_image_urls(urls: list[str], *, limit: int = _VIDU_REF_IMAGE_LIMIT) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        u = str(raw or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def _task_id_from_submit(data: dict[str, Any]) -> str:
    for key in ("task_id", "taskId", "id"):
        val = str(data.get(key) or "").strip()
        if val:
            return val
    return ""


def _parse_vidu_state(data: dict[str, Any]) -> tuple[str, str | None, str | None]:
    state = str(data.get("state") or data.get("status") or "").strip().lower()
    if state in {"success", "succeed", "succeeded", "completed", "done"}:
        state = "success"
    video = None
    for row in data.get("creations") or []:
        if not isinstance(row, dict):
            continue
        for k in ("url", "video_url", "download_url"):
            u = str(row.get(k) or "").strip()
            if u.startswith("http"):
                video = u
                break
        if video:
            break
    err = str(data.get("message") or data.get("err_msg") or "").strip() or None
    return state, video, err


async def poll_vidu_task(
    task_id: str,
    *,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 1200.0,
) -> str:
    """仅按 task_id 轮询 Vidu 任务直至完成，返回视频 URL；不含任何 submit。"""
    creds = vidu_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 VIDU_API_KEY", code="NOT_CONFIGURED")
    return await _poll_vidu(
        task_id,
        api_key=creds.api_key,
        api_base=creds.api_base,
        poll_interval_s=poll_interval_s,
        poll_max_s=poll_max_s,
    )


async def _poll_vidu(
    task_id: str,
    *,
    api_key: str,
    api_base: str,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 1200.0,
) -> str:
    if not _TASK_ID.match(task_id):
        raise UpstreamError("非法 task_id", code="INVALID_TASK_ID")
    url = f"{api_base}/ent/v2/tasks/{task_id}/creations"
    headers = _headers(api_key)
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            resp = await client.get(url, headers=headers)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                raise UpstreamError(str(data.get("message") or resp.text or "Vidu 查询失败")[:500], code="POLL_ERROR")
            state, video, err = _parse_vidu_state(data)
            if state == "success":
                note_upstream_billing_from_response(data, provider="vidu", event="poll_succeeded")
                if not video:
                    raise UpstreamError("任务成功但未返回视频 URL", code="EMPTY_OUTPUT")
                return video
            if state in {"failed", "error"}:
                raise UpstreamError(err or "Vidu 视频生成失败", code="TASK_FAILED")
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("Vidu 视频生成超时", code="TIMEOUT")


async def _submit(path: str, body: dict[str, Any]) -> str:
    creds = vidu_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 VIDU_API_KEY", code="NOT_CONFIGURED")
    url = f"{creds.api_base}{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        logger.info("[upstream] POST %s kind=vidu path=%s model=%s", url, path, body.get("model"))
        resp = await client.post(url, headers=_headers(creds.api_key), json=body)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise UpstreamError(str(data.get("message") or resp.text or "Vidu 提交失败")[:500], code="SUBMIT_ERROR")
        task_id = _task_id_from_submit(data)
        if not task_id:
            raise UpstreamError("未返回 task_id", code="MISSING_TASK_ID")
        # 提交成功后立即落库 task_id，保证崩溃/重试只续轮询，不二次提交
        note_upstream(provider="vidu", provider_task_id=task_id, event="submit")
        await maybe_flush_upstream_trace()
        # 全新任务：提交后接着轮询；重试任务由上层直接调用 poll_vidu_task
        return await _poll_vidu(task_id, api_key=creds.api_key, api_base=creds.api_base)


async def vidu_reference_to_video(
    *,
    model: str,
    prompt: str,
    image_urls: list[str],
    duration: int = 5,
    resolution: str = "720",
    aspect_ratio: str = "16:9",
    watermark: bool = False,
    off_peak: bool = False,
    audio: bool = False,
) -> str:
    """POST /ent/v2/reference2video — images 必须为 URL 字符串数组（非 {url:...} 对象）。"""
    images = _normalize_image_urls(image_urls)
    if not images:
        raise UpstreamError("参考生视频需要至少一张可访问的参考图 URL", code="INVALID_MEDIA")

    body: dict[str, Any] = {
        "model": model,
        "images": images,
        "prompt": (prompt or "").strip(),
        "duration": _clamp_duration(model, duration),
        "resolution": _resolution_api(resolution),
        "aspect_ratio": (aspect_ratio or "16:9").strip(),
        "off_peak": bool(off_peak),
        "watermark": bool(watermark),
        "bgm": False,
        "audio": bool(audio),
    }
    if watermark:
        body["wm_position"] = 3
    if audio and "q3" in (model or "").lower():
        body["audio_type"] = "all"

    return await _submit("/ent/v2/reference2video", body)


async def vidu_start_end_to_video(
    *,
    model: str,
    prompt: str,
    first_frame_url: str,
    last_frame_url: str | None = None,
    duration: int = 5,
    resolution: str = "720",
    watermark: bool = False,
    off_peak: bool = False,
    audio: bool = False,
) -> str:
    """POST /ent/v2/start-end2video — 同样使用 images 字符串数组。"""
    images = _normalize_image_urls([first_frame_url, last_frame_url or ""])
    if not images:
        raise UpstreamError("首尾帧生视频需要首帧图片 URL", code="INVALID_MEDIA")

    body: dict[str, Any] = {
        "model": model,
        "images": images,
        "duration": _clamp_duration(model, duration),
        "seed": 0,
        "resolution": _resolution_api(resolution),
        "movement_amplitude": "auto",
        "is_rec": False,
        "off_peak": bool(off_peak),
        "watermark": bool(watermark),
        "payload": "",
    }
    if (prompt or "").strip():
        body["prompt"] = prompt.strip()
    if watermark:
        body["wm_position"] = 3
    if audio:
        body["audio"] = True

    return await _submit("/ent/v2/start-end2video", body)


async def vidu_lip_sync(*, video_url: str, audio_url: str) -> str:
    """提交 Vidu 对口型任务并轮询直至完成，返回合成视频 URL。"""
    body = {"video_url": video_url, "audio_url": audio_url}
    return await _submit("/ent/v2/lip-sync", body)
