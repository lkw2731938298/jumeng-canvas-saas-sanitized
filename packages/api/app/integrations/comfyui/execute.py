"""把画布单节点生成提交到用户自己的 ComfyUI（/prompt 轮询 /view）。"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from ..upstream.errors import UpstreamError
from ..upstream.trace_context import maybe_flush_upstream_trace, note_upstream
from .client import ComfyUIClient
from .generic_prompt import apply_user_workflow, default_sd_txt2img_prompt

logger = logging.getLogger(__name__)


def resolve_comfy_base_url(spec_extra: dict[str, Any] | None, fallback: str = "") -> str:
    extra = spec_extra or {}
    for key in ("comfyBaseUrl", "comfyui_base_url", "comfy_base_url"):
        val = str(extra.get(key) or "").strip()
        if val:
            return val.rstrip("/")
    return (fallback or "").strip().rstrip("/")


async def _upload_first_image(client: ComfyUIClient, image_url: str) -> str:
    """把参考图拉下来再传到该 ComfyUI 的 /upload/image，返回 Comfy 侧文件名。"""
    src = (image_url or "").strip()
    if not src:
        return ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as http:
        resp = await http.get(src)
        resp.raise_for_status()
        data = resp.content
    parsed = urlparse(src)
    filename = (parsed.path.rsplit("/", 1)[-1] or "ref.png").split("?")[0] or "ref.png"
    if "." not in filename:
        filename += ".png"
    return await client.upload_image(data, filename)


def _collect_media(entry: dict[str, Any]) -> list[dict[str, str]]:
    outputs = entry.get("outputs") or {}
    media: list[dict[str, str]] = []
    if not isinstance(outputs, dict):
        return media
    for _nid, node_out in outputs.items():
        if not isinstance(node_out, dict):
            continue
        for key in ("images", "gifs", "videos"):
            for item in node_out.get(key) or []:
                if not isinstance(item, dict):
                    continue
                fn = str(item.get("filename") or "").strip()
                if not fn:
                    continue
                media.append(
                    {
                        "filename": fn,
                        "subfolder": str(item.get("subfolder") or ""),
                        "type": str(item.get("type") or "output"),
                    }
                )
    return media


async def run_comfyui_generation(
    *,
    base_url: str,
    model_filename: str,
    prompt: str,
    negative: str = "",
    category: str = "image",
    workflow: dict[str, Any] | str | None = None,
    reference_image_url: str | None = None,
    width: int = 1024,
    height: int = 1024,
    max_wait_s: float = 1800.0,
) -> tuple[bytes, str, str]:
    """执行一次生成，返回 (bytes, content_type, ext)。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise UpstreamError("未配置 ComfyUI 地址", code="NOT_CONFIGURED")
    ckpt = (model_filename or "").strip()
    client = ComfyUIClient(base_url=url)
    try:
        image_name = ""
        if reference_image_url:
            try:
                image_name = await _upload_first_image(client, reference_image_url)
            except Exception as exc:
                raise UpstreamError(f"参考图上传 ComfyUI 失败: {exc}", code="INVALID_MEDIA") from exc

        if workflow:
            try:
                prompt_graph = apply_user_workflow(
                    workflow,
                    prompt=prompt,
                    negative=negative,
                    model_filename=ckpt,
                    image_name=image_name,
                    width=width,
                    height=height,
                )
            except (ValueError, json.JSONDecodeError) as exc:
                raise UpstreamError(f"工作流 JSON 无效: {exc}", code="INVALID_MODEL") from exc
        else:
            if (category or "image").lower() == "video":
                raise UpstreamError(
                    "该视频模型未绑定 ComfyUI API 工作流。请在模型配置中粘贴「另存为 API 格式」的 JSON，"
                    "并用 {{PROMPT}} / {{MODEL}} / {{IMAGE}} 占位。",
                    code="NOT_CONFIGURED",
                )
            if not ckpt:
                raise UpstreamError("缺少 ComfyUI 权重文件名", code="INVALID_MODEL")
            prompt_graph = default_sd_txt2img_prompt(
                ckpt_name=ckpt,
                prompt=prompt,
                negative=negative,
                width=width,
                height=height,
            )

        note_upstream(provider="comfyui", event="submit", detail={"kind": category, "model": ckpt})
        queued = await client.queue_prompt(prompt_graph)
        prompt_id = str(queued.get("prompt_id") or "").strip()
        if not prompt_id:
            err = queued.get("node_errors") or queued
            raise UpstreamError(f"ComfyUI 未返回 prompt_id: {err}", code="SUBMIT_ERROR")
        note_upstream(provider="comfyui", provider_task_id=prompt_id, event="submit")
        await maybe_flush_upstream_trace()

        history = await client.submit_and_wait_existing(prompt_id, max_wait_s=max_wait_s)
        if not history:
            raise UpstreamError("ComfyUI 任务超时未完成", code="POLL_TIMEOUT")
        media = _collect_media(history)
        if not media:
            raise UpstreamError("ComfyUI 未返回图片或视频", code="EMPTY_OUTPUT")
        item = media[0]
        raw = await client.view_image(item["filename"], item["subfolder"], item["type"])
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise UpstreamError("下载 ComfyUI 产物失败", code="EMPTY_OUTPUT")
        fn = item["filename"].lower()
        if fn.endswith(".mp4") or fn.endswith(".webm"):
            return bytes(raw), "video/mp4", "mp4"
        if fn.endswith(".gif") or fn.endswith(".webp"):
            ctype = "image/gif" if fn.endswith(".gif") else "image/webp"
            return bytes(raw), ctype, fn.rsplit(".", 1)[-1]
        return bytes(raw), "image/png", "png"
    finally:
        await client.close()
