"""平台素材库：上传、列表、更新、删除（媒体走平台 OSS）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage
from ..models.material_library import (
    MATERIAL_LIBRARY_CATEGORIES,
    PROMPT_LIBRARY_CATEGORY_NAME_MAX_LEN,
    PROMPT_LIBRARY_TEXT_MAX_LEN,
    MaterialLibraryItem,
    MaterialLibraryPromptCategory,
)
from .storage_urls import public_url_for_key

IMAGE_MAX_BYTES = 10 * 1024 * 1024
VIDEO_MAX_BYTES = 80 * 1024 * 1024

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
    "video/x-matroska",
    "video/x-msvideo",
    "video/avi",
    "video/mpeg",
    "video/x-m4v",
}
_VIDEO_EXT_TO_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".m4v": "video/x-m4v",
}

# 各类别默认媒体；特效库 / 提示词库可同时收视频与图片
_CATEGORY_MEDIA: dict[str, str] = {
    "style": "image",
    "effect": "video",
    "character": "image",
    "prompt": "image",  # 实际上传可图可视频，见 upload_material_media
}
# 特效库额外允许的图片（对齐 LibTV cinematic 动图/封面）
_EFFECT_IMAGE_MIME = {
    "image/gif",
    "image/webp",
    "image/png",
    "image/jpeg",
}


def normalize_prompt_category_name(raw: str | None) -> str:
    """规范化提示词库分类名称。"""
    return str(raw or "").strip()[:PROMPT_LIBRARY_CATEGORY_NAME_MAX_LEN]


def prompt_category_to_dict(row: MaterialLibraryPromptCategory) -> dict[str, Any]:
    """序列化提示词库二级分类。"""
    return {
        "id": str(row.id),
        "name": row.name or "",
        "sortOrder": int(row.sort_order or 0),
        "isActive": bool(row.is_active),
        "createdAt": to_cst_iso(row.created_at),
        "updatedAt": to_cst_iso(row.updated_at),
    }


async def list_prompt_categories(
    db: AsyncSession,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    """列出提示词库分类；用户侧仅活跃项。"""
    stmt = select(MaterialLibraryPromptCategory)
    if not include_inactive:
        stmt = stmt.where(MaterialLibraryPromptCategory.is_active.is_(True))
    stmt = stmt.order_by(
        MaterialLibraryPromptCategory.sort_order.asc(),
        MaterialLibraryPromptCategory.id.asc(),
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [prompt_category_to_dict(r) for r in rows]


async def get_prompt_category(
    db: AsyncSession, category_id: int
) -> MaterialLibraryPromptCategory | None:
    return await db.get(MaterialLibraryPromptCategory, category_id)


async def create_prompt_category(
    db: AsyncSession,
    *,
    name: str,
    sort_order: int = 0,
) -> MaterialLibraryPromptCategory:
    """新建提示词库分类；名称不可重复。"""
    clean = normalize_prompt_category_name(name)
    if not clean:
        raise ValueError("分类名称不能为空")
    row = MaterialLibraryPromptCategory(
        name=clean,
        sort_order=int(sort_order),
        is_active=True,
        created_at=now_cst_naive(),
        updated_at=now_cst_naive(),
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError("分类名称已存在") from exc
    await db.refresh(row)
    return row


async def update_prompt_category(
    db: AsyncSession,
    row: MaterialLibraryPromptCategory,
    *,
    name: str | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
) -> MaterialLibraryPromptCategory:
    if name is not None:
        clean = normalize_prompt_category_name(name)
        if not clean:
            raise ValueError("分类名称不能为空")
        row.name = clean
    if sort_order is not None:
        row.sort_order = int(sort_order)
    if is_active is not None:
        row.is_active = bool(is_active)
    row.updated_at = now_cst_naive()
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ValueError("分类名称已存在") from exc
    await db.refresh(row)
    return row


async def delete_prompt_category(db: AsyncSession, row: MaterialLibraryPromptCategory) -> None:
    """删除分类；其下条目变为未分类。"""
    cat_id = int(row.id)
    items = (
        await db.execute(
            select(MaterialLibraryItem).where(MaterialLibraryItem.prompt_category_id == cat_id)
        )
    ).scalars().all()
    now = now_cst_naive()
    for item in items:
        item.prompt_category_id = None
        item.updated_at = now
    await db.delete(row)
    await db.commit()


async def resolve_prompt_category_id(
    db: AsyncSession,
    raw: int | str | None,
    *,
    require_active: bool = False,
) -> int | None:
    """解析并校验提示词库分类 ID；空值表示未分类。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        cat_id = int(text)
    except ValueError as exc:
        raise ValueError("无效的分类") from exc
    if cat_id <= 0:
        return None
    row = await get_prompt_category(db, cat_id)
    if not row:
        raise ValueError("分类不存在")
    if require_active and not row.is_active:
        raise ValueError("分类已下架")
    return int(row.id)


def normalize_prompt_text(raw: str | None) -> str:
    """规范化提示词库正文；超长截断。"""
    text = str(raw or "").strip()
    if len(text) > PROMPT_LIBRARY_TEXT_MAX_LEN:
        return text[:PROMPT_LIBRARY_TEXT_MAX_LEN]
    return text


def normalize_category(raw: str | None) -> str | None:
    """规范化类别；非法返回 None。"""
    cat = str(raw or "").strip().lower()
    if cat in MATERIAL_LIBRARY_CATEGORIES:
        return cat
    return None


def expected_media_type(category: str) -> str:
    return _CATEGORY_MEDIA[category]


def _guess_image_mime(filename: str, content_type: str | None) -> str | None:
    mime = str(content_type or "").split(";")[0].strip().lower()
    if mime in _ALLOWED_IMAGE_MIME:
        return mime
    return _EXT_TO_MIME.get(Path(filename or "").suffix.lower())


def _guess_video_mime(filename: str, content_type: str | None) -> str | None:
    mime = str(content_type or "").split(";")[0].strip().lower()
    if mime in _ALLOWED_VIDEO_MIME:
        return mime
    return _VIDEO_EXT_TO_MIME.get(Path(filename or "").suffix.lower())


def upload_material_media(
    category: str,
    filename: str,
    data: bytes,
    content_type: str | None,
) -> dict[str, str]:
    """上传素材媒体到 ``platform/material-library/{category}/``。"""
    if not data:
        raise ValueError("文件为空")

    mime: str | None = None
    media_type: str
    ext: str

    if category in ("effect", "prompt"):
        # 特效 / 提示词：优先按视频识别，否则允许图片（提示词库收全量图片 MIME）
        mime = _guess_video_mime(filename, content_type)
        if mime:
            media_type = "video"
            if len(data) > VIDEO_MAX_BYTES:
                raise ValueError("视频不能超过 80MB")
            ext = next((e for e, m in _VIDEO_EXT_TO_MIME.items() if m == mime), ".mp4")
        else:
            mime = _guess_image_mime(filename, content_type)
            if category == "effect":
                if not mime or mime not in _EFFECT_IMAGE_MIME:
                    raise ValueError(
                        "特效库支持 MP4 / WebM / MOV / MKV / AVI / M4V，或 GIF / WebP / PNG / JPG"
                    )
            elif not mime:
                raise ValueError(
                    "提示词库支持图片（JPG/PNG/GIF/WebP/BMP）或视频（MP4/WebM/MOV/MKV/AVI/M4V）"
                )
            media_type = "image"
            if len(data) > IMAGE_MAX_BYTES:
                raise ValueError("图片/动图不能超过 10MB")
            ext = next((e for e, m in _EXT_TO_MIME.items() if m == mime), ".gif")
    elif expected_media_type(category) == "image":
        media_type = "image"
        if len(data) > IMAGE_MAX_BYTES:
            raise ValueError("图片不能超过 10MB")
        mime = _guess_image_mime(filename, content_type)
        if not mime:
            raise ValueError("仅支持 JPG / PNG / GIF / WebP / BMP")
        ext = next((e for e, m in _EXT_TO_MIME.items() if m == mime), ".jpg")
    else:
        media_type = "video"
        if len(data) > VIDEO_MAX_BYTES:
            raise ValueError("视频不能超过 80MB")
        mime = _guess_video_mime(filename, content_type)
        if not mime:
            raise ValueError("仅支持 MP4 / WebM / MOV / MKV / AVI / M4V")
        ext = next((e for e, m in _VIDEO_EXT_TO_MIME.items() if m == mime), ".mp4")

    file_id = uuid.uuid4().hex
    rel_path = f"material-library/{category}/{file_id}{ext}"
    storage = get_canvas_storage()
    try:
        stored = storage.put_platform(rel_path, data, mime)
    except StorageWriteError as exc:
        raise ValueError(str(exc)) from exc

    return {
        "ossKey": stored.oss_key,
        "mediaUrl": public_url_for_key(stored.oss_key),
        "mediaType": media_type,
    }


def item_to_dict(
    row: MaterialLibraryItem,
    *,
    prompt_category_name: str = "",
) -> dict[str, Any]:
    """序列化条目；mediaUrl 每次按当前 STORAGE_URL_MODE 重新生成（cdn 即稳定 CDN）。"""
    oss_key = str(row.oss_key or "")
    cat_id = getattr(row, "prompt_category_id", None)
    return {
        "id": str(row.id),
        "category": row.category,
        "title": row.title or "",
        "mediaType": row.media_type,
        "ossKey": oss_key,
        "mediaUrl": public_url_for_key(oss_key) if oss_key else "",
        "promptText": str(getattr(row, "prompt_text", None) or ""),
        "promptCategoryId": str(cat_id) if cat_id else None,
        "promptCategoryName": prompt_category_name or "",
        "sortOrder": int(row.sort_order or 0),
        "isActive": bool(row.is_active),
        "createdBy": str(row.created_by) if row.created_by is not None else None,
        "createdAt": to_cst_iso(row.created_at),
        "updatedAt": to_cst_iso(row.updated_at),
    }


async def list_items(
    db: AsyncSession,
    *,
    category: str | None = None,
    include_inactive: bool = False,
    prompt_category_id: int | None = None,
    uncategorized_only: bool = False,
) -> list[dict[str, Any]]:
    """按类别列出素材；用户侧默认仅活跃项。"""
    stmt = select(MaterialLibraryItem)
    if category:
        stmt = stmt.where(MaterialLibraryItem.category == category)
    if not include_inactive:
        stmt = stmt.where(MaterialLibraryItem.is_active.is_(True))
    if uncategorized_only:
        stmt = stmt.where(MaterialLibraryItem.prompt_category_id.is_(None))
    elif prompt_category_id is not None:
        stmt = stmt.where(MaterialLibraryItem.prompt_category_id == prompt_category_id)
    stmt = stmt.order_by(
        MaterialLibraryItem.sort_order.asc(),
        MaterialLibraryItem.id.desc(),
    )
    rows = (await db.execute(stmt)).scalars().all()
    names: dict[int, str] = {}
    if any(getattr(r, "prompt_category_id", None) for r in rows):
        cats = (
            await db.execute(select(MaterialLibraryPromptCategory))
        ).scalars().all()
        names = {int(c.id): str(c.name or "") for c in cats}
    out: list[dict[str, Any]] = []
    for r in rows:
        cid = getattr(r, "prompt_category_id", None)
        out.append(
            item_to_dict(
                r,
                prompt_category_name=names.get(int(cid), "") if cid else "",
            )
        )
    return out


async def get_item(db: AsyncSession, item_id: int) -> MaterialLibraryItem | None:
    return await db.get(MaterialLibraryItem, item_id)


async def create_item(
    db: AsyncSession,
    *,
    category: str,
    title: str,
    oss_key: str,
    media_type: str,
    created_by: int | None,
    sort_order: int = 0,
    prompt_text: str = "",
    prompt_category_id: int | None = None,
) -> MaterialLibraryItem:
    """写入素材库元数据行。"""
    # 仅提示词库写入正文与二级分类；其它类别保持空
    body = normalize_prompt_text(prompt_text) if category == "prompt" else ""
    cat_id = prompt_category_id if category == "prompt" else None
    row = MaterialLibraryItem(
        category=category,
        title=title.strip()[:128] or "未命名",
        media_type=media_type,
        oss_key=oss_key,
        prompt_text=body,
        prompt_category_id=cat_id,
        sort_order=int(sort_order),
        is_active=True,
        created_by=created_by,
        created_at=now_cst_naive(),
        updated_at=now_cst_naive(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_item(
    db: AsyncSession,
    row: MaterialLibraryItem,
    *,
    title: str | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
    prompt_text: str | None = None,
    prompt_category_id: int | None = None,
    clear_prompt_category: bool = False,
) -> MaterialLibraryItem:
    if title is not None:
        row.title = title.strip()[:128] or row.title
    if sort_order is not None:
        row.sort_order = int(sort_order)
    if is_active is not None:
        row.is_active = bool(is_active)
    if prompt_text is not None and row.category == "prompt":
        row.prompt_text = normalize_prompt_text(prompt_text)
    if row.category == "prompt":
        if clear_prompt_category:
            row.prompt_category_id = None
        elif prompt_category_id is not None:
            row.prompt_category_id = prompt_category_id
    row.updated_at = now_cst_naive()
    await db.commit()
    await db.refresh(row)
    return row


async def item_to_public_dict(db: AsyncSession, row: MaterialLibraryItem) -> dict[str, Any]:
    """序列化单条并带上分类名称。"""
    name = ""
    cid = getattr(row, "prompt_category_id", None)
    if cid:
        cat = await get_prompt_category(db, int(cid))
        name = (cat.name if cat else "") or ""
    return item_to_dict(row, prompt_category_name=name)


async def delete_item(db: AsyncSession, row: MaterialLibraryItem) -> None:
    """硬删除元数据（OSS 对象保留，避免误伤历史画布引用）。"""
    await db.delete(row)
    await db.commit()
