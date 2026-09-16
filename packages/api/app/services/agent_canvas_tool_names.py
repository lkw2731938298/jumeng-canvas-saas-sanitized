"""画布工具官方中文名 ↔ tool id。用户点名必须对上，禁止模糊近义猜测。

另有高置信口语软映射（看背面→多角度等），仅在官方名未命中时启用，减少无意义 ask_user。
"""

from __future__ import annotations

import re

# 顶栏 / 九宫格弹层上的官方名（与用户可见按钮一致）。不含「侧面」「光不好」这类近义说法。
_UI_NAME_TO_TOOL: tuple[tuple[str, str], ...] = (
    ("多角度", "multi_angle"),
    ("打光", "lighting"),
    ("全景", "panorama"),
    ("720° 全景", "panorama_720"),
    ("720全景", "panorama_720"),
    ("九宫格", "grid_9"),
    ("调度故事板", "blocking_storyboard"),
    ("故事板", "storyboard"),
    ("25宫格连贯分镜", "grid_25"),
    ("剧情推演四宫格", "plot_grid_4"),
    ("画面推演 - 3秒后", "frame_forward_3s"),
    ("画面推演3秒后", "frame_forward_3s"),
    ("画面推演 - 5秒前", "frame_back_5s"),
    ("画面推演5秒前", "frame_back_5s"),
    ("电影级光影校正", "cinematic_lighting"),
    ("多机位九宫格", "multi_cam_grid_9"),
    ("角色脸部三视图", "face_tri_view"),
    ("角色设定图", "character_sheet"),
    ("九宫格·角色设定图", "character_sheet"),
    ("角色三视图", "character_tri_view"),
    ("九宫格·角色三视图", "character_tri_view"),
    ("场景设定图", "scene_sheet"),
    ("九宫格·场景设定图", "scene_sheet"),
    ("产品设定图", "product_sheet"),
    ("九宫格·产品设定图", "product_sheet"),
    ("宫格切分", "grid_split"),
    ("扩图", "outpaint"),
    ("抠图", "cutout"),
    ("高清", "hd_upscale"),
    ("人像调节", "portrait_adjust"),
    ("情绪调节", "emotion_adjust"),
    ("画板 AI", "drawing_board_ai"),
    ("视频高清", "hd_upscale_video"),
    ("智能抠像", "video_smart_matting"),
    ("主体消除", "video_subject_remove"),
    ("主体修改", "video_subject_edit"),
    ("主体替换", "video_subject_replace"),
    ("智能去字幕·智能擦除", "video_subtitle_smart_erase"),
    ("智能去字幕·框选擦除", "video_subtitle_box_erase"),
    ("人声分离", "vocal_separate"),
    ("消除人声", "vocal_remove"),
    ("分镜表解析", "storyboard_table"),
    ("爆款拉片复刻", "storyboard_from_video"),
    ("图片提取分镜", "storyboard_from_image"),
    ("一键出海本地化", "storyboard_overseas_localize"),
    ("主体提取", "text_subject"),
    ("运镜提示词", "storyboard_camera"),
    ("视频提示词", "storyboard_video"),
    ("分镜草图", "storyboard_sketch"),
    ("主体生图", "storyboard_subject_image"),
)

_SKIP_LABEL_FRAGMENTS = ("兼容",)

# 高置信口语 → 工具（仅官方名未命中时）。(正则, tool_id, 展示语, 参数提示)
_SOFT_EFFECT_RULES: tuple[tuple[re.Pattern[str], str, str, str], ...] = (
    (
        re.compile(r"看背面|转到后面|后视图|从背面"),
        "multi_angle",
        "看背面",
        "params.azimuth=180",
    ),
    (
        re.compile(r"换个角度|换角度|看侧面|侧视图|转到侧面"),
        "multi_angle",
        "换角度",
        "",
    ),
    (
        re.compile(r"光不好|补光|打个光|光线太暗|太暗了|加个光|改一下.{0,8}光|调一下光|光线不对"),
        "lighting",
        "补光",
        "",
    ),
    (
        re.compile(r"做几个变体|多几个变体|来几个变体|九宫格变体"),
        "grid_9",
        "变体",
        "",
    ),
    (
        re.compile(r"去背景|抠一下|抠掉背景|去掉背景"),
        "cutout",
        "去背景",
        "",
    ),
    (
        re.compile(r"扩一下|向外扩|画布扩边"),
        "outpaint",
        "扩画布",
        "",
    ),
    (
        re.compile(r"高清一下|超分|变清晰|放大高清|清晰一点"),
        "hd_upscale",
        "变清晰",
        "",
    ),
    (
        re.compile(
            r"主体修改|改主体|把主体改|换成动漫|改成动漫|二次元化|改成二次元|变成动漫|"
            r"改成穿|换成穿|把片里|把视频里的|把男生改|把女生改|西服.*改成|西装.*改成"
        ),
        "video_subject_edit",
        "主体修改",
        "params.userPrompt=改成什么样；主模型见速查表",
    ),
    (
        re.compile(r"主体替换|换主体|替换主体"),
        "video_subject_replace",
        "主体替换",
        "需参考图；params.refImageNodeId 或先把图连到视频 ref_in",
    ),
    (
        re.compile(r"人设图|角色设定(?!图)|出个人设"),
        "character_sheet",
        "人设图",
        "",
    ),
    (
        re.compile(r"做个分镜表|填分镜表|写分镜表|生成分镜表"),
        "storyboard_table",
        "分镜表",
        "",
    ),
    (
        re.compile(r"按这[条段个]?片?.{0,8}拆|拆成镜|视频拆镜|拉片填表|按视频拆镜"),
        "storyboard_from_video",
        "拆镜",
        "",
    ),
    (
        re.compile(r"去字幕|擦字幕|抹掉字幕"),
        "video_subtitle_smart_erase",
        "去字幕",
        "",
    ),
)


def _tool_label(tid: str) -> str:
    """展示名；懒加载计费表，避免本模块 import 拉起 FastAPI/SQLAlchemy。"""
    try:
        from .canvas_tool_pricing import canvas_tool_label

        return canvas_tool_label(tid)
    except Exception:  # noqa: BLE001
        return tid


def _collect_aliases() -> list[tuple[str, str]]:
    """官方名优先；计费中文名与去掉「九宫格·」前缀的子名一并收录。同名不覆盖已有映射。"""
    seen: dict[str, str] = {}
    ordered: list[tuple[str, str]] = []

    def add(name: str, tool_id: str) -> None:
        key = str(name or "").strip()
        tid = str(tool_id or "").strip()
        if not key or not tid:
            return
        if any(frag in key for frag in _SKIP_LABEL_FRAGMENTS):
            return
        if key in seen:
            return
        seen[key] = tid
        ordered.append((key, tid))

    for name, tid in _UI_NAME_TO_TOOL:
        add(name, tid)
    try:
        from .canvas_tool_pricing import CANVAS_TOOL_CREDIT_DEFS
    except Exception:  # noqa: BLE001
        CANVAS_TOOL_CREDIT_DEFS = ()  # type: ignore[assignment]
    for tid, label, _group in CANVAS_TOOL_CREDIT_DEFS:
        add(label, tid)
        if label.startswith("九宫格·"):
            add(label[len("九宫格·") :], tid)
    for tid, _label, _group in CANVAS_TOOL_CREDIT_DEFS:
        add(tid, tid)
    extra_ids = ("panorama_720", "visual_style", "storyboard_batch_videos")
    for tid in extra_ids:
        add(tid, tid)
    return ordered


_ALIASES: list[tuple[str, str]] = _collect_aliases()
# 最长名优先，避免「调度故事板」命中「故事板」、「多机位九宫格」命中「九宫格」
_ALIASES_BY_LEN: list[tuple[str, str]] = sorted(_ALIASES, key=lambda p: len(p[0]), reverse=True)


def match_named_canvas_tools(text: str) -> list[tuple[str, str]]:
    """从用户原文做最长、不重叠匹配。返回 [(命中原文, tool_id), ...] 按出现顺序。"""
    raw = str(text or "")
    if not raw.strip():
        return []
    occupied: list[tuple[int, int]] = []
    hits: list[tuple[int, str, str]] = []
    for name, tid in _ALIASES_BY_LEN:
        start = 0
        while True:
            idx = raw.find(name, start)
            if idx < 0:
                break
            end = idx + len(name)
            if any(idx < b and end > a for a, b in occupied):
                start = idx + 1
                continue
            occupied.append((idx, end))
            hits.append((idx, name, tid))
            break
    hits.sort(key=lambda x: x[0])
    # 同一 tool 只保留第一次点名
    seen_ids: set[str] = set()
    out: list[tuple[str, str]] = []
    for _pos, name, tid in hits:
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        out.append((name, tid))
    return out


def match_soft_effect_tools(text: str) -> list[tuple[str, str, str]]:
    """官方名未命中时的高置信口语。返回 [(展示语, tool_id, params_hint)]。"""
    raw = str(text or "")
    if not raw.strip():
        return []
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for pattern, tid, label, hint in _SOFT_EFFECT_RULES:
        if tid in seen:
            continue
        if pattern.search(raw):
            seen.add(tid)
            out.append((label, tid, hint))
    return out


def format_named_tools_hint(text: str) -> str:
    """写入本轮 user 块：点名了就必须用这些 id；否则尝试高置信口语推断。"""
    hits = match_named_canvas_tools(text)
    if hits:
        lines = [
            "【本轮点名工具 · 必须 run_canvas_tool 用下列 id，禁止换成相近工具或 generate_node 顶替】"
        ]
        for name, tid in hits:
            shown = _tool_label(tid)
            lines.append(f"- 用户说了「{name}」→ `tool={tid}`（{shown}）")
        return "\n".join(lines)

    soft = match_soft_effect_tools(text)
    if not soft:
        return ""
    lines = [
        "【本轮推断工具 · 高置信口语，请直接 run_canvas_tool，勿再 ask_user 要官方名】"
    ]
    for label, tid, hint in soft:
        shown = _tool_label(tid)
        extra = f"；{hint}" if hint else ""
        lines.append(f"- 「{label}」→ `tool={tid}`（{shown}）{extra}")
    return "\n".join(lines)


# 不在顶栏对照表里、但 run_canvas_tool 仍支持的展示名
_EXTRA_OFFICIAL_NAMES: dict[str, str] = {
    "visual_style": "套画风",
    "storyboard_batch_videos": "按表批量出视频",
}


def official_name_for_tool(tool_id: str) -> str:
    """tool id → 画布按钮官方中文名（取对照表第一条）。"""
    tid = str(tool_id or "").strip()
    if not tid:
        return ""
    extra = _EXTRA_OFFICIAL_NAMES.get(tid)
    if extra:
        return extra
    for name, mapped in _UI_NAME_TO_TOOL:
        if mapped == tid:
            return name
    return _tool_label(tid)


def format_official_tool_name_table() -> str:
    """官方中文名对照表，供 list_canvas_tools / 规则附录。"""
    lines = ["官方名（与画布按钮一致）→ tool id。用户必须说这些名字，不要用近义猜测。"]
    for name, tid in _UI_NAME_TO_TOOL:
        lines.append(f"- 「{name}」→ `{tid}`")
    return "\n".join(lines)
