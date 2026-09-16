"""画布模型选择 UI 标签库。

权威存 MySQL ``platform_settings.model_ui_tags``；
模型通过 ``parameters.uiTagIds: string[]`` 多选绑定标签 id。
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .platform_settings import get_model_ui_tags_raw, set_model_ui_tags_raw

# 与模型目录 category 对齐
_ALLOWED_CATEGORIES = frozenset({"text", "image", "video", "audio", "tool"})
_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def read_model_ui_tag_ids(parameters: Any) -> list[str]:
    """从模型 parameters 读取 uiTagIds（去重、保序）。"""
    if not isinstance(parameters, dict):
        return []
    raw = parameters.get("uiTagIds")
    if raw is None:
        raw = parameters.get("ui_tag_ids")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tid = str(item or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def normalize_model_ui_tags(raw: Any) -> dict[str, Any]:
    """规范化标签库：去重 id、trim label、排序、默认 enabled=true。"""
    version = 1
    tags_in: list[Any] = []
    if isinstance(raw, dict):
        try:
            version = max(1, int(raw.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        src = raw.get("tags")
        if isinstance(src, list):
            tags_in = src
    elif isinstance(raw, list):
        tags_in = raw

    tags: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in tags_in:
        if not isinstance(entry, dict):
            continue
        tag_id = str(entry.get("id") or "").strip()
        label = str(entry.get("label") or "").strip()
        if not tag_id or not label:
            continue
        if not _ID_RE.match(tag_id):
            continue
        if tag_id in seen_ids:
            continue
        seen_ids.add(tag_id)
        try:
            sort_order = int(entry.get("sortOrder", entry.get("sort_order", 0)) or 0)
        except (TypeError, ValueError):
            sort_order = 0
        enabled = entry.get("enabled")
        if enabled is None:
            enabled = True
        cats_raw = entry.get("categories")
        categories: list[str] = []
        if isinstance(cats_raw, list):
            for c in cats_raw:
                cat = str(c or "").strip().lower()
                if cat in _ALLOWED_CATEGORIES and cat not in categories:
                    categories.append(cat)
        tags.append(
            {
                "id": tag_id,
                "label": label[:64],
                "sortOrder": sort_order,
                "enabled": bool(enabled),
                "categories": categories,
            }
        )

    tags.sort(key=lambda t: (int(t.get("sortOrder") or 0), str(t.get("label") or "")))
    return {"version": version, "tags": tags}


async def get_model_ui_tags(db: AsyncSession) -> dict[str, Any]:
    """读取并规范化标签库。"""
    raw = await get_model_ui_tags_raw(db)
    return normalize_model_ui_tags(raw)


async def set_model_ui_tags(
    db: AsyncSession,
    payload: dict[str, Any] | list[Any],
    *,
    bump_version: bool = True,
) -> dict[str, Any]:
    """写入标签库；校验 id/label 后落库。允许空 tags（清空库）。"""
    tags_src: list[Any]
    if isinstance(payload, dict):
        tags_src = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    elif isinstance(payload, list):
        tags_src = payload
    else:
        fail(ErrorCode.VALIDATION_ERROR, message="标签库格式无效")

    for entry in tags_src:
        if not isinstance(entry, dict):
            fail(ErrorCode.VALIDATION_ERROR, message="标签项必须为对象")
        tag_id = str(entry.get("id") or "").strip()
        label = str(entry.get("label") or "").strip()
        if not tag_id or not label:
            fail(ErrorCode.VALIDATION_ERROR, message="标签 id 与 label 不能为空")
        if not _ID_RE.match(tag_id):
            fail(
                ErrorCode.VALIDATION_ERROR,
                message="标签 id 仅允许字母数字、下划线、短横线（1–64）",
            )

    normalized = normalize_model_ui_tags(
        {"version": payload.get("version") if isinstance(payload, dict) else 1, "tags": tags_src}
        if isinstance(payload, dict)
        else tags_src
    )

    if bump_version:
        try:
            normalized["version"] = max(1, int(normalized.get("version") or 1) + 1)
        except (TypeError, ValueError):
            normalized["version"] = 2

    await set_model_ui_tags_raw(db, normalized)
    return normalized


def public_model_ui_tags(
    cfg: dict[str, Any],
    *,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """公开列表：仅 enabled；可选按 category 过滤（空 categories = 全类目）。"""
    normalized = normalize_model_ui_tags(cfg)
    cat = (category or "").strip().lower() or None
    out: list[dict[str, Any]] = []
    for tag in normalized["tags"]:
        if not tag.get("enabled"):
            continue
        cats = tag.get("categories") or []
        if cat and cats and cat not in cats:
            continue
        out.append(
            {
                "id": tag["id"],
                "label": tag["label"],
                "sort_order": tag.get("sortOrder", 0),
                "categories": list(cats),
            }
        )
    return out


def new_tag_id() -> str:
    """生成短标签 id（后台新建用）。"""
    return f"tag_{uuid.uuid4().hex[:10]}"
