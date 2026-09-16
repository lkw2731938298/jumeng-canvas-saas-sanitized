"""Unified editable document format for all canvas prompt templates."""

from __future__ import annotations

import uuid
from copy import deepcopy
from typing import Any

from .prompt_templates import BUNDLED_DEFAULT_PATH, _load_json, load_store, save_store

SECTION_JOINER = "joiner"
SECTION_TOP_MENU = "top_menu"
SECTION_MA_CONSISTENCY = "multi_angle.consistency"
SECTION_MA_HORIZONTAL = "multi_angle.horizontal"
SECTION_MA_ELEVATION = "multi_angle.elevation"
SECTION_MA_SHOT = "multi_angle.shot"

TOP_MENU_ORDER = ["panorama", "lighting", "grid_9", "grid_split"]

DOCUMENT_HEADER = """# 画布 Prompt 模板（整块编辑，保存后前台即时生效）
# 以 [section] 分块；# 开头为注释；空行忽略

"""

SECTION_HELP = {
    SECTION_JOINER: "[joiner]\n多角度 prompt 片段之间的拼接符（通常为中文逗号）",
    SECTION_TOP_MENU: (
        "[top_menu]\n"
        "图片节点顶部菜单：工具ID | 菜单名 | 点击后追加的 prompt\n"
        "工具ID：panorama / lighting / grid_9 / grid_split"
    ),
    SECTION_MA_CONSISTENCY: "[multi_angle.consistency]\n多角度生成时追加的一致性约束（可多行）",
    SECTION_MA_HORIZONTAL: "[multi_angle.horizontal]\n水平方位：角度 | 显示名 | prompt 文案",
    SECTION_MA_ELEVATION: "[multi_angle.elevation]\n俯仰：角度 | 显示名 | prompt 文案",
    SECTION_MA_SHOT: "[multi_angle.shot]\n景别：key | 显示名 | prompt 文案（close / medium / wide）",
}


def _template_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("tool") or ""), str(item.get("category") or ""), str(item.get("key") or ""))


def _index_templates(items: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {_template_key(item): item for item in items if isinstance(item, dict)}


def _new_id(existing: dict[tuple[str, str, str], dict[str, Any]], tool: str, category: str, key: str) -> str:
    prior = existing.get((tool, category, key), {})
    if prior.get("id"):
        return str(prior["id"])
    return f"pt_{uuid.uuid4().hex[:12]}"


def _append_template(
    out: list[dict[str, Any]],
    existing: dict[tuple[str, str, str], dict[str, Any]],
    *,
    tool: str,
    category: str,
    key: str,
    label: str,
    content: str,
    sort_order: int,
) -> None:
    out.append(
        {
            "id": _new_id(existing, tool, category, key),
            "tool": tool,
            "category": category,
            "key": key,
            "label": label,
            "content": content,
            "enabled": True,
            "sort_order": sort_order,
        }
    )


def _parse_sections(text: str) -> dict[str, list[str]]:
    section: str | None = None
    lines: list[str] = []
    sections: dict[str, list[str]] = {}

    def flush() -> None:
        nonlocal lines
        if section is not None:
            sections[section] = lines
        lines = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            flush()
            section = stripped[1:-1].strip()
            continue
        if section is None:
            continue
        lines.append(raw.rstrip())

    flush()
    return sections


def _parse_pipe_row(line: str, expected: int, section: str) -> list[str]:
    parts = [part.strip() for part in line.split("|", expected - 1)]
    if len(parts) != expected or not all(parts):
        raise ValueError(f"{section} 行格式错误（需 {expected} 段，用 | 分隔）：{line}")
    return parts


def parse_document(text: str) -> list[dict[str, Any]]:
    sections = _parse_sections(text)
    existing = _index_templates(list((load_store().get("templates") or [])))
    templates: list[dict[str, Any]] = []

    joiner_lines = sections.get(SECTION_JOINER, [])
    joiner = "\n".join(joiner_lines).strip() if joiner_lines else "，"
    _append_template(
        templates,
        existing,
        tool="multi_angle",
        category="joiner",
        key="default",
        label="拼接符",
        content=joiner,
        sort_order=0,
    )

    consistency_lines = sections.get(SECTION_MA_CONSISTENCY, [])
    consistency = "\n".join(consistency_lines).strip()
    if consistency:
        _append_template(
            templates,
            existing,
            tool="multi_angle",
            category="consistency",
            key="default",
            label="一致性约束",
            content=consistency,
            sort_order=0,
        )

    for index, line in enumerate(sections.get(SECTION_TOP_MENU, [])):
        tool_id, label, content = _parse_pipe_row(line, 3, SECTION_TOP_MENU)
        _append_template(
            templates,
            existing,
            tool=tool_id,
            category="suffix",
            key="default",
            label=label,
            content=content,
            sort_order=index * 10,
        )

    for index, line in enumerate(sections.get(SECTION_MA_HORIZONTAL, [])):
        key, label, content = _parse_pipe_row(line, 3, SECTION_MA_HORIZONTAL)
        _append_template(
            templates,
            existing,
            tool="multi_angle",
            category="horizontal",
            key=key,
            label=label,
            content=content,
            sort_order=index * 10,
        )

    for index, line in enumerate(sections.get(SECTION_MA_ELEVATION, [])):
        key, label, content = _parse_pipe_row(line, 3, SECTION_MA_ELEVATION)
        _append_template(
            templates,
            existing,
            tool="multi_angle",
            category="elevation",
            key=key,
            label=label,
            content=content,
            sort_order=index * 10,
        )

    for index, line in enumerate(sections.get(SECTION_MA_SHOT, [])):
        key, label, content = _parse_pipe_row(line, 3, SECTION_MA_SHOT)
        _append_template(
            templates,
            existing,
            tool="multi_angle",
            category="shot",
            key=key,
            label=label,
            content=content,
            sort_order=index * 10,
        )

    if not templates:
        raise ValueError("文档为空或未包含任何有效配置块")

    return templates


def serialize_document(store: dict[str, Any] | None = None) -> str:
    data = store or load_store()
    items = [item for item in (data.get("templates") or []) if isinstance(item, dict)]

    by_key = _index_templates(items)

    def pick(tool: str, category: str, key: str) -> dict[str, Any] | None:
        return by_key.get((tool, category, key))

    def sorted_items(tool: str, category: str) -> list[dict[str, Any]]:
        matched = [item for item in items if item.get("tool") == tool and item.get("category") == category]
        matched.sort(key=lambda t: (int(t.get("sort_order") or 0), str(t.get("key") or "")))
        return matched

    lines: list[str] = [DOCUMENT_HEADER.strip(), ""]

    lines.append(f"[{SECTION_JOINER}]")
    joiner = pick("multi_angle", "joiner", "default")
    lines.append(str((joiner or {}).get("content") or "，"))
    lines.append("")

    lines.append(f"[{SECTION_TOP_MENU}]")
    for tool_id in TOP_MENU_ORDER:
        item = pick(tool_id, "suffix", "default")
        if item:
            lines.append(f"{tool_id} | {item.get('label', '')} | {item.get('content', '')}")
    lines.append("")

    lines.append(f"[{SECTION_MA_CONSISTENCY}]")
    consistency = pick("multi_angle", "consistency", "default")
    if consistency and consistency.get("content"):
        lines.append(str(consistency["content"]))
    lines.append("")

    lines.append(f"[{SECTION_MA_HORIZONTAL}]")
    for item in sorted_items("multi_angle", "horizontal"):
        lines.append(f"{item.get('key', '')} | {item.get('label', '')} | {item.get('content', '')}")
    lines.append("")

    lines.append(f"[{SECTION_MA_ELEVATION}]")
    for item in sorted_items("multi_angle", "elevation"):
        lines.append(f"{item.get('key', '')} | {item.get('label', '')} | {item.get('content', '')}")
    lines.append("")

    lines.append(f"[{SECTION_MA_SHOT}]")
    for item in sorted_items("multi_angle", "shot"):
        lines.append(f"{item.get('key', '')} | {item.get('label', '')} | {item.get('content', '')}")

    return "\n".join(lines).rstrip() + "\n"


async def save_document(text: str) -> None:
    templates = parse_document(text)
    await save_store({"version": 1, "templates": templates})


async def reset_document_to_default() -> str:
    bundled = _load_json(BUNDLED_DEFAULT_PATH)
    if not bundled:
        raise ValueError("Bundled default prompt templates not found")
    await save_store(deepcopy(bundled))
    return serialize_document(bundled)


def document_help_text() -> str:
    return "\n\n".join(SECTION_HELP.values())
