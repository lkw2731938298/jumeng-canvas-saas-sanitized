"""Visual style prompts for image/video node generation."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage
from ..services.storage_urls import (
    in_app_storage_url,
    normalize_browser_storage_url,
    oss_key_from_browser_url,
)
from . import prompt_config as cfg

NONE_STYLE_ID = "none"
VISUAL_STYLE_IMAGE_MAX_BYTES = 10 * 1024 * 1024
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
_STYLE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _tool_config() -> dict[str, Any]:
    tool = cfg.get_tool("visual_style")
    return tool if isinstance(tool, dict) else {}


def _resign_style_image_url(url: str) -> str:
    """预签名会过期：能解析 OSS key 则按当前模式重签，避免运营图「突然打不开」。"""
    value = str(url or "").strip()
    if not value:
        return ""
    return normalize_browser_storage_url(value) or value


def stabilize_visual_style_image_url(url: str) -> str:
    """落库用稳定代理 URL（或外链原样），禁止把带 Expires 的预签名写入配置。"""
    value = str(url or "").strip()
    if not value:
        return ""
    key = oss_key_from_browser_url(value)
    if key:
        return in_app_storage_url(key)
    return value


def resign_visual_styles_tool_config(tool: dict[str, Any]) -> dict[str, Any]:
    """API 返回前重签 visual_styles.items[].imageUrl。"""
    out = dict(tool)
    items: list[dict[str, Any]] = []
    for raw in out.get("items") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        raw_url = str(row.get("imageUrl") or row.get("image_url") or "").strip()
        row["imageUrl"] = _resign_style_image_url(raw_url)
        row.pop("image_url", None)
        items.append(row)
    out["items"] = items
    return out


def stabilize_visual_styles_tool_config(tool: dict[str, Any]) -> dict[str, Any]:
    """保存前把 OSS 预签名换成稳定指针，避免配置里堆过期链接。"""
    out = dict(tool)
    items: list[dict[str, Any]] = []
    for raw in out.get("items") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        raw_url = str(row.get("imageUrl") or row.get("image_url") or "").strip()
        row["imageUrl"] = stabilize_visual_style_image_url(raw_url)
        row.pop("image_url", None)
        items.append(row)
    out["items"] = items
    return out


def list_visual_styles(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    items = _tool_config().get("items") or []
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if not include_disabled and raw.get("enabled") is False:
            continue
        style_id = str(raw.get("id") or "").strip()
        if not style_id:
            continue
        raw_url = str(raw.get("imageUrl") or raw.get("image_url") or "").strip()
        out.append(
            {
                "id": style_id,
                "label": str(raw.get("label") or style_id).strip() or style_id,
                "prompt": str(raw.get("prompt") or "").strip(),
                "imageUrl": _resign_style_image_url(raw_url),
                "enabled": raw.get("enabled", True) is not False,
                "sortOrder": int(raw.get("sortOrder") or raw.get("sort_order") or 0),
            }
        )
    out.sort(key=lambda item: (item["sortOrder"], item["id"] != NONE_STYLE_ID, item["id"]))
    return out


def get_visual_style(style_id: str | None) -> dict[str, Any] | None:
    sid = str(style_id or "").strip()
    if not sid:
        return None
    for item in list_visual_styles(include_disabled=True):
        if item["id"] == sid:
            return item
    return None


def resolve_visual_style_prompt(style_id: str | None) -> str:
    sid = str(style_id or "").strip()
    if not sid or sid == NONE_STYLE_ID:
        return ""
    item = get_visual_style(sid)
    if not item or not item.get("enabled", True):
        return ""
    return str(item.get("prompt") or "").strip()


def apply_visual_style_to_prompt(user_prompt: str, style_id: str | None) -> str:
    style_prompt = resolve_visual_style_prompt(style_id)
    if not style_prompt:
        return user_prompt.strip()
    user = user_prompt.strip()
    if not user:
        return style_prompt
    joiner = str(_tool_config().get("joiner") or "，").strip() or "，"
    return f"{user}{joiner}{style_prompt}"


def _guess_image_mime(filename: str, content_type: str | None) -> str | None:
    if content_type and content_type.split(";")[0].strip().lower() in _ALLOWED_IMAGE_MIME:
        return content_type.split(";")[0].strip().lower()
    ext = Path(filename or "").suffix.lower()
    return _EXT_TO_MIME.get(ext)


def upload_visual_style_image(
    style_id: str,
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    """上传视觉风格展示图到平台 OSS。

    每次写入唯一对象路径（含 uuid），避免覆盖同名 key 后 CDN/浏览器仍显示旧图。
    """
    sid = str(style_id or "").strip()
    if not sid or not _STYLE_ID_RE.fullmatch(sid):
        raise ValueError("无效的风格 ID")
    if sid == NONE_STYLE_ID:
        raise ValueError("固定风格「无」无需上传展示图")
    if not data:
        raise ValueError("文件为空")
    if len(data) > VISUAL_STYLE_IMAGE_MAX_BYTES:
        raise ValueError("图片不能超过 10MB")

    mime = _guess_image_mime(filename, content_type)
    if not mime:
        raise ValueError("仅支持 JPG / PNG / GIF / WebP / BMP")

    ext = next((e for e, m in _EXT_TO_MIME.items() if m == mime), ".jpg")
    # 唯一路径：同风格多次上传 URL 必变，规避 CDN 同 key 缓存
    rel_path = f"visual-styles/{sid}/{uuid.uuid4().hex}{ext}"
    storage = get_canvas_storage()
    try:
        stored = storage.put_platform(rel_path, data, mime)
    except StorageWriteError as exc:
        raise ValueError(str(exc)) from exc

    browser_url = normalize_browser_storage_url(stored.file_url, oss_key=stored.oss_key)
    return {
        "imageUrl": browser_url or in_app_storage_url(stored.oss_key),
        "ossKey": stored.oss_key,
    }
