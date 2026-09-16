"""发现页运营图片 / 视频上传（平台级 OSS 目录）。"""

from __future__ import annotations

import uuid
from pathlib import Path

from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage
from .storage_urls import in_app_storage_url, public_url_for_key

DISCOVER_IMAGE_MAX_BYTES = 10 * 1024 * 1024
# Hero 背景视频：允许较大体积，避免运营素材被过早拒绝
DISCOVER_VIDEO_MAX_BYTES = 80 * 1024 * 1024
_ALLOWED_IMAGE_MIME = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
}
_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
_ALLOWED_VIDEO_MIME = {
    "video/mp4",
    "video/webm",
    "video/quicktime",
}
_VIDEO_EXT_TO_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}


def _guess_image_mime(filename: str, content_type: str | None) -> str | None:
    mime = str(content_type or "").split(";")[0].strip().lower()
    if mime in _ALLOWED_IMAGE_MIME:
        return mime
    return _EXT_TO_MIME.get(Path(filename or "").suffix.lower())


def _public_url(oss_key: str) -> str:
    return public_url_for_key(oss_key) or in_app_storage_url(oss_key)


def _upload_platform_content_image(
    folder: str,
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    if not data:
        raise ValueError("文件为空")
    if len(data) > DISCOVER_IMAGE_MAX_BYTES:
        raise ValueError("图片不能超过 10MB")

    mime = _guess_image_mime(filename, content_type)
    if not mime:
        raise ValueError("仅支持 JPG / PNG / GIF / WebP / BMP")

    image_id = uuid.uuid4().hex
    ext = next((e for e, m in _EXT_TO_MIME.items() if m == mime), ".jpg")
    rel_path = f"{folder}/{image_id}{ext}"
    storage = get_canvas_storage()
    try:
        stored = storage.put_platform(rel_path, data, mime)
    except StorageWriteError as exc:
        raise ValueError(str(exc)) from exc

    return {
        "id": image_id,
        "ossKey": stored.oss_key,
        "imageUrl": _public_url(stored.oss_key),
    }


def upload_discover_image(
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    """写入 ``platform/discover/images``，返回持久化 OSS key 与访问 URL。"""
    return _upload_platform_content_image(
        "discover/images",
        filename,
        data,
        content_type,
    )


def upload_skill_cover_image(
    filename: str,
    data: bytes,
    content_type: str | None,
    *,
    user_id: int,
) -> dict[str, str]:
    """用户 Skill 封面：写入 ``platform/skill-covers/{userId}``。"""
    uid = max(int(user_id), 0)
    return _upload_platform_content_image(
        f"skill-covers/{uid}",
        filename,
        data,
        content_type,
    )


def upload_credit_activity_image(
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    """写入 ``platform/credit-activities/images``，供活动页卡片展示。"""
    return _upload_platform_content_image(
        "credit-activities/images",
        filename,
        data,
        content_type,
    )


def upload_footer_image(
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    """写入 ``platform/footer/images``，供联系我们二维码等展示。"""
    return _upload_platform_content_image(
        "footer/images",
        filename,
        data,
        content_type,
    )


def _guess_video_mime(filename: str, content_type: str | None) -> str | None:
    mime = str(content_type or "").split(";")[0].strip().lower()
    if mime in _ALLOWED_VIDEO_MIME:
        return mime
    # 部分浏览器对 mp4 只给 application/octet-stream，按扩展名回退
    return _VIDEO_EXT_TO_MIME.get(Path(filename or "").suffix.lower())


def upload_discover_video(
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    """写入 ``platform/discover/videos``，返回持久化 OSS key 与访问 URL。"""
    if not data:
        raise ValueError("文件为空")
    if len(data) > DISCOVER_VIDEO_MAX_BYTES:
        raise ValueError("视频不能超过 80MB")

    mime = _guess_video_mime(filename, content_type)
    if not mime:
        raise ValueError("仅支持 MP4 / WebM / MOV")

    video_id = uuid.uuid4().hex
    ext = next((e for e, m in _VIDEO_EXT_TO_MIME.items() if m == mime), ".mp4")
    rel_path = f"discover/videos/{video_id}{ext}"
    storage = get_canvas_storage()
    try:
        stored = storage.put_platform(rel_path, data, mime)
    except StorageWriteError as exc:
        raise ValueError(str(exc)) from exc

    return {
        "id": video_id,
        "ossKey": stored.oss_key,
        "videoUrl": _public_url(stored.oss_key),
    }
