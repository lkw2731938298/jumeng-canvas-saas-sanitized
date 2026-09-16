"""火山方舟 / 即梦 Seedream 图片生成集成。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...core.llm_keys import get_llm_keys, get_model_credentials
from ...core.model_registry import ARK_IMAGE_MODEL_IDS, get_model_spec
from ...services.reference_image import resolve_references_for_ark
from ..upstream.errors import UpstreamError
from ..upstream.trace_context import note_upstream, note_upstream_billing_from_response

logger = logging.getLogger(__name__)

LIVE_ARK_IMAGE_MODELS = frozenset(ARK_IMAGE_MODEL_IDS)


def _resolve_remote_model(model_id: str) -> str:
    if model_id not in LIVE_ARK_IMAGE_MODELS:
        raise ValueError(f"Image generation not implemented for model: {model_id}")

    keys = get_llm_keys()
    # 仅旧默认 doubao_image 可用全局 endpoint；Lite/Pro 必须用各自 upstreamModel
    if model_id == "doubao_image" and keys.doubao_image_endpoint_id.strip():
        return keys.doubao_image_endpoint_id.strip()

    spec = get_model_spec(model_id)
    if spec and spec.upstream_model:
        return spec.upstream_model

    if model_id == "doubao_image":
        return keys.doubao_image_model
    raise ValueError(f"missing upstreamModel for image model: {model_id}")


def _normalize_ark_image_size(size: str, *, remote_model: str) -> str:
    """Seedream 5.x 仅接受 2k/3k/4k 或 WIDTHxHEIGHT；旧 1K/2K 会 InvalidParameter。"""
    raw = (size or "2k").strip()
    if "seedream-5" not in remote_model.lower():
        return raw
    lower = raw.lower().replace(" ", "")
    if lower in {"1k", "1"}:
        # 5.0 无 1k 档，升到官方最低 2k
        return "2k"
    if lower in {"2k", "3k", "4k"}:
        return lower
    if "x" in lower and all(p.isdigit() for p in lower.split("x", 1)):
        return lower
    if lower.endswith("k") and lower[:-1].isdigit():
        return lower
    return "2k"


def _ark_http_error_detail(resp: httpx.Response) -> str:
    """从方舟错误 JSON 提取可读文案，避免仅显示「上游 HTTP 404」。"""
    text = (resp.text or "").strip()
    detail = ""
    code = ""
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else body
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("msg") or "").strip()
            code = str(err.get("code") or "").strip()
    if not detail:
        detail = text[:400] or f"上游 HTTP {resp.status_code}"
    if code and code not in detail:
        detail = f"{detail}（{code}）"
    return detail[:500]


async def generate_image(
    model_id: str,
    prompt: str,
    *,
    reference_urls: list[str] | None = None,
    size: str = "2K",
    watermark: bool = False,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> str:
    """调用 Ark images/generations 并返回临时图片 URL。"""
    creds = get_model_credentials(model_id)
    remote_model = _resolve_remote_model(model_id)
    size_norm = _normalize_ark_image_size(size, remote_model=remote_model)

    base = creds.api_base.rstrip("/")
    url = f"{base}/images/generations"

    payload: dict[str, Any] = {
        "model": remote_model,
        "prompt": prompt,
        "size": size_norm,
        "response_format": "url",
        "watermark": watermark,
    }
    # Seedream 5.0 Pro 不支持 sequential_image_generation；Lite / 4.5 仍可传 disabled
    if "seedream-5-0-pro" not in remote_model.lower():
        payload["sequential_image_generation"] = "disabled"

    refs = [u for u in (reference_urls or []) if u]
    if refs:
        refs = resolve_references_for_ark(
            refs,
            project_id=project_id,
            storage_folder=storage_folder,
        )
        payload["image"] = refs[0] if len(refs) == 1 else refs

    logger.info("[upstream] POST %s model=%s size=%s kind=image", url, remote_model, size_norm)
    note_upstream(
        provider="ark",
        event="submit",
        detail={"kind": "image_sync", "model": remote_model, "size": size_norm},
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {creds.api_key}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code >= 400:
            detail = _ark_http_error_detail(resp)
            logger.error("Jimeng image error %s: %s", resp.status_code, (resp.text or "")[:800])
            raise UpstreamError(detail, code="UPSTREAM_HTTP_ERROR")
        data = resp.json()

    note_upstream_billing_from_response(data, provider="ark", event="image_generation")
    items = data.get("data") or []
    if not items:
        raise RuntimeError("Image API returned empty data")
    image_url = items[0].get("url")
    if not isinstance(image_url, str) or not image_url.strip():
        raise RuntimeError("Image API returned no url")
    return image_url.strip()


async def download_image(url: str) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            logger.error("Image download error %s: %s", resp.status_code, url[:200])
            resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "image/png").split(";")[0].strip().lower()
        return resp.content, content_type
