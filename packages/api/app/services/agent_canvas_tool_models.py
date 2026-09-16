"""画布工具主模型对照（助手用）。

权威来源：
1. 本文件默认表 = ``CANVAS_TOOL_MODEL_DEFS``（代码默认主模型）
2. 后台「模型开关」可覆盖；每轮【画布工具速查】注入的是**当前实配**主模型

助手执行 ``run_canvas_tool`` 时：对照速查表 / 本文件该功能标明的**主模型**；
前端按后台「模型开关」同一配置提交。新建节点 / ``generate_node`` 才用画布偏好生图/生视频模型。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .agent_canvas_tool_names import official_name_for_tool
from .canvas_tool_models import (
    CANVAS_TOOL_MODEL_DEFS,
    CANVAS_TOOL_MODEL_IDS,
    get_canvas_tool_models,
    normalize_canvas_tool_models,
)

# 无独立主模型配置的工具说明（不进模型开关）
_TOOL_MODEL_FALLBACK: dict[str, str] = {
    "grid_split": "本地切图不调模型",
    "storyboard_batch_videos": "用各镜视频节点已有模型",
}

_MODEL_DISPLAY: dict[str, str] | None = None


def default_tool_primary_models() -> dict[str, str]:
    """代码默认主模型（后台未改时的权威默认）。"""
    return {tid: primary for tid, _cat, primary in CANVAS_TOOL_MODEL_DEFS}


def tool_primary_models_from_cfg(cfg: dict[str, Any] | None) -> dict[str, str]:
    """从规范化后的 canvas_tool_models 配置取出各工具 primary。"""
    normalized = normalize_canvas_tool_models(cfg if isinstance(cfg, dict) else {})
    tools = normalized.get("tools") if isinstance(normalized.get("tools"), dict) else {}
    out = default_tool_primary_models()
    for tid in CANVAS_TOOL_MODEL_IDS:
        entry = tools.get(tid) if isinstance(tools, dict) else None
        if not isinstance(entry, dict):
            continue
        primary = str(entry.get("primary") or "").strip()
        if primary:
            out[tid] = primary
    return out


async def load_agent_tool_primary_models(db: AsyncSession) -> dict[str, str]:
    """读库：后台实配主模型（含默认回填）。"""
    cfg = await get_canvas_tool_models(db)
    return tool_primary_models_from_cfg(cfg)


def model_display_name(model_id: str) -> str:
    """模型 id → 目录展示名；未收录则空串。"""
    global _MODEL_DISPLAY
    mid = str(model_id or "").strip()
    if not mid:
        return ""
    if _MODEL_DISPLAY is None:
        from ..core.model_registry import CANVAS_MODEL_SPECS

        _MODEL_DISPLAY = {spec.name: spec.display_name for spec in CANVAS_MODEL_SPECS}
    return str(_MODEL_DISPLAY.get(mid) or "")


def format_tool_model_bit(tid: str, primary_by_tool: dict[str, str] | None = None) -> str:
    """单行：主模型=id「展示名」或本地说明。"""
    fallback = _TOOL_MODEL_FALLBACK.get(tid)
    if fallback:
        return fallback
    models = primary_by_tool if primary_by_tool is not None else default_tool_primary_models()
    mid = str(models.get(tid) or default_tool_primary_models().get(tid) or "").strip()
    if not mid:
        return ""
    disp = model_display_name(mid)
    if disp and disp != mid:
        return f"主模型={mid}「{disp}」"
    return f"主模型={mid}"


def format_agent_canvas_tool_models_doc(
    primary_by_tool: dict[str, str] | None = None,
) -> str:
    """写给助手/人看的完整对照表（默认或实配）。"""
    models = primary_by_tool if primary_by_tool is not None else default_tool_primary_models()
    lines = [
        "# 画布工具 · 主模型对照",
        "",
        "助手调用 `run_canvas_tool(tool=…)` 时，使用下表该功能的**主模型**（后台「模型开关」可改；",
        "每轮【画布工具速查】以实配为准）。前端执行时也会按同一配置提交。",
        "",
        "| 官方名 | tool id | 类别 | 主模型 | 展示名 |",
        "|--------|---------|------|--------|--------|",
    ]
    for tid, category, default_primary in CANVAS_TOOL_MODEL_DEFS:
        mid = str(models.get(tid) or default_primary).strip()
        name = official_name_for_tool(tid) or tid
        disp = model_display_name(mid) or mid
        lines.append(f"| {name} | `{tid}` | {category} | `{mid}` | {disp} |")
    lines.extend(
        [
            "",
            "## 无独立主模型的工具",
            "",
            "| tool id | 说明 |",
            "|---------|------|",
        ]
    )
    for tid, note in _TOOL_MODEL_FALLBACK.items():
        lines.append(f"| `{tid}` | {note} |")
    lines.append("")
    return "\n".join(lines)
