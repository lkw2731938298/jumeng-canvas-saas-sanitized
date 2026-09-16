"""管理员同步上游任务状态 —— 单次查询各供应商任务进度（不 submit）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ..integrations.upstream.credentials import (
    dashscope_creds,
    runninghub_creds,
    vidu_creds,
)
from ..integrations.upstream.dashscope import _parse_dashscope_video_task
from ..integrations.upstream.errors import UpstreamError
from ..integrations.upstream.jumengai import inspect_jumengai_video_task
from ..integrations.upstream.runninghub import _extract_result_url, _headers as rh_headers
from ..integrations.upstream.vidu import _parse_vidu_state


@dataclass
class UpstreamStatusResult:
    """上游任务状态查询结果：状态、产物 URL、错误信息、原始响应。"""

    state: str  # pending | running | succeeded | failed | unknown
    result_url: str | None = None
    error_message: str | None = None
    raw: dict[str, Any] | None = None


async def query_runninghub_task(task_id: str, *, use_ltx: bool = False) -> UpstreamStatusResult:
    """查询 RunningHub 任务当前状态（管理员 sync_upstream 用）。

    use_ltx=True 走海外版密钥/地址（ltx_runninghub）。
    """
    creds = runninghub_creds(use_ltx=use_ltx)
    if not creds.api_key:
        raise UpstreamError("未配置 RUNNINGHUB_API_KEY", code="NOT_CONFIGURED")
    query_url = f"{creds.api_base}/openapi/v2/query"
    payload = {"taskId": task_id}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        resp = await client.post(
            query_url,
            headers=rh_headers(creds.api_key),
            content=json.dumps(payload),
        )
        body = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            return UpstreamStatusResult(
                state="unknown",
                error_message=str(body.get("message") or resp.text or "查询失败")[:500],
                raw=body if isinstance(body, dict) else None,
            )
        status = str(body.get("status") or "").upper()
        if status == "SUCCESS":
            url = _extract_result_url(body)
            if url and not url.startswith("http"):
                url = f"{creds.api_base}/{url.lstrip('/')}"
            return UpstreamStatusResult(state="succeeded", result_url=url or None, raw=body)
        if status == "FAILED":
            return UpstreamStatusResult(
                state="failed",
                error_message=str(body.get("message") or "上游任务失败")[:500],
                raw=body,
            )
        if status in ("RUNNING", "PROCESSING", "QUEUED", "PENDING"):
            mapped = "running" if status in ("RUNNING", "PROCESSING") else "pending"
            return UpstreamStatusResult(state=mapped, raw=body)
        return UpstreamStatusResult(
            state="unknown",
            error_message=f"无法解析上游状态: {status or 'empty'}",
            raw=body,
        )


async def query_dashscope_task(task_id: str) -> UpstreamStatusResult:
    """查询百炼/万相任务当前状态。"""
    creds = dashscope_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 DASHSCOPE_API_KEY", code="NOT_CONFIGURED")
    url = f"{creds.api_base}/tasks/{task_id}"
    headers = {"Authorization": f"Bearer {creds.api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        resp = await client.get(url, headers=headers)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("code"):
            return UpstreamStatusResult(
                state="unknown",
                error_message=str(data.get("message") or "查询失败")[:500],
                raw=data if isinstance(data, dict) else None,
            )
        st, video, err = _parse_dashscope_video_task(data)
        if st == "SUCCEEDED":
            out = data.get("output") if isinstance(data.get("output"), dict) else {}
            img = None
            for choice in out.get("choices") or []:
                if not isinstance(choice, dict):
                    continue
                msg = choice.get("message") or {}
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "image" and block.get("image"):
                        img = str(block["image"]).strip()
                        break
            result_url = video or img
            return UpstreamStatusResult(state="succeeded", result_url=result_url, raw=data)
        if st == "FAILED":
            return UpstreamStatusResult(state="failed", error_message=err or "上游任务失败", raw=data)
        if st == "RUNNING":
            return UpstreamStatusResult(state="running", raw=data)
        if st == "PENDING":
            return UpstreamStatusResult(state="pending", raw=data)
        return UpstreamStatusResult(
            state="unknown",
            error_message=f"无法解析上游状态: {st}",
            raw=data,
        )


async def query_vidu_task(task_id: str) -> UpstreamStatusResult:
    """查询 Vidu 任务当前状态。"""
    creds = vidu_creds()
    if not creds.api_key:
        raise UpstreamError("未配置 VIDU_API_KEY", code="NOT_CONFIGURED")
    url = f"{creds.api_base.rstrip('/')}/ent/v2/tasks/{task_id}/creations"
    headers = {"Authorization": f"Token {creds.api_key}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0)) as client:
        resp = await client.get(url, headers=headers)
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            return UpstreamStatusResult(
                state="unknown",
                error_message=str(data.get("message") or resp.text or "查询失败")[:500],
                raw=data if isinstance(data, dict) else None,
            )
        state, video, err = _parse_vidu_state(data)
        if state == "success":
            return UpstreamStatusResult(state="succeeded", result_url=video, raw=data)
        if state in ("failed", "error"):
            return UpstreamStatusResult(state="failed", error_message=err or "上游任务失败", raw=data)
        if state in ("processing", "running", "queueing", "pending"):
            mapped = "running" if state in ("processing", "running") else "pending"
            return UpstreamStatusResult(state=mapped, raw=data)
        return UpstreamStatusResult(
            state="unknown",
            error_message=f"无法解析上游状态: {state}",
            raw=data,
        )


async def query_jumengai_task(task_id: str) -> UpstreamStatusResult:
    """查询聚梦网关视频任务（管理员 sync_upstream），禁止二次 POST。"""
    info = await inspect_jumengai_video_task(task_id)
    state = str(info.get("state") or "unknown")
    url = str(info.get("url") or "").strip() or None
    err = str(info.get("error") or "").strip() or None
    raw = info.get("raw") if isinstance(info.get("raw"), dict) else None
    if state == "succeeded":
        return UpstreamStatusResult(state="succeeded", result_url=url, raw=raw)
    if state == "failed":
        return UpstreamStatusResult(state="failed", error_message=err or "上游任务失败", raw=raw)
    if state in ("pending", "running"):
        return UpstreamStatusResult(state=state, raw=raw)
    return UpstreamStatusResult(
        state="unknown",
        error_message=err or "无法解析聚梦网关任务状态",
        raw=raw,
    )


async def query_upstream_task_status(
    *,
    provider: str | None,
    task_id: str,
) -> UpstreamStatusResult:
    """按供应商标识分发到对应查询函数。"""
    provider_key = (provider or "").strip().lower()
    if provider_key in ("runninghub", "rh"):
        return await query_runninghub_task(task_id)
    if provider_key == "ltx_runninghub":
        # RunningHub 海外版任务：用海外密钥/地址查询
        return await query_runninghub_task(task_id, use_ltx=True)
    if provider_key in ("dashscope", "aliyun", "wan", "kling"):
        return await query_dashscope_task(task_id)
    if provider_key == "vidu":
        return await query_vidu_task(task_id)
    if provider_key == "jumengai":
        return await query_jumengai_task(task_id)
    return UpstreamStatusResult(
        state="unknown",
        error_message=f"暂不支持同步供应商: {provider or 'unknown'}",
    )
