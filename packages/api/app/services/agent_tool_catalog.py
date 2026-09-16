"""画布工具/节点目录：官方名 + 功能特点 + 主模型，供助手每轮对照选工具。

完整说明书仍走 list_canvas_tools（agent_canvas_tools.md）。
主模型对照见 agent_canvas_tool_models.md / agent_canvas_tool_models.py；
每轮速查可注入后台「模型开关」实配主模型。
本模块不在 import 时拉 agent_tools，避免循环依赖。
"""

from __future__ import annotations

from typing import Any

from .agent_canvas_tool_models import (
    default_tool_primary_models,
    format_tool_model_bit,
    model_display_name,
)
from .agent_canvas_tool_names import official_name_for_tool

# 分组：官方名对照 + 选工具。顺序即助手扫描顺序。
_TOOL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "改已有图 · 须 image_input 且 hasMedia=1",
        (
            "multi_angle",
            "lighting",
            "outpaint",
            "cutout",
            "hd_upscale",
            "portrait_adjust",
            "emotion_adjust",
            "visual_style",
            "panorama",
            "panorama_720",
        ),
    ),
    (
        "设定/宫格 · 前置 image_input；设定图可无参考文生",
        (
            "grid_9",
            "grid_25",
            "plot_grid_4",
            "frame_forward_3s",
            "frame_back_5s",
            "cinematic_lighting",
            "multi_cam_grid_9",
            "face_tri_view",
            "character_sheet",
            "character_tri_view",
            "scene_sheet",
            "product_sheet",
            "grid_split",
        ),
    ),
    (
        "故事板合成图 · 一张多格不是表格；params.userPrompt 必填",
        ("storyboard", "blocking_storyboard"),
    ),
    (
        "分镜表 · 前置 storyboard_grid（拉片/提图另见行内）",
        (
            "storyboard_table",
            "storyboard_from_image",
            "storyboard_from_video",
            "storyboard_overseas_localize",
            "text_subject",
            "storyboard_camera",
            "storyboard_video",
            "storyboard_sketch",
            "storyboard_subject_image",
            "storyboard_batch_videos",
        ),
    ),
    (
        "视频画面 · 须 video_input 且 hasMedia=1",
        (
            "hd_upscale_video",
            "video_smart_matting",
            "video_subject_remove",
            "video_subject_edit",
            "video_subject_replace",
            "video_subtitle_smart_erase",
            "video_subtitle_box_erase",
        ),
    ),
    (
        "音频 · 前置 audio_input 或视频音轨",
        ("vocal_separate", "vocal_remove"),
    ),
    ("其它", ("drawing_board_ai",)),
)


def format_node_model_bit(model_id: str) -> str:
    """节点目录 model=id「展示名」。"""
    mid = str(model_id or "").strip()
    if not mid:
        return ""
    disp = model_display_name(mid)
    if disp and disp != mid:
        return f" model={mid}「{disp}」"
    return f" model={mid}"


def node_catalog_capability(
    *,
    node_type: str,
    has_media: bool,
    role: str = "",
    label: str = "",
) -> str:
    """节点目录 can=：这类节点能跑哪些画布工具/生成。"""
    t = str(node_type or "").strip()
    label_l = str(label or "")
    is_board = str(role or "").strip() == "storyboard_sheet" or any(
        k in label_l for k in ("故事板", "调度故事板")
    )
    if t == "image_input":
        if is_board:
            return "故事板合成图;再生成;可读板出视频(须确认)"
        if has_media:
            return "再生成;多角度/打光/九宫格/抠图/扩图/高清/故事板"
        return "文生图;角色/场景/产品设定图(可无参考);故事板(须userPrompt)"
    if t == "video_input":
        if has_media:
            return "再生成;主体消除/修改/替换/去字幕/视频高清"
        return "文生/图生视频(须确认生成)"
    if t == "audio_input":
        return "人声分离/消除人声" if has_media else "配乐/配音"
    if t == "storyboard_grid":
        return "分镜表解析/主体提取/运镜词/视频词/草图;批量出视频(须确认)"
    if t == "text_input":
        return "剧本/提示词;可作主体提取输入"
    if t == "document_input":
        return "文档参考"
    if t == "director_stage":
        return "导演台调度截图"
    return ""


def _short_feat(tid: str, hint: str, *, detailed: bool) -> str:
    """速查截短 hint；详细索引保留全文（含关键 params）。"""
    raw = str(hint or "").strip()
    if detailed or not raw:
        return raw
    return raw[:72]


def _grouped_tool_ids() -> list[tuple[str, str]]:
    """(分组标题, tool_id)，未分组的并入「其它」。"""
    from .agent_tools import CANVAS_RUN_TOOL_IDS

    grouped: set[str] = {tid for _title, ids in _TOOL_GROUPS for tid in ids}
    leftovers = tuple(tid for tid in CANVAS_RUN_TOOL_IDS if tid not in grouped)
    out: list[tuple[str, str]] = []
    saw_other = False
    for title, ids in _TOOL_GROUPS:
        extra = leftovers if title == "其它" else ()
        if title == "其它":
            saw_other = True
        for tid in (*ids, *extra):
            out.append((title, tid))
    if leftovers and not saw_other:
        for tid in leftovers:
            out.append(("其它", tid))
    return out


def _format_tool_rows(
    *,
    detailed: bool,
    primary_by_tool: dict[str, str] | None = None,
) -> str:
    from .agent_tools import CANVAS_TOOL_NODE_HINTS

    models = primary_by_tool if primary_by_tool is not None else default_tool_primary_models()
    lines: list[str] = []
    last_group = ""
    for group, tid in _grouped_tool_ids():
        if group != last_group:
            lines.append(f"■ {group}")
            last_group = group
        name = official_name_for_tool(tid)
        feat = _short_feat(tid, CANVAS_TOOL_NODE_HINTS.get(tid, ""), detailed=detailed)
        model_txt = format_tool_model_bit(tid, models)
        bits = [f"「{name}」", f"`{tid}`"]
        if feat:
            bits.append(feat)
        if model_txt:
            bits.append(model_txt)
        prefix = "- " if detailed else ""
        lines.append(prefix + " ".join(bits))
    return "\n".join(lines)


def format_canvas_tools_quickref(
    primary_by_tool: dict[str, str] | None = None,
) -> str:
    """每轮注入的紧凑速查：官方名、id、特点、主模型（实配或默认）。"""
    head = (
        "点名官方名 → run_canvas_tool(tool=id)。先对照本表再动手；"
        "表中「主模型=」即该功能指定模型（后台可改，本表为当前实配）。"
        "params 细节再 list_canvas_tools。有【本轮点名/推断工具】必须用那个 id。"
    )
    return f"{head}\n{_format_tool_rows(detailed=False, primary_by_tool=primary_by_tool)}"


def format_canvas_tools_detailed_index(
    primary_by_tool: dict[str, str] | None = None,
) -> str:
    """list_canvas_tools 用：分组 + 完整 hint + 主模型展示名。"""
    head = (
        "分组对照：官方名 → tool id → 功能要点 → 主模型"
        "（下表为当前实配；换未列出的模型先 list_models）。"
    )
    return f"{head}\n{_format_tool_rows(detailed=True, primary_by_tool=primary_by_tool)}"


def format_preferred_models_line(snapshot: dict[str, Any] | None) -> str:
    """画布偏好生图/生视频模型，带展示名。"""
    if not isinstance(snapshot, dict):
        return ""
    pref_img = str(snapshot.get("preferredImageModel") or "").strip()
    pref_vid = str(snapshot.get("preferredVideoModel") or "").strip()
    if not pref_img and not pref_vid:
        return ""
    bits: list[str] = []
    if pref_img:
        disp = model_display_name(pref_img)
        extra = f"「{disp}」" if disp else ""
        bits.append(f"生图={pref_img}{extra}")
    if pref_vid:
        disp = model_display_name(pref_vid)
        extra = f"「{disp}」" if disp else ""
        bits.append(f"生视频={pref_vid}{extra}")
    return (
        "用户画布操控偏好模型（仅新建/generate_node 优先写入 params.model；"
        "run_canvas_tool 用上方工具主模型，勿与偏好模型混用）："
        + "；".join(bits)
    )
