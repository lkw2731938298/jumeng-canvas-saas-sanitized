"""画布模型选择「系列」左侧栏展示顺序。

权威存 MySQL ``platform_settings.model_ui_series``；
系列名与模型 ``parameters.uiSeries`` / 前端 ``resolveModelSeries`` 输出的展示名精确匹配。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from .platform_settings import get_model_ui_series_raw, set_model_ui_series_raw

# 与模型目录 category 对齐
_ALLOWED_CATEGORIES = frozenset({"text", "image", "video", "audio", "tool"})


def normalize_model_ui_series(raw: Any) -> dict[str, Any]:
    """规范化系列顺序：去重 label、trim、排序、默认 enabled=true。"""
    version = 1
    series_in: list[Any] = []
    if isinstance(raw, dict):
        try:
            version = max(1, int(raw.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        src = raw.get("series")
        if isinstance(src, list):
            series_in = src
    elif isinstance(raw, list):
        series_in = raw

    series: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for entry in series_in:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or "").strip()
        if not label:
            continue
        # 与画布左侧系列名一致；过长截断防脏数据
        label = label[:64]
        if label in seen_labels:
            continue
        seen_labels.add(label)
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
        series.append(
            {
                "label": label,
                "sortOrder": sort_order,
                "enabled": bool(enabled),
                "categories": categories,
            }
        )

    series.sort(key=lambda t: (int(t.get("sortOrder") or 0), str(t.get("label") or "")))
    return {"version": version, "series": series}


async def get_model_ui_series(db: AsyncSession) -> dict[str, Any]:
    """读取并规范化系列顺序配置。"""
    raw = await get_model_ui_series_raw(db)
    return normalize_model_ui_series(raw)


async def set_model_ui_series(
    db: AsyncSession,
    payload: dict[str, Any] | list[Any],
    *,
    bump_version: bool = True,
) -> dict[str, Any]:
    """写入系列顺序；校验 label 后落库。允许空 series（清空，回退拼音序）。"""
    series_src: list[Any]
    if isinstance(payload, dict):
        series_src = payload.get("series") if isinstance(payload.get("series"), list) else []
    elif isinstance(payload, list):
        series_src = payload
    else:
        fail(ErrorCode.VALIDATION_ERROR, message="系列顺序格式无效")

    for entry in series_src:
        if not isinstance(entry, dict):
            fail(ErrorCode.VALIDATION_ERROR, message="系列项必须为对象")
        label = str(entry.get("label") or "").strip()
        if not label:
            fail(ErrorCode.VALIDATION_ERROR, message="系列名称不能为空")

    normalized = normalize_model_ui_series(
        {"version": payload.get("version") if isinstance(payload, dict) else 1, "series": series_src}
        if isinstance(payload, dict)
        else series_src
    )

    if bump_version:
        try:
            normalized["version"] = max(1, int(normalized.get("version") or 1) + 1)
        except (TypeError, ValueError):
            normalized["version"] = 2

    await set_model_ui_series_raw(db, normalized)
    return normalized


def public_model_ui_series(
    cfg: dict[str, Any],
    *,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """公开列表：仅 enabled；可选按 category 过滤（空 categories = 全类目）。"""
    normalized = normalize_model_ui_series(cfg)
    cat = (category or "").strip().lower() or None
    out: list[dict[str, Any]] = []
    for item in normalized["series"]:
        if not item.get("enabled"):
            continue
        cats = item.get("categories") or []
        if cat and cats and cat not in cats:
            continue
        out.append(
            {
                "label": item["label"],
                "sort_order": item.get("sortOrder", 0),
                "categories": list(cats),
            }
        )
    return out
