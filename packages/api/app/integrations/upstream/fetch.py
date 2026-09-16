from __future__ import annotations

import base64

import httpx

from .errors import UpstreamError


def decode_data_url(url: str, *, max_bytes: int = 80 * 1024 * 1024) -> tuple[bytes, str]:
    """解码 data:image/...;base64,... 内联图（OpenAI b64_json 落库用）。"""
    raw = (url or "").strip()
    if not raw.startswith("data:"):
        raise UpstreamError("内联图片格式无效", code="INVALID_MEDIA")
    header, sep, payload = raw.partition(",")
    if not sep or not payload:
        raise UpstreamError("内联图片缺少内容", code="EMPTY_OUTPUT")
    mime = "image/png"
    meta = header[5:] if header.startswith("data:") else header
    if ";" in meta:
        mime = (meta.split(";", 1)[0] or mime).strip() or mime
    elif meta:
        mime = meta.strip() or mime
    try:
        data = base64.b64decode(payload, validate=False)
    except Exception as exc:
        raise UpstreamError("内联图片解码失败", code="EMPTY_OUTPUT") from exc
    if not data:
        raise UpstreamError("内联图片为空", code="EMPTY_OUTPUT")
    if len(data) > max_bytes:
        raise UpstreamError("媒体文件过大", code="DOWNLOAD_TOO_LARGE")
    return data, mime.split(";")[0].strip().lower() or "image/png"


async def download_bytes(url: str, *, max_bytes: int = 80 * 1024 * 1024, timeout_s: float = 180.0) -> tuple[bytes, str]:
    """下载 HTTP(S) 媒体；data: URL 则本地解码，不发起请求。"""
    raw = (url or "").strip()
    if raw.startswith("data:"):
        return decode_data_url(raw, max_bytes=max_bytes)
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=20.0), follow_redirects=True) as client:
        resp = await client.get(raw)
        if resp.status_code >= 400:
            raise UpstreamError(f"下载失败 HTTP {resp.status_code}", code="DOWNLOAD_FAILED")
        data = resp.content
        if len(data) > max_bytes:
            raise UpstreamError("媒体文件过大", code="DOWNLOAD_TOO_LARGE")
        ctype = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip().lower()
        return data, ctype


def download_bytes_sync(url: str, *, max_bytes: int = 80 * 1024 * 1024, timeout_s: float = 180.0) -> tuple[bytes, str]:
    raw = (url or "").strip()
    if raw.startswith("data:"):
        return decode_data_url(raw, max_bytes=max_bytes)
    with httpx.Client(timeout=httpx.Timeout(timeout_s, connect=20.0), follow_redirects=True) as client:
        resp = client.get(raw)
        if resp.status_code >= 400:
            raise UpstreamError(f"下载失败 HTTP {resp.status_code}", code="DOWNLOAD_FAILED")
        data = resp.content
        if len(data) > max_bytes:
            raise UpstreamError("媒体文件过大", code="DOWNLOAD_TOO_LARGE")
        ctype = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0].strip().lower()
        return data, ctype
