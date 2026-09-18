"""本地 OpenAI 兼容上游：Ollama / vLLM / SGLang / 自建网关。

文本走 chat.completions（chat.py）；图片 ``POST {base}/images/generations``；
视频 ``POST {base}/videos/generations``（MiniMax H3 SGLang / 兼容网关）。
密钥可填占位 ``local``；权威配置在管理后台「供应商密钥」的 ``local`` 条目。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import httpx

from ...core.llm_keys import get_llm_keys
from .errors import UpstreamError
from .trace_context import maybe_flush_upstream_trace, note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_API_BASE = "http://127.0.0.1:8000/v1"


def _creds(*, api_base_override: str | None = None) -> tuple[str, str]:
    cfg = get_llm_keys()
    key = (cfg.local.api_key or "").strip() or "local"
    base = (api_base_override or cfg.local.api_base or DEFAULT_LOCAL_API_BASE).rstrip("/")
    if not base:
        raise UpstreamError("未配置本地模型 API 地址（LOCAL_API_BASE）", code="NOT_CONFIGURED")
    return key, base


def _error_message(body: dict[str, Any], fallback: str) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "").strip()
        if msg:
            return msg[:500]
    return str(body.get("message") or body.get("msg") or fallback)[:500]


def _extract_urls_from_image_body(body: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    data = body.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                u = str(item.get("url") or "").strip()
                if u:
                    urls.append(u)
            elif isinstance(item, str) and item.startswith("http"):
                urls.append(item)
    return urls


def _extract_video_url(body: dict[str, Any]) -> str:
    for key in ("url", "video_url", "videoUrl", "output_url", "result_url"):
        val = str(body.get(key) or "").strip()
        if val.startswith("http"):
            return val
    data = body.get("data")
    if isinstance(data, dict):
        for key in ("url", "video_url", "videoUrl", "output"):
            val = str(data.get(key) or "").strip()
            if val.startswith("http"):
                return val
        outputs = data.get("outputs") or data.get("results")
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                val = str(first.get("url") or first.get("video_url") or "").strip()
                if val.startswith("http"):
                    return val
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, str) and first.startswith("http"):
            return first
        if isinstance(first, dict):
            val = str(first.get("url") or first.get("video_url") or "").strip()
            if val.startswith("http"):
                return val
    return ""


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
    return ""


async def local_image_generate(
    *,
    model: str,
    prompt: str,
    reference_urls: list[str] | None = None,
    size: str = "1024x1024",
    n: int = 1,
    api_base_override: str | None = None,
) -> list[str]:
    """调用本地 OpenAI 兼容文生图，返回产物 URL。"""
    api_key, api_base = _creds(api_base_override=api_base_override)
    url = f"{api_base}/images/generations"
    payload: dict[str, Any] = {
        "model": (model or "").strip(),
        "prompt": (prompt or "").strip()[:20000],
        "n": max(1, min(int(n or 1), 4)),
        "response_format": "url",
    }
    if size:
        payload["size"] = size
    refs = [u for u in (reference_urls or []) if u and str(u).strip()]
    if refs:
        payload["image"] = refs[0] if len(refs) == 1 else refs

    note_upstream(provider="local", event="submit", detail={"kind": "image", "model": model})
    logger.info("[upstream] POST %s kind=local-image model=%s", url, model)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=20.0)) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise UpstreamError(_error_message(body, resp.text or "本地图片生成失败"), code="SUBMIT_ERROR")
        note_upstream_billing_from_response(body, provider="local", event="image_succeeded")
        urls = _extract_urls_from_image_body(body)
        if urls:
            return urls
        raise UpstreamError("本地推理未返回图片 URL", code="EMPTY_OUTPUT")


async def _poll_local_video_task(
    task_id: str,
    *,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 1800.0,
    api_base_override: str | None = None,
) -> str:
    """轮询本地异步视频任务。"""
    api_key, api_base = _creds(api_base_override=api_base_override)
    tid = quote(str(task_id).strip(), safe="")
    query_paths = [
        f"{api_base}/videos/{tid}",
        f"{api_base}/videos/generations/{tid}",
    ]
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            for qurl in query_paths:
                resp = await client.get(
                    qurl,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code >= 400:
                    continue
                body = resp.json() if resp.content else {}
                status = _status_of(body)
                if status in ("failed", "error", "cancelled"):
                    raise UpstreamError(_error_message(body, "本地视频任务失败"), code="TASK_FAILED")
                out = _extract_video_url(body)
                if out:
                    return out
                if status in ("succeeded", "success", "completed", "done"):
                    raise UpstreamError("本地视频任务完成但未返回 URL", code="EMPTY_OUTPUT")
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("本地视频任务轮询超时", code="POLL_TIMEOUT")


async def local_video_generate(
    *,
    model: str,
    prompt: str,
    duration_sec: int = 5,
    resolution: str = "768p",
    ratio: str = "16:9",
    generate_audio: bool = True,
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
    api_base_override: str | None = None,
) -> str:
    """调用本地 OpenAI 兼容视频生成，返回产物 URL。"""
    api_key, api_base = _creds(api_base_override=api_base_override)
    url = f"{api_base}/videos/generations"
    dur = int(max(4, min(15, int(duration_sec or 5))))
    payload: dict[str, Any] = {
        "model": (model or "").strip(),
        "prompt": (prompt or "").strip()[:20000],
        "duration": dur,
        "seconds": str(dur),
        "resolution": (resolution or "768p").strip() or "768p",
        "aspect_ratio": (ratio or "16:9").strip() or "16:9",
        "generate_audio": bool(generate_audio),
    }
    imgs = [u for u in (image_urls or []) if u and str(u).strip()][:9]
    vids = [u for u in (video_urls or []) if u and str(u).strip()][:3]
    auds = [u for u in (audio_urls or []) if u and str(u).strip()][:3]
    if imgs:
        payload["images"] = imgs
        payload["image"] = imgs[0]
        payload["first_frame"] = imgs[0]
        if len(imgs) > 1:
            payload["last_frame"] = imgs[1]
    if vids:
        payload["videos"] = vids
    if auds:
        payload["audios"] = auds

    note_upstream(provider="local", event="submit", detail={"kind": "video", "model": model})
    logger.info("[upstream] POST %s kind=local-video model=%s", url, model)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise UpstreamError(_error_message(body, resp.text or "本地视频生成失败"), code="SUBMIT_ERROR")

    out = _extract_video_url(body)
    if out:
        note_upstream_billing_from_response(body, provider="local", event="video_succeeded")
        return out
    task_id = _extract_task_id(body)
    if not task_id:
        raise UpstreamError(_error_message(body, "本地推理未返回视频 URL 或 task_id"), code="MISSING_TASK_ID")
    note_upstream(provider="local", provider_task_id=task_id, event="submit")
    await maybe_flush_upstream_trace()
    return await _poll_local_video_task(task_id, api_base_override=api_base_override)
