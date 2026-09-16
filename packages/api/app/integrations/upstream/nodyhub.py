"""NodyHub 上游：图片（OpenAI 兼容 /v1/images/generations）+ 视频（/v1/videos/generations）。

默认 Base：https://nodyhub.com/v1（可在管理端供应商凭证中覆盖 api_base）。
密钥仅经 llm-keys / DB 加密凭证读取，禁止写死在代码中。

视频：SD2.0 全模态 / Fast / Mini（upstream：sd-2.0 / sd-2.0-fast / sd-2.0-mini），
走 NewAPI 风格 videos/generations；支持同步 URL 或异步 task 轮询。
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

DEFAULT_NODYHUB_API_BASE = "https://nodyhub.com/v1"


def _creds() -> tuple[str, str]:
    cfg = get_llm_keys()
    key = (cfg.nodyhub.api_key or "").strip()
    base = (cfg.nodyhub.api_base or DEFAULT_NODYHUB_API_BASE).rstrip("/")
    if not key:
        raise UpstreamError("未配置 NODYHUB_API_KEY", code="NOT_CONFIGURED")
    return key, base


def _error_message(body: dict[str, Any], fallback: str) -> str:
    err = body.get("error")
    if isinstance(err, dict):
        msg = str(err.get("message") or "").strip()
        if msg:
            return msg[:500]
    return str(body.get("message") or body.get("msg") or fallback)[:500]


async def nodyhub_image_generate(
    *,
    model: str,
    prompt: str,
    reference_urls: list[str] | None = None,
    size: str = "1024x1024",
    quality: str | None = None,
    n: int = 1,
) -> list[str]:
    """调用 NodyHub 图片生成，返回产物 URL 列表（兼容 url / b64 由上层下载处理）。"""
    api_key, api_base = _creds()
    url = f"{api_base}/images/generations"
    count = max(1, min(int(n or 1), 4))
    payload: dict[str, Any] = {
        "model": (model or "").strip(),
        "prompt": (prompt or "").strip()[:20000],
        "n": count,
        "response_format": "url",
    }
    if size:
        payload["size"] = size
    if quality:
        payload["quality"] = quality
    refs = [u for u in (reference_urls or []) if u and str(u).strip()]
    if refs:
        # 兼容部分网关用 image / images 传参考图
        payload["image"] = refs[0] if len(refs) == 1 else refs

    note_upstream(provider="nodyhub", event="submit", detail={"kind": "image", "model": model})
    logger.info("[upstream] POST %s kind=nodyhub model=%s", url, model)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=20.0)) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise UpstreamError(
                _error_message(body, resp.text or "NodyHub 提交失败"),
                code="SUBMIT_ERROR",
            )
        note_upstream_billing_from_response(body, provider="nodyhub", event="image_succeeded")
        data = body.get("data")
        urls: list[str] = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                out_url = str(item.get("url") or "").strip()
                if out_url:
                    urls.append(out_url)
        if urls:
            return urls
        # 部分网关异步：返回 task_id / id
        task_id = str(body.get("id") or body.get("task_id") or body.get("taskId") or "").strip()
        if task_id:
            note_upstream(provider="nodyhub", provider_task_id=task_id, event="submit")
            raise UpstreamError(
                "NodyHub 返回异步任务但本集成暂未实现轮询，请改用同步 URL 模式或联系管理员",
                code="ASYNC_NOT_SUPPORTED",
            )
        raise UpstreamError("NodyHub 未返回图片 URL", code="EMPTY_OUTPUT")


def _extract_video_url(body: dict[str, Any]) -> str:
    """从同步/轮询响应中提取视频 URL。"""
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
        outputs = data.get("outputs") or data.get("results") or data.get("video")
        if isinstance(outputs, list) and outputs:
            first = outputs[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                for key in ("url", "video_url", "videoUrl"):
                    val = str(first.get(key) or "").strip()
                    if val.startswith("http"):
                        return val
        elif isinstance(outputs, str) and outputs.startswith("http"):
            return outputs
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict):
            for key in ("url", "video_url", "videoUrl"):
                val = str(first.get(key) or "").strip()
                if val.startswith("http"):
                    return val
        if isinstance(first, str) and first.startswith("http"):
            return first
    results = body.get("results")
    if isinstance(results, list) and results and isinstance(results[0], dict):
        val = str(results[0].get("url") or "").strip()
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


async def poll_nodyhub_video_task(
    task_id: str,
    *,
    poll_interval_s: float = 5.0,
    poll_max_s: float = 900.0,
) -> str:
    """按 task_id 轮询 NodyHub 视频任务直至完成，返回视频 URL。"""
    api_key, api_base = _creds()
    tid = quote(str(task_id).strip(), safe="")
    # NewAPI / 网关常见查询路径
    query_paths = [
        f"{api_base}/videos/{tid}",
        f"{api_base}/videos/generations/{tid}",
        f"{api_base}/video/generations/{tid}",
    ]
    deadline = asyncio.get_running_loop().time() + poll_max_s
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        while asyncio.get_running_loop().time() < deadline:
            last_err = ""
            for qurl in query_paths:
                resp = await client.get(
                    qurl,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                body = resp.json() if resp.content else {}
                if resp.status_code == 404:
                    continue
                if resp.status_code >= 400:
                    last_err = _error_message(body, resp.text or "NodyHub 查询失败")
                    continue
                status = _status_of(body)
                url = _extract_video_url(body)
                if url and status in ("", "succeeded", "success", "completed", "done", "finished"):
                    note_upstream_billing_from_response(body, provider="nodyhub", event="poll_succeeded")
                    return url
                if status in ("failed", "error", "cancelled", "canceled"):
                    raise UpstreamError(
                        _error_message(body, "NodyHub 视频任务失败"),
                        code="TASK_FAILED",
                    )
                if url and status in ("processing", "running", "queued", "pending", "submitted", "in_progress"):
                    # 部分网关未完成也回传占位 URL，继续等终态
                    break
                break
            else:
                if last_err:
                    raise UpstreamError(last_err[:500], code="POLL_ERROR")
            await asyncio.sleep(poll_interval_s)
    raise UpstreamError("NodyHub 视频任务超时", code="TIMEOUT")


async def nodyhub_video_generate(
    *,
    model: str,
    prompt: str,
    duration_sec: int = 5,
    resolution: str = "720p",
    ratio: str = "16:9",
    generate_audio: bool = True,
    image_urls: list[str] | None = None,
    video_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
) -> str:
    """提交 NodyHub SD2.0 视频任务并返回产物 URL（同步或轮询）。

    中文说明：入参对齐画布多模态；上游字段兼容 NewAPI / OpenAI-style videos/generations。
    """
    api_key, api_base = _creds()
    url = f"{api_base}/videos/generations"
    dur = int(max(4, min(15, int(duration_sec or 5))))
    payload: dict[str, Any] = {
        "model": (model or "").strip(),
        "prompt": (prompt or "").strip()[:20000],
        "duration": dur,
        "seconds": str(dur),
        "resolution": (resolution or "720p").strip() or "720p",
        "aspect_ratio": (ratio or "16:9").strip() or "16:9",
        "ratio": (ratio or "16:9").strip() or "16:9",
        "generate_audio": bool(generate_audio),
    }
    imgs = [u for u in (image_urls or []) if u and str(u).strip()][:9]
    vids = [u for u in (video_urls or []) if u and str(u).strip()][:3]
    auds = [u for u in (audio_urls or []) if u and str(u).strip()][:3]
    if imgs:
        payload["images"] = imgs
        payload["image"] = imgs[0]
        payload["image_urls"] = imgs
    if vids:
        payload["videos"] = vids
        payload["video"] = vids[0]
        payload["video_urls"] = vids
    if auds:
        payload["audios"] = auds
        payload["audio"] = auds[0]
        payload["audio_urls"] = auds

    note_upstream(provider="nodyhub", event="submit", detail={"kind": "video", "model": model})
    logger.info("[upstream] POST %s kind=nodyhub-video model=%s", url, model)
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=30.0)) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise UpstreamError(
                _error_message(body, resp.text or "NodyHub 视频提交失败"),
                code="SUBMIT_ERROR",
            )

    out = _extract_video_url(body)
    if out:
        note_upstream_billing_from_response(body, provider="nodyhub", event="video_succeeded")
        return out

    task_id = _extract_task_id(body)
    if not task_id:
        raise UpstreamError(
            _error_message(body, "NodyHub 未返回视频 URL 或 task_id"),
            code="MISSING_TASK_ID",
        )
    note_upstream(provider="nodyhub", provider_task_id=task_id, event="submit")
    await maybe_flush_upstream_trace()
    return await poll_nodyhub_video_task(task_id)
