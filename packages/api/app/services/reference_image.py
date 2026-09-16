"""将画布参考图 URL 解析为外部上游 API 可用的公网 URL 或 base64。"""

from __future__ import annotations

import base64
import logging
from urllib.parse import parse_qs, unquote, urlparse

from ..integrations.oss.canvas_storage import get_canvas_storage
from .project_scope import assert_safe_storage_key, oss_key_belongs_to_project

logger = logging.getLogger(__name__)

_INTERNAL_PATH_MARKERS = (
    "/api/storage/object",
    "/api/v1/storage/object",
    "/api/proxy/api/v1/storage/object",
    "/api/proxy/v1/storage/object",
)


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")
_AUDIO_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")


def _path_has_extension(path: str, extensions: tuple[str, ...]) -> bool:
    return path.endswith(extensions)


def _url_extension_target(url: str) -> str:
    """Path or storage key used to infer media type from canvas proxy URLs."""
    cleaned = (url or "").strip().lower()
    if not cleaned:
        return ""
    path = cleaned.split("?", 1)[0]
    if _path_has_extension(path, _IMAGE_EXTENSIONS + _VIDEO_EXTENSIONS + _AUDIO_EXTENSIONS):
        return path
    try:
        oss_key = _extract_oss_key(cleaned)
    except ValueError:
        return path
    return (oss_key or path).lower()


def _is_probably_image_url(url: str) -> bool:
    cleaned = (url or "").strip().lower()
    if not cleaned:
        return False
    if cleaned.startswith("data:image/"):
        return True
    return _path_has_extension(_url_extension_target(cleaned), _IMAGE_EXTENSIONS)


def _is_probably_video_url(url: str) -> bool:
    cleaned = (url or "").strip().lower()
    if not cleaned:
        return False
    return _path_has_extension(_url_extension_target(cleaned), _VIDEO_EXTENSIONS)


def _is_probably_audio_url(url: str) -> bool:
    cleaned = (url or "").strip().lower()
    if not cleaned:
        return False
    return _path_has_extension(_url_extension_target(cleaned), _AUDIO_EXTENSIONS)


def _media_kind(url: str) -> str:
    """Classify reference URL as image | video | audio."""
    if _is_probably_video_url(url):
        return "video"
    if _is_probably_audio_url(url):
        return "audio"
    if _is_probably_image_url(url):
        return "image"
    oss_key = _extract_oss_key(url)
    if oss_key:
        lower = oss_key.lower()
        if lower.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v")):
            return "video"
        if lower.endswith((".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")):
            return "audio"
    return "image"


def _extract_oss_key(url: str) -> str | None:
    from .storage_urls import oss_key_from_browser_url

    key = oss_key_from_browser_url(url)
    if not key:
        return None
    try:
        return assert_safe_storage_key(key)
    except ValueError:
        return None


def _sniff_image_format(data: bytes) -> str:
    """Return data-URI subtype (jpeg/png/gif/webp) from magic bytes."""
    if len(data) < 12:
        raise ValueError("参考图文件无效或已损坏")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    head = data[:512].lstrip()
    if head.startswith((b"<?xml", b"<svg")) or b"<svg" in head[:128]:
        raise ValueError("参考图不支持 SVG，请使用 PNG 或 JPEG")
    raise ValueError("参考图格式不支持，请使用 PNG 或 JPEG")


def _is_internal_reference_url(url: str) -> bool:
    cleaned = (url or "").strip()
    if not cleaned:
        return False
    # 已解析为 data URI 或 OSS 预签名/CDN 公网 URL，无需再当内部存储处理
    if cleaned.startswith("data:"):
        return False
    from .storage_urls import _is_direct_oss_url

    if _is_direct_oss_url(cleaned):
        return False
    if _extract_oss_key(cleaned):
        return True
    lower = cleaned.lower()
    if any(marker in lower for marker in _INTERNAL_PATH_MARKERS):
        return True
    parsed = urlparse(cleaned)
    if not parsed.scheme:
        return True
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return True
    if host.startswith("192.168.") or host.startswith("10.") or host.endswith(".local"):
        return True
    from ..core.config import get_settings

    bucket = (get_settings().oss_bucket or "").strip().lower()
    if bucket and bucket in host:
        return True
    return False


def _mime_from_key(oss_key: str) -> str:
    lower = oss_key.lower()
    if lower.endswith(".png"):
        return "png"
    if lower.endswith((".jpg", ".jpeg")):
        return "jpeg"
    if lower.endswith(".webp"):
        return "webp"
    if lower.endswith(".gif"):
        return "gif"
    return "png"


def _content_type_from_key(oss_key: str) -> str:
    lower = oss_key.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".bmp"):
        return "image/bmp"
    if lower.endswith(".svg"):
        return "image/svg+xml"
    return "application/octet-stream"


def upstream_fetchable_url_for_key(oss_key: str, *, expires_seconds: int | None = None) -> str | None:
    """上游服务器拉取参考媒体：OSS 公网预签名 HTTPS（勿用 CDN，外部网关常拉不到）。"""
    from ..core.config import get_settings
    from ..integrations.oss.service import _ensure_https_url, get_oss

    settings = get_settings()
    if settings.canvas_storage_local_only:
        return None
    oss = get_oss()
    if not oss.bucket:
        return None
    storage = get_canvas_storage()
    content_type = _content_type_from_key(oss_key)
    if not oss.exists(oss_key):
        storage.ensure_oss_object(oss_key, content_type=content_type)
    if not oss.exists(oss_key):
        data = storage.get_bytes(oss_key)
        if data:
            oss.bucket.put_object(oss_key, data, headers={"Content-Type": content_type})
    if not oss.exists(oss_key):
        return None
    ttl = int(expires_seconds or settings.oss_signed_url_ttl_seconds or 86400)
    signed = oss.presign_get(oss_key, ttl)
    if signed and signed.startswith("http"):
        return _ensure_https_url(signed)
    return None


def _presign_oss_reference(oss_key: str) -> str | None:
    """将参考媒体转为上游可拉取的公网 URL（cdn 模式为稳定 CDN，否则预签名）。"""
    from ..core.config import get_settings
    from ..integrations.oss.service import get_oss
    from .storage_urls import public_url_for_key

    if get_settings().canvas_storage_local_only:
        return None

    oss = get_oss()
    if not oss.bucket:
        return None

    storage = get_canvas_storage()
    content_type = _content_type_from_key(oss_key)
    if not oss.exists(oss_key):
        storage.ensure_oss_object(oss_key, content_type=content_type)
    if not oss.exists(oss_key):
        data = storage.get_bytes(oss_key)
        if data:
            oss.bucket.put_object(oss_key, data, headers={"Content-Type": content_type})
            logger.info("Uploaded reference image to OSS %s (%d bytes)", oss_key, len(data))
    if oss.exists(oss_key):
        # 中文：业务展示与上游参考统一走 public_url_for_key，禁止硬编码 presign_get
        url = public_url_for_key(oss_key)
        return url if url.startswith("http://") or url.startswith("https://") else None
    return None


def resolve_reference_for_ark(
    url: str,
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> str:
    """将内部存储参考图解析为 Ark 可接受的公网 URL 或 data:image base64。"""
    cleaned = url.strip()
    if not cleaned:
        return cleaned

    if not _is_internal_reference_url(cleaned):
        return cleaned

    oss_key = _extract_oss_key(cleaned)
    if not oss_key:
        raise ValueError("Reference image URL is not reachable by Jimeng API (missing storage key)")

    if project_id and not oss_key_belongs_to_project(
        oss_key, project_id, storage_folder=storage_folder
    ):
        raise ValueError("Reference image does not belong to this project")

    data = get_canvas_storage().get_bytes(oss_key)
    if not data:
        raise ValueError(f"Reference image not found in project storage: {oss_key}")

    if len(data) > 10 * 1024 * 1024:
        raise ValueError("Reference image exceeds 10MB limit for Jimeng API")

    mime = _sniff_image_format(data)
    encoded = base64.b64encode(data).decode("ascii")
    logger.info("Resolved internal reference to base64 (%s, %d bytes, %s)", oss_key, len(data), mime)
    return f"data:image/{mime};base64,{encoded}"


def resolve_references_for_ark(
    urls: list[str],
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> list[str]:
    """批量将参考图 URL 解析为 Ark 可用格式。"""
    return [
        resolve_reference_for_ark(u, project_id=project_id, storage_folder=storage_folder)
        for u in urls
        if u and u.strip()
    ]


def resolve_reference_url(
    url: str,
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> str:
    """将内部画布存储 URL 解析为上游 API 可用的预签名 OSS 或 base64。"""
    from ..core.config import get_settings
    from .reference_video import (
        resolve_audio_reference_url,
        resolve_video_reference_for_multimodal,
    )

    cleaned = url.strip()
    if not cleaned:
        return cleaned
    if cleaned.startswith("data:"):
        return cleaned
    if not _is_internal_reference_url(cleaned):
        return cleaned

    kind = _media_kind(cleaned)
    if kind == "video":
        video_https, frame_uri = resolve_video_reference_for_multimodal(
            cleaned, project_id=project_id, storage_folder=storage_folder
        )
        if frame_uri:
            return frame_uri
        if video_https:
            return video_https
        raise ValueError(f"参考视频无法解析: {_extract_oss_key(cleaned) or cleaned}")

    if kind == "audio":
        return resolve_audio_reference_url(
            cleaned, project_id=project_id, storage_folder=storage_folder
        )

    if get_settings().canvas_storage_local_only:
        return resolve_reference_for_ark(
            cleaned, project_id=project_id, storage_folder=storage_folder
        )

    oss_key = _extract_oss_key(cleaned)
    if oss_key:
        presigned = _presign_oss_reference(oss_key)
        if presigned:
            return presigned

    return resolve_reference_for_ark(
        cleaned, project_id=project_id, storage_folder=storage_folder
    )


def resolve_reference_urls(
    urls: list[str],
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> list[str]:
    """批量将参考媒体 URL 解析为上游 API 可用格式。"""
    return [
        resolve_reference_url(u, project_id=project_id, storage_folder=storage_folder)
        for u in urls
        if u and u.strip()
    ]


def resolve_reference_url_for_upstream(
    url: str,
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> str:
    """聚梦等外部网关：画布/CDN URL → OSS 预签名直链（图片失败时降级 base64）。"""
    cleaned = url.strip()
    if not cleaned:
        return cleaned
    if cleaned.startswith("data:"):
        return cleaned

    oss_key = _extract_oss_key(cleaned)
    if oss_key:
        if project_id and not oss_key_belongs_to_project(
            oss_key, project_id, storage_folder=storage_folder
        ):
            raise ValueError("参考媒体不属于当前项目")
        presigned = upstream_fetchable_url_for_key(oss_key)
        if presigned:
            logger.info("Upstream reference presigned: %s", oss_key)
            return presigned
        if _media_kind(cleaned) == "image":
            return resolve_reference_for_ark(
                cleaned, project_id=project_id, storage_folder=storage_folder
            )
        raise ValueError(f"参考媒体无法解析为上游可拉取 URL: {oss_key}")

    if _is_internal_reference_url(cleaned):
        return resolve_reference_url(
            cleaned, project_id=project_id, storage_folder=storage_folder
        )
    return cleaned


def resolve_reference_urls_for_upstream(
    urls: list[str],
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> list[str]:
    """批量解析为上游可拉取的 OSS 预签名 URL（聚梦网关专用）。"""
    return [
        resolve_reference_url_for_upstream(
            u, project_id=project_id, storage_folder=storage_folder
        )
        for u in urls
        if u and str(u).strip()
    ]
