"""Login page GridMotion background images (OSS platform scope)."""

from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path
from typing import Any

from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage
from ..services.storage_urls import in_app_storage_url, public_url_for_key

logger = logging.getLogger(__name__)

AUTH_GRID_IMAGE_MAX_BYTES = 10 * 1024 * 1024
AUTH_GRID_IMAGE_MAX_COUNT = 24
# 网格单元约 400px：长边压到 480 + WebP，显著降低解码内存与传输
AUTH_GRID_DISPLAY_MAX_EDGE = 480
AUTH_GRID_WEBP_QUALITY = 65
# 已存大图（历史 JPG/PNG）展示时走 OSS 图片处理，避免浏览器解码原图
AUTH_GRID_OSS_PROCESS = "image/resize,l_480/format,webp/quality,q_65"
# 重压时若已是轻量 WebP 且体积不大则跳过
AUTH_GRID_SKIP_RECOMPRESS_BYTES = 90_000
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


def _guess_image_mime(filename: str, content_type: str | None) -> str | None:
    if content_type and content_type.split(";")[0].strip().lower() in _ALLOWED_IMAGE_MIME:
        return content_type.split(";")[0].strip().lower()
    ext = Path(filename or "").suffix.lower()
    return _EXT_TO_MIME.get(ext)


def resolve_homepage_background_url(oss_key: str | None) -> str:
    """登录/管理端展示 URL：带 OSS 缩略处理，减少传输与解码像素。"""
    key = str(oss_key or "").strip()
    if not key:
        return ""
    return (
        public_url_for_key(key, image_process=AUTH_GRID_OSS_PROCESS)
        or in_app_storage_url(key)
    )


def normalize_auth_grid_images(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        image_id = str(entry.get("id") or "").strip()
        oss_key = str(entry.get("ossKey") or entry.get("oss_key") or "").strip()
        if not image_id or not oss_key or image_id in seen_ids:
            continue
        seen_ids.add(image_id)
        sort_order = entry.get("sortOrder", entry.get("sort_order", index))
        try:
            sort_order = int(sort_order)
        except (TypeError, ValueError):
            sort_order = index
        items.append(
            {
                "id": image_id,
                "ossKey": oss_key,
                "sortOrder": sort_order,
            }
        )
    items.sort(key=lambda item: (item["sortOrder"], item["id"]))
    return items


def serialize_auth_grid_images_for_client(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "ossKey": item["ossKey"],
            "imageUrl": resolve_homepage_background_url(item["ossKey"]),
            "sortOrder": item["sortOrder"],
        }
        for item in items
    ]


def compress_auth_grid_image_for_display(data: bytes) -> tuple[bytes, str, str]:
    """将登录背景图压成 WebP 小图，供网格展示（失败则抛错由调用方回退原图）。"""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        # 动图只取首帧，避免网格背景体积失控
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in (img.mode or "") else "RGB")
        w, h = img.size
        max_edge = max(w, h)
        if max_edge > AUTH_GRID_DISPLAY_MAX_EDGE:
            scale = AUTH_GRID_DISPLAY_MAX_EDGE / float(max_edge)
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        # WebP 有损；无透明则转 RGB 体积更小
        if img.mode == "RGBA":
            # 网格背景多为不透明图：铺黑底后压 JPEG/WebP
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(
            out,
            format="WEBP",
            quality=AUTH_GRID_WEBP_QUALITY,
            method=4,
        )
        compressed = out.getvalue()
    if not compressed:
        raise ValueError("压缩结果为空")
    return compressed, "image/webp", ".webp"


# 登录弹窗左栏需要看清运营图，长边压到 1600
LOGIN_MODAL_DISPLAY_MAX_EDGE = 1600
LOGIN_MODAL_WEBP_QUALITY = 78
LOGIN_MODAL_OSS_PROCESS = "image/resize,l_1600/format,webp/quality,q_78"


def resolve_login_modal_image_url(oss_key: str | None) -> str:
    """登录弹窗左侧图展示 URL（比网格背景更大）。"""
    key = str(oss_key or "").strip()
    if not key:
        return ""
    return (
        public_url_for_key(key, image_process=LOGIN_MODAL_OSS_PROCESS)
        or in_app_storage_url(key)
    )


def compress_login_modal_image(data: bytes) -> tuple[bytes, str, str]:
    """将登录弹窗左栏图压成 WebP，保留较大边长。"""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in (img.mode or "") else "RGB")
        w, h = img.size
        max_edge = max(w, h)
        if max_edge > LOGIN_MODAL_DISPLAY_MAX_EDGE:
            scale = LOGIN_MODAL_DISPLAY_MAX_EDGE / float(max_edge)
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (245, 240, 232))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=LOGIN_MODAL_WEBP_QUALITY, method=4)
        compressed = out.getvalue()
    if not compressed:
        raise ValueError("压缩结果为空")
    return compressed, "image/webp", ".webp"


def upload_login_modal_image(
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    """上传登录弹窗左半边图片到平台 OSS。"""
    if not data:
        raise ValueError("文件为空")
    if len(data) > AUTH_GRID_IMAGE_MAX_BYTES:
        raise ValueError("图片不能超过 10MB")

    mime = _guess_image_mime(filename, content_type)
    if not mime:
        raise ValueError("仅支持 JPG / PNG / GIF / WebP / BMP")

    store_bytes = data
    store_mime = mime
    store_ext = next((e for e, m in _EXT_TO_MIME.items() if m == mime), ".jpg")
    try:
        store_bytes, store_mime, store_ext = compress_login_modal_image(data)
    except Exception as exc:
        logger.warning("login_modal compress failed, store original: %s", exc)

    image_id = uuid.uuid4().hex
    rel_path = f"homepage/login-modal/{image_id}{store_ext}"
    storage = get_canvas_storage()
    try:
        stored = storage.put_platform(rel_path, store_bytes, store_mime)
    except StorageWriteError as exc:
        raise ValueError(str(exc)) from exc

    return {
        "ossKey": stored.oss_key,
        "imageUrl": resolve_login_modal_image_url(stored.oss_key),
    }


def upload_auth_grid_image(
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    if not data:
        raise ValueError("文件为空")
    if len(data) > AUTH_GRID_IMAGE_MAX_BYTES:
        raise ValueError("图片不能超过 10MB")

    mime = _guess_image_mime(filename, content_type)
    if not mime:
        raise ValueError("仅支持 JPG / PNG / GIF / WebP / BMP")

    # 上传即压缩：后台缩略预览与登录页网格共用轻量 WebP
    store_bytes = data
    store_mime = mime
    store_ext = next((e for e, m in _EXT_TO_MIME.items() if m == mime), ".jpg")
    try:
        store_bytes, store_mime, store_ext = compress_auth_grid_image_for_display(data)
        logger.info(
            "auth_grid compress ok in=%s out=%s ratio=%.2f",
            len(data),
            len(store_bytes),
            (len(store_bytes) / len(data)) if data else 0,
        )
    except Exception as exc:
        logger.warning("auth_grid compress failed, store original: %s", exc)

    image_id = uuid.uuid4().hex
    rel_path = f"homepage/auth-grid/{image_id}{store_ext}"
    storage = get_canvas_storage()
    try:
        stored = storage.put_platform(rel_path, store_bytes, store_mime)
    except StorageWriteError as exc:
        raise ValueError(str(exc)) from exc

    return {
        "id": image_id,
        "ossKey": stored.oss_key,
        "imageUrl": resolve_homepage_background_url(stored.oss_key),
    }


def recompress_auth_grid_image_object(image_id: str, oss_key: str) -> tuple[str, str]:
    """从 OSS 读取已有图并重压为 WebP；返回 (新 ossKey, 动作: rewritten|skipped|failed)。"""
    storage = get_canvas_storage()
    raw = storage.get_bytes(oss_key)
    if not raw:
        logger.warning("auth_grid recompress miss key=%s", oss_key)
        return oss_key, "failed"

    key_lower = oss_key.lower()
    if key_lower.endswith(".webp") and len(raw) <= AUTH_GRID_SKIP_RECOMPRESS_BYTES:
        return oss_key, "skipped"

    try:
        compressed, store_mime, store_ext = compress_auth_grid_image_for_display(raw)
    except Exception as exc:
        logger.warning("auth_grid recompress compress fail id=%s: %s", image_id, exc)
        return oss_key, "failed"

    # 体积几乎没变且已是 webp：跳过写回
    if key_lower.endswith(".webp") and len(compressed) >= int(len(raw) * 0.9):
        return oss_key, "skipped"

    rel_path = f"homepage/auth-grid/{image_id}{store_ext}"
    try:
        stored = storage.put_platform(rel_path, compressed, store_mime)
    except StorageWriteError as exc:
        logger.warning("auth_grid recompress put fail id=%s: %s", image_id, exc)
        return oss_key, "failed"

    new_key = stored.oss_key
    if new_key != oss_key:
        try:
            storage.delete(oss_key)
        except Exception as exc:
            logger.debug("auth_grid delete old key ignored %s: %s", oss_key, exc)

    logger.info(
        "auth_grid recompress ok id=%s in=%s out=%s",
        image_id,
        len(raw),
        len(compressed),
    )
    return new_key, "rewritten"
