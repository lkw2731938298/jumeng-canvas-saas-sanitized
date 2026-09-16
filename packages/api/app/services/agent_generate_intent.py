"""点名节点出片护栏：指定了节点就不要新建；已经生成成功就不要再生成。

与技能包配合：
- 「生成视频」短出片（准星点名）→ 只出一条，禁止再铺板
- 「确认生成 / 做宣传片」配方确认 → 不拦 storyboard_batch_videos / 多镜同批 generate
- 「只要一张故事板」→ 只出一张合成图，禁止搭视频节点、禁止再出第二张板
- 多角度/打光/九宫格等单工具成功 → 收束，禁止续跑再铺点
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_call_name_args(tc: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """解析上游 tool_call → (id, name, args)。本地实现，避免依赖 agent_tools（重依赖）。"""
    tid = str(tc.get("id") or "").strip()
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = str(fn.get("name") or tc.get("name") or "").strip()
    args = _parse_tool_arguments(fn.get("arguments") if fn else tc.get("arguments"))
    return tid, name, args


_NODE_LINE_RE = re.compile(
    r"\[节点:([^\]|]*)\|nodeId=([0-9a-zA-Z_-]{1,128})"
    r"(?:\|type=([0-9a-zA-Z_-]+))?(?:\|assetId=([0-9a-zA-Z_-]{1,128}))?\]"
)
# 「宣传片」不放这里：技能包名/「做宣传片」应走完整配方，不能当成短出片
_VIDEO_INTENT_RE = re.compile(r"视频|出片|出视频|做视频|图生视频")
_GENERATE_INTENT_RE = re.compile(
    r"生成|出片|确认生成|开始做|开始生成|出视频|做视频|再生成|重做|生成同款|开始成片"
)
# 明确「就这一条视频」：才启用点名短出片硬拦
_SHORT_VIDEO_SHOT_RE = re.compile(
    r"生成视频|出视频|做个视频|做视频|图生视频|生成一段视频|出一段视频"
)
# 技能配方确认 / 整段目标（不应短出片硬拦）
# 注意：不要用裸「电影级/出海/复刻」——风格词 ≠ 整段配方；「做一张电影级故事板」仍属只要板
_RECIPE_CONFIRM_RE = re.compile(
    r"确认生成|开始成片|生成同款|开始出视频|做宣传片|电影级宣传片|宣传片|"
    r"拉片复刻|爆款复刻|一键出海|出海本地化|"
    r"催泪短片|第一视角短片|第一视角催泪"
)

# 用户只要「一张故事板合成图」（非读板搭镜头、非出视频）
_STORYBOARD_SHEET_RE = re.compile(r"调度故事板|故事板")
# 明确要板之后的下一步才退出 sheet-only；风格词（电影级/出海风/出片感）不算
_STORYBOARD_WANTS_MORE_RE = re.compile(
    r"确认生成|出视频|生成视频|做视频|做宣传片|宣传片|"
    r"分镜表|一系列视频|短片成片|批量出视频|读板|分配镜头|搭镜头|"
    r"视频节点|开始成片|拉片复刻|爆款复刻|一键出海"
)
_STORYBOARD_REGEN_RE = re.compile(
    r"再生成故事板|重新生成故事板|再出一张故事板|再做一张故事板|"
    r"换一张故事板|重做故事板|再画一张故事板",
    re.I,
)
# 未确认出片时，仍允许「搭空视频节点」的话术（配方布点 / 短出片新建一条）
_ALLOW_VIDEO_LAYOUT_RE = re.compile(
    r"视频|出视频|做视频|图生视频|生成视频|搭镜头|视频节点|读板|分配镜头|"
    r"确认生成|开始成片|批量成片|做宣传片|宣传片|拉片复刻|爆款复刻|一键出海|"
    r"一系列视频|短片成片|批量出视频|电影级宣传片"
)
# 整段配方读板出片：含故事板/读板语义时，「生成视频」不算短出片确认
_CINEMATIC_STORYBOARD_LAYOUT_RE = re.compile(
    r"基于故事板|故事板生成|按故事板|读板|搭镜头|分配镜头|故事板.*生成|生成.*故事板",
    re.I,
)
# 未确认时禁止扣视频算力（generate / batch）；短出片另走 is_short_video_shot
# 与 agent_canvas_orchestrator / agent_sessions 语义对齐；改此处须同步文案与 ask 选项
_CONFIRM_VIDEO_GENERATE_RE = re.compile(
    r"确认生成|生成同款|确认并生成|开始成片|批量成片|"
    r"开始吧|开始生成|需要生成|生成吧|可以生成|现在开始|开始出片|"
    r"同意生成|确认出片|按方案生成|就这样生成|出片吧|"
    r"好的[，,]?开始|可以[，,]?开始|那就生成|就生成吧|"
    r"提交生成|搭线生成|搭线.*生成|执行生成|直接生成|马上生成|立刻生成|"
    r"去生成|你来生成|你来提交|帮我生成|开始出视频|生成视频|"
    r"confirm_generate|start_generate|选项\s+(confirm_generate|start_generate)",
    re.I,
)

# 点名短出片时禁止再铺板 / 批量出片
_LAYOUT_OR_BATCH_TOOLS = frozenset(
    {
        "storyboard",
        "blocking_storyboard",
        "storyboard_sketch",
        "storyboard_table",
        "storyboard_from_video",
        "storyboard_batch_videos",
        "storyboard_subject_image",
        "storyboard_overseas_localize",
    }
)
_BATCH_VIDEO_TOOLS = frozenset({"storyboard_batch_videos"})
_SHEET_TOOLS = frozenset({"storyboard", "blocking_storyboard"})
# 单次编辑类工具：成功后必须收束（禁止续跑再调同工具 / 顺手铺视频）
# 不含分镜管线中段（table/from_video/text_subject…），以免打断多步技能包同轮后续
_ATOMIC_EDIT_TOOLS = frozenset(
    {
        "multi_angle",
        "lighting",
        "cinematic_lighting",
        "grid_9",
        "grid_25",
        "plot_grid_4",
        "multi_cam_grid_9",
        "grid_split",
        "cutout",
        "outpaint",
        "hd_upscale",
        "panorama",
        "panorama_720",
        "portrait_adjust",
        "emotion_adjust",
        "drawing_board_ai",
        "visual_style",
        "face_tri_view",
        "character_sheet",
        "character_tri_view",
        "scene_sheet",
        "product_sheet",
        "frame_forward_3s",
        "frame_back_5s",
        "hd_upscale_video",
        "video_smart_matting",
        "video_subject_remove",
        "video_subject_edit",
        "video_subject_replace",
        "video_subtitle_smart_erase",
        "video_subtitle_box_erase",
        "vocal_separate",
        "vocal_remove",
    }
)
# 用户点名「只要这一下」时可收束的管线单步（有「然后/出片/确认」则不收束）
_SINGLE_SHOT_PIPELINE_TOOLS = frozenset(
    {
        "storyboard_table",
        "storyboard_from_image",
        "storyboard_from_video",
        "storyboard_overseas_localize",
        "text_subject",
        "storyboard_camera",
        "storyboard_video",
        "storyboard_sketch",
        "storyboard_subject_image",
    }
)
_STOP_AFTER_TOOL_IDS = _ATOMIC_EDIT_TOOLS | _SINGLE_SHOT_PIPELINE_TOOLS | _SHEET_TOOLS

# 只要出图、不要视频/故事板配方
_IMAGE_ONLY_RE = re.compile(
    r"出一张图|生成图片|画一张|做一张图|文生图|生一张|生个图|出张图|画张图|做张图|"
    r"做个海报|做张海报|画个海报|做个封面|画个封面|做张封面|画张封面"
)
# 点名工具成功后仍要续跑的衔接词
_TOOL_CHAIN_CONTINUE_RE = re.compile(
    r"然后|再出|接着|并出|再做|之后|下一步|再生成|再搭|读板|搭镜头|"
    r"确认生成|开始成片|批量出视频|出视频|生成视频|做宣传片|宣传片|"
    r"开始吧|开始生成|需要生成|生成吧|可以生成"
)

# 前端投影 generate_node 成功时的 result 文案
_GENERATE_OK_MARKERS = ("已提交生成", "已生成")
# 故事板工具成功：前端 record 为「工具 storyboard 已执行」
_SHEET_OK_MARKERS = (
    "工具 storyboard 已执行",
    "工具 blocking_storyboard 已执行",
    "storyboard 已执行",
    "blocking_storyboard 已执行",
)

# 视频出片执行层硬闸：无确认短语时禁止投影，并回传给模型（对齐 Codex approval）
VIDEO_CONFIRM_NEEDED_TEXT = (
    "硬闸：未确认出片。须先 ask_user 问用户是否开始生成；"
    "收到「确认生成 / 提交生成 / 搭线生成 / 开始吧 / 开始生成 / 需要生成 / 开始成片」"
    "或点击「确认生成」选项后再 generate。"
    "禁止只复述计划不调用 generate_node。"
)
VIDEO_CONFIRM_ASK_PROMPT = (
    "按故事板出视频会消耗算力。是否现在开始生成？"
    "可回复「确认生成 / 开始吧 / 需要生成」，或点下方选项。"
)
VIDEO_CONFIRM_ASK_OPTIONS: list[dict[str, str]] = [
    {"id": "confirm_generate", "label": "确认生成"},
    {"id": "start_generate", "label": "开始生成"},
    {"id": "not_now", "label": "先不出片"},
]


def _assistant_visible_content(raw: dict[str, Any] | None, assistant_text: str) -> str:
    """inflight / 续跑用可见正文；优先已剥离思考链的 assistant_text。"""
    text = (assistant_text or "").strip()
    if text:
        return text
    if not isinstance(raw, dict):
        return ""
    content = raw.get("content")
    if isinstance(content, str):
        return content.strip()
    return ""


def assistant_message_keeping_tool_calls(
    raw: dict[str, Any] | None,
    tool_calls: list[dict[str, Any]],
    assistant_text: str,
) -> dict[str, Any]:
    """续跑时 assistant.tool_calls 必须与即将回传的 tool 结果对齐。"""
    return {
        "role": "assistant",
        "content": _assistant_visible_content(raw, assistant_text),
        "tool_calls": tool_calls,
    }


def assistant_message_for_client_calls(
    raw: dict[str, Any] | None,
    client_calls: list[tuple[str, str, dict[str, Any]]],
    assistant_text: str,
) -> dict[str, Any]:
    """inflight 的 assistant.tool_calls 必须与实际下发的 canvasOps 一致，否则续跑对不齐。"""
    tool_calls: list[dict[str, Any]] = []
    for cid, name, args in client_calls:
        tool_calls.append(
            {
                "id": cid,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        )
    return assistant_message_keeping_tool_calls(raw, tool_calls, assistant_text)


GENERATE_DONE_TEXT = (
    "已按你的要求提交生成。成片会出现在画布节点上；要再生成或改提示词，直接说即可。"
)

STORYBOARD_DONE_TEXT = (
    "故事板已生成并落到画布。若要按板搭视频镜头或出片，再说一声即可。"
)

ATOMIC_DONE_TEXT = (
    "已按你的要求执行画布工具。若要继续改、搭镜头或出片，直接说即可。"
)

CONTINUE_PLACEHOLDER = "（画布工具已执行，请根据最新快照继续）"


def wants_video(text: str) -> bool:
    """用户本轮是否在要视频（不是再生一张图）。"""
    return bool(_VIDEO_INTENT_RE.search(text or ""))


def wants_generate(text: str) -> bool:
    """用户本轮是否明确要求生成/出片。"""
    return bool(_GENERATE_INTENT_RE.search(text or ""))


def is_short_video_shot(text: str) -> bool:
    """准星短出片：「生成视频」类，不是「确认生成整段配方」。"""
    t = text or ""
    # 故事板读板出片须先 ask 再确认短语，不能把「基于故事板生成视频」当短出片
    if _CINEMATIC_STORYBOARD_LAYOUT_RE.search(t):
        return False
    if _RECIPE_CONFIRM_RE.search(t) and not _SHORT_VIDEO_SHOT_RE.search(t):
        return False
    return bool(_SHORT_VIDEO_SHOT_RE.search(t))


def is_recipe_confirm(text: str) -> bool:
    """整段技能配方 / 宣传片确认（非短出片、非只要一张板）。"""
    return bool(_RECIPE_CONFIRM_RE.search(text or ""))


def is_storyboard_sheet_only(text: str) -> bool:
    """用户只要一张故事板合成图：不出视频、不读板搭镜头。

    「5s」等时长口癖、以及「电影级/出海风/出片感」等风格词单独出现不算要视频。
    """
    t = text or ""
    if not _STORYBOARD_SHEET_RE.search(t):
        return False
    if _STORYBOARD_WANTS_MORE_RE.search(t):
        return False
    if _STORYBOARD_REGEN_RE.search(t):
        # 重做板仍属「只要板」
        return True
    return True


def allows_video_layout(text: str) -> bool:
    """本轮是否允许新建空 video_input（配方布点或明确要视频）。"""
    t = text or ""
    if is_storyboard_sheet_only(t):
        return False
    if is_image_only_request(t):
        return False
    return bool(_ALLOW_VIDEO_LAYOUT_RE.search(t))


def allows_video_generate(text: str) -> bool:
    """本轮是否允许对视频节点 generate / batch（扣算力）。"""
    t = text or ""
    if _CONFIRM_VIDEO_GENERATE_RE.search(t):
        return True
    if is_short_video_shot(t):
        return True
    if is_image_only_request(t) or is_storyboard_sheet_only(t):
        return False
    # 用户口语「生成视频 / 搭线生成…」且不是要「只生成故事板」→ 视为确认出片
    if wants_generate(t) and wants_video(t):
        return True
    return False


def is_image_only_request(text: str) -> bool:
    """用户只要出图（海报/封面/生一张），不要搭视频或整段配方。"""
    t = text or ""
    if not _IMAGE_ONLY_RE.search(t):
        return False
    if is_recipe_confirm(t):
        return False
    if _ALLOW_VIDEO_LAYOUT_RE.search(t):
        return False
    if _STORYBOARD_SHEET_RE.search(t):
        return False
    return True


def wants_tool_chain_continue(text: str) -> bool:
    """点名工具后是否还要接着做下一步（然后出片 / 确认生成等）。"""
    t = text or ""
    if is_recipe_confirm(t):
        return True
    if allows_video_generate(t):
        return True
    return bool(_TOOL_CHAIN_CONTINUE_RE.search(t))


def evolving_use_deep_thinking(
    user_text: str,
    *,
    chat_only: bool = False,
    has_skill: bool = False,
) -> bool:
    """Evolving 深路径：开思考、加大 max_tokens。复用已有意图函数，不另写 NLP。

    快路径：点名/推断原子工具、出图-only、只要一张故事板、纯对话。
    深路径：绑定技能包，或宣传片/出海/爆款/多节点编排。
    """
    if chat_only:
        return False
    if has_skill:
        return True
    if is_recipe_confirm(user_text):
        return True
    if is_storyboard_sheet_only(user_text):
        return False
    if is_image_only_request(user_text):
        return False
    named = user_requested_tool_ids(user_text)
    if named and not wants_tool_chain_continue(user_text):
        return False
    return True


def user_requested_tool_ids(text: str) -> set[str]:
    """本轮用户点名或高置信口语推断的工具 id。"""
    try:
        from .agent_canvas_tool_names import (
            match_named_canvas_tools,
            match_soft_effect_tools,
        )
    except Exception:  # noqa: BLE001
        return set()
    ids = {tid for _name, tid in match_named_canvas_tools(text)}
    ids |= {tid for _label, tid, _hint in match_soft_effect_tools(text)}
    return ids


def wants_storyboard_regen(text: str) -> bool:
    """用户明确要重做一张合成板。"""
    return bool(_STORYBOARD_REGEN_RE.search(text or ""))


def snapshot_has_completed_storyboard(snapshot: dict[str, Any] | None) -> bool:
    """画布是否已有带媒体的故事板/调度板合成图。"""
    if not isinstance(snapshot, dict):
        return False
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list):
        return False
    for n in nodes:
        if not isinstance(n, dict):
            continue
        label = str(n.get("label") or "")
        role = str(n.get("role") or "")
        has_media = bool(
            n.get("hasMedia") or n.get("assetId") or n.get("url") or n.get("imageUrl")
        )
        is_board = role == "storyboard_sheet" or any(
            k in label for k in ("故事板", "调度故事板")
        )
        if is_board and has_media:
            return True
    return False


def parse_named_nodes(text: str) -> list[dict[str, str]]:
    """解析消息里的准星引用 `[节点:…|nodeId=…]`。"""
    out: list[dict[str, str]] = []
    for m in _NODE_LINE_RE.finditer(text or ""):
        nid = (m.group(2) or "").strip()
        if not nid:
            continue
        out.append(
            {
                "name": (m.group(1) or "").strip(),
                "id": nid,
                "type": (m.group(3) or "").strip(),
                "assetId": (m.group(4) or "").strip(),
            }
        )
    return out


def focused_nodes_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, str]]:
    """快照里 focused=1 的节点（仅调试/兼容；护栏不用）。"""
    if not isinstance(snapshot, dict):
        return []
    raw = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    out: list[dict[str, str]] = []
    for n in raw:
        if not isinstance(n, dict) or not n.get("focused"):
            continue
        nid = str(n.get("id") or "").strip()
        if not nid:
            continue
        out.append(
            {
                "name": str(n.get("label") or "").strip(),
                "id": nid,
                "type": str(n.get("type") or "").strip(),
                "assetId": str(n.get("assetId") or "").strip(),
            }
        )
    return out


def enrich_named_types(
    named: list[dict[str, str]], snapshot: dict[str, Any] | None
) -> list[dict[str, str]]:
    """准星缺 type / type=unknown 时，用画布快照补全。"""
    nodes = _nodes_by_id(snapshot)
    out: list[dict[str, str]] = []
    for n in named:
        item = dict(n)
        nid = str(item.get("id") or "").strip()
        ntype = str(item.get("type") or "").strip()
        if (not ntype or ntype in ("?", "unknown")) and nid in nodes:
            item["type"] = str(nodes[nid].get("type") or "").strip()
            if not item.get("assetId"):
                item["assetId"] = str(nodes[nid].get("assetId") or "").strip()
            if not item.get("name"):
                item["name"] = str(nodes[nid].get("label") or "").strip()
        out.append(item)
    return out


def specified_nodes(
    text: str, snapshot: dict[str, Any] | None = None
) -> list[dict[str, str]]:
    """本轮用户**准星点名**的节点。

    只用消息里的 `[节点:…|nodeId=…]`，不用画布选中 focused——
    选中产品图做技能包时不应被当成「点名了就禁止新建」。
    """
    named = parse_named_nodes(text)
    if not named:
        return []
    return enrich_named_types(named, snapshot)


def node_has_media(snapshot: dict[str, Any] | None, node_id: str, named_asset: str = "") -> bool:
    """节点是否已有媒体（参考片/已出片）。"""
    if named_asset:
        return True
    row = _nodes_by_id(snapshot).get((node_id or "").strip()) or {}
    return bool(row.get("hasMedia") or str(row.get("assetId") or "").strip())


def named_idle_video_ids(
    named: list[dict[str, str]], snapshot: dict[str, Any] | None
) -> list[str]:
    """点名的、尚未出片的 video_input（可安全 generate）。"""
    out: list[str] = []
    for n in named:
        if n.get("type") != "video_input":
            continue
        nid = str(n.get("id") or "").strip()
        if not nid:
            continue
        if node_has_media(snapshot, nid, n.get("assetId") or ""):
            continue
        out.append(nid)
    return out


def named_only_reference_media(
    named: list[dict[str, str]], snapshot: dict[str, Any] | None
) -> bool:
    """准星全是已有媒体的参考图/参考片 → 更像带参考做配方，不是「生成这个空节点」。"""
    if not named:
        return False
    for n in named:
        ntype = str(n.get("type") or "")
        nid = str(n.get("id") or "")
        if ntype not in ("image_input", "video_input"):
            return False
        if not node_has_media(snapshot, nid, n.get("assetId") or ""):
            return False
    return True


def last_user_intent_text(rows: list[Any], current_message: str) -> str:
    """取本轮真实用户原话（续跑占位句不算）。"""
    cur = (current_message or "").strip()
    if cur and cur != CONTINUE_PLACEHOLDER:
        return cur
    for msg in reversed(list(rows or [])):
        role = str(getattr(msg, "role", "") or "")
        if role != "user":
            continue
        content = str(getattr(msg, "content", "") or "").strip()
        if content and content != CONTINUE_PLACEHOLDER:
            return content
    return cur


def _nodes_by_id(snapshot: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return {}
    raw = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for n in raw:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip()
        if nid:
            out[nid] = n
    return out


def _edges(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    raw = snapshot.get("edges") if isinstance(snapshot.get("edges"), list) else []
    return [e for e in raw if isinstance(e, dict)]


def linked_video_ids(snapshot: dict[str, Any] | None, source_id: str) -> list[str]:
    """从指定节点连出的 video_input 下游。"""
    src = (source_id or "").strip()
    if not src:
        return []
    nodes = _nodes_by_id(snapshot)
    out: list[str] = []
    seen: set[str] = set()
    for e in _edges(snapshot):
        if str(e.get("source") or "").strip() != src:
            continue
        tid = str(e.get("target") or "").strip()
        if not tid or tid in seen:
            continue
        ntype = str((nodes.get(tid) or {}).get("type") or "").strip()
        if ntype == "video_input":
            seen.add(tid)
            out.append(tid)
    return out


def _prefer_idle_video_ids(
    ids: list[str], snapshot: dict[str, Any] | None, *, limit: int
) -> list[str]:
    """优先选没有媒体、未在 running 的视频节点。"""
    nodes = _nodes_by_id(snapshot)
    idle: list[str] = []
    busy: list[str] = []
    for nid in ids:
        n = nodes.get(nid) or {}
        status = str(n.get("status") or "").strip().lower()
        if status == "running":
            busy.append(nid)
            continue
        if n.get("hasMedia"):
            busy.append(nid)
            continue
        idle.append(nid)
    ordered = idle + [x for x in busy if x not in idle]
    return ordered[: max(0, int(limit))]


def resolve_video_generate_targets(
    named: list[dict[str, str]],
    snapshot: dict[str, Any] | None,
    *,
    short_shot: bool = True,
) -> tuple[list[str], bool]:
    """点名节点后，视频应对准哪些已有 nodeId；以及是否允许新建一个视频节点。

    short_shot=True：只出一条（用户说生成视频）。
    short_shot=False：点名了多个空视频时可全部生成；参考片不作为目标。
    """
    named_video = [n for n in named if n.get("type") == "video_input" and n.get("id")]
    idle_named = named_idle_video_ids(named, snapshot)
    if idle_named:
        if short_shot:
            return idle_named[:1], False
        return idle_named, False
    # 点名的是已有媒体的参考视频：短出片才允许对它再生成；配方确认则不要当目标
    if named_video and short_shot:
        return [str(n["id"]) for n in named_video], False

    named_image = [n for n in named if n.get("type") == "image_input" and n.get("id")]
    linked: list[str] = []
    for img in named_image:
        linked.extend(linked_video_ids(snapshot, str(img["id"])))
    uniq: list[str] = []
    seen: set[str] = set()
    for nid in linked:
        if nid in seen:
            continue
        seen.add(nid)
        uniq.append(nid)
    if uniq:
        limit = 1 if short_shot else max(1, len(uniq))
        return _prefer_idle_video_ids(uniq, snapshot, limit=limit), False
    if named_image and short_shot:
        return [], True
    return [], False


def successful_generate_in_tool_results(
    inflight: dict[str, Any] | None,
    tool_results: list[dict[str, Any]] | None,
) -> bool:
    """前端回传里是否已有成功的 generate_node / 批量出视频。"""
    items = [x for x in (tool_results or []) if isinstance(x, dict)]
    ok_ids: set[str] = set()
    text_hit = False
    for item in items:
        if not bool(item.get("ok", True)):
            continue
        cid = str(item.get("toolCallId") or item.get("id") or "").strip()
        if cid:
            ok_ids.add(cid)
        result = str(item.get("result") or "")
        if any(m in result for m in _GENERATE_OK_MARKERS):
            text_hit = True

    assistant = (inflight or {}).get("assistant") if isinstance(inflight, dict) else None
    tcs = assistant.get("tool_calls") if isinstance(assistant, dict) else None
    gen_ids: set[str] = set()
    if isinstance(tcs, list):
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            cid, name, args = _tool_call_name_args(tc)
            if name == "generate_node":
                gen_ids.add(cid)
                continue
            if name == "run_canvas_tool":
                tool = str((args or {}).get("tool") or "").strip()
                if tool in _BATCH_VIDEO_TOOLS:
                    gen_ids.add(cid)

    if gen_ids:
        return bool(gen_ids & ok_ids) or (text_hit and bool(ok_ids))
    return text_hit


def successful_sheet_tool_in_tool_results(
    inflight: dict[str, Any] | None,
    tool_results: list[dict[str, Any]] | None,
) -> bool:
    """前端回传里是否已有成功的故事板 / 调度故事板工具。"""
    items = [x for x in (tool_results or []) if isinstance(x, dict)]
    ok_ids: set[str] = set()
    text_hit = False
    for item in items:
        if not bool(item.get("ok", True)):
            continue
        cid = str(item.get("toolCallId") or item.get("id") or "").strip()
        if cid:
            ok_ids.add(cid)
        result = str(item.get("result") or "")
        if any(m in result for m in _SHEET_OK_MARKERS):
            text_hit = True
        # 宽松：工具名 + 已执行
        if ("storyboard" in result or "blocking_storyboard" in result) and (
            "已执行" in result or "已生成" in result
        ):
            text_hit = True

    assistant = (inflight or {}).get("assistant") if isinstance(inflight, dict) else None
    tcs = assistant.get("tool_calls") if isinstance(assistant, dict) else None
    sheet_ids: set[str] = set()
    if isinstance(tcs, list):
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            cid, name, args = _tool_call_name_args(tc)
            if name != "run_canvas_tool":
                continue
            tool = str((args or {}).get("tool") or "").strip()
            if tool in _SHEET_TOOLS:
                sheet_ids.add(cid)

    if sheet_ids:
        return bool(sheet_ids & ok_ids) or (text_hit and bool(ok_ids))
    return text_hit


def _ok_tool_call_ids(
    tool_results: list[dict[str, Any]] | None,
) -> tuple[set[str], list[str]]:
    """回传里成功的 toolCallId，以及成功文案列表。"""
    items = [x for x in (tool_results or []) if isinstance(x, dict)]
    ok_ids: set[str] = set()
    texts: list[str] = []
    for item in items:
        if not bool(item.get("ok", True)):
            continue
        cid = str(item.get("toolCallId") or item.get("id") or "").strip()
        if cid:
            ok_ids.add(cid)
        result = str(item.get("result") or "")
        if result:
            texts.append(result)
    return ok_ids, texts


def successful_run_tools_in_tool_results(
    inflight: dict[str, Any] | None,
    tool_results: list[dict[str, Any]] | None,
    *,
    allow_tools: set[str] | frozenset[str],
) -> set[str]:
    """本轮成功落地、且属于 allow_tools 的 run_canvas_tool id。"""
    ok_ids, texts = _ok_tool_call_ids(tool_results)
    found: set[str] = set()
    allow = set(allow_tools)
    assistant = (inflight or {}).get("assistant") if isinstance(inflight, dict) else None
    tcs = assistant.get("tool_calls") if isinstance(assistant, dict) else None
    if isinstance(tcs, list):
        for tc in tcs:
            if not isinstance(tc, dict):
                continue
            cid, name, args = _tool_call_name_args(tc)
            if name != "run_canvas_tool":
                continue
            tool = str((args or {}).get("tool") or "").strip()
            if tool not in allow:
                continue
            if cid and cid in ok_ids:
                found.add(tool)
            elif (not cid) and any(
                (f"工具 {tool} 已执行" in t) or (f"{tool} 已执行" in t) for t in texts
            ):
                found.add(tool)
    if not found:
        for t in texts:
            for tool in allow:
                if f"工具 {tool} 已执行" in t or f"{tool} 已执行" in t:
                    found.add(tool)
    return found


def successful_atomic_tools_in_tool_results(
    inflight: dict[str, Any] | None,
    tool_results: list[dict[str, Any]] | None,
) -> set[str]:
    """本轮成功落地的单次编辑类工具 id 集合。"""
    return successful_run_tools_in_tool_results(
        inflight, tool_results, allow_tools=_ATOMIC_EDIT_TOOLS
    )


def should_stop_after_tool_results(
    *,
    user_text: str,
    inflight: dict[str, Any] | None,
    tool_results: list[dict[str, Any]] | None,
    snapshot: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """续跑前：本轮是否已完成用户目标、应收束。

    返回 (should_stop, done_text)。
    """
    if successful_generate_in_tool_results(inflight, tool_results):
        return True, GENERATE_DONE_TEXT
    if is_storyboard_sheet_only(user_text):
        # 只要一张板：工具成功 或 快照已有带图合成板 → 收束（禁再铺点/搭视频）
        if successful_sheet_tool_in_tool_results(inflight, tool_results):
            return True, STORYBOARD_DONE_TEXT
        if snapshot_has_completed_storyboard(snapshot) and not wants_storyboard_regen(
            user_text
        ):
            return True, STORYBOARD_DONE_TEXT
        return False, ""
    # 多角度 / 打光 / 九宫格 / 抠图等：成功即收束
    if successful_atomic_tools_in_tool_results(inflight, tool_results):
        return True, ATOMIC_DONE_TEXT
    # 点名/推断的单步工具（分镜表、拉片、设定图等）：无「然后出片」时收束
    if not wants_tool_chain_continue(user_text):
        stop_ids = user_requested_tool_ids(user_text) & _STOP_AFTER_TOOL_IDS
        if stop_ids:
            succeeded = successful_run_tools_in_tool_results(
                inflight, tool_results, allow_tools=stop_ids
            )
            if succeeded:
                return True, ATOMIC_DONE_TEXT
    return False, ""


def _filter_already_generated(
    client_calls: list[tuple[str, str, dict[str, Any]]],
) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    for cid, name, args in client_calls:
        if name == "generate_node":
            continue
        if name == "add_node" and str((args or {}).get("type") or "") in (
            "video_input",
            "image_input",
        ):
            continue
        if name == "run_canvas_tool":
            tool = str((args or {}).get("tool") or "").strip()
            if (
                tool in _LAYOUT_OR_BATCH_TOOLS
                or tool in _BATCH_VIDEO_TOOLS
                or tool in _ATOMIC_EDIT_TOOLS
                or tool in _SINGLE_SHOT_PIPELINE_TOOLS
                or tool in _SHEET_TOOLS
            ):
                continue
        out.append((cid, name, dict(args or {})))
    return out


def _idle_snapshot_video_ids(snapshot: dict[str, Any] | None) -> list[str]:
    """画布上尚无成片的 video_input，按 node id 排序。"""
    ids = sorted(_snapshot_video_ids(snapshot))
    return [vid for vid in ids if not node_has_media(snapshot, vid)]


def inject_confirmed_video_generate_calls(
    client_calls: list[tuple[str, str, dict[str, Any]]],
    *,
    snapshot: dict[str, Any] | None,
    user_text: str,
) -> list[tuple[str, str, dict[str, Any]]]:
    """用户已确认出片但模型未调 generate_node 时，服务端补交第一个待生成视频。"""
    if not allows_video_generate(user_text):
        return client_calls
    if any(name == "generate_node" for _, name, _ in client_calls):
        return client_calls
    idle = _idle_snapshot_video_ids(snapshot)
    if not idle:
        return client_calls
    out = list(client_calls)
    out.append(("inject_confirmed_generate", "generate_node", {"nodeId": idle[0]}))
    return out


def _snapshot_video_ids(snapshot: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    if not isinstance(snapshot, dict):
        return ids
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list):
        return ids
    for n in nodes:
        if isinstance(n, dict) and str(n.get("type") or "") == "video_input" and n.get("id"):
            ids.add(str(n["id"]))
    return ids


def _filter_image_only_ops(
    client_calls: list[tuple[str, str, dict[str, Any]]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """只要出图：禁止视频节点、故事板/分镜管线、批量出片。"""
    out: list[tuple[str, str, dict[str, Any]]] = []
    image_adds = 0
    for cid, name, args in client_calls:
        args = dict(args or {})
        if name == "add_node":
            ntype = str(args.get("type") or "")
            if ntype == "video_input":
                continue
            if ntype == "image_input":
                if image_adds >= 1:
                    continue
                image_adds += 1
            out.append((cid, name, args))
            continue
        if name == "run_canvas_tool":
            tool = str(args.get("tool") or "").strip()
            if (
                tool in _LAYOUT_OR_BATCH_TOOLS
                or tool in _BATCH_VIDEO_TOOLS
                or tool in _SHEET_TOOLS
                or tool in _SINGLE_SHOT_PIPELINE_TOOLS
            ):
                continue
            out.append((cid, name, args))
            continue
        out.append((cid, name, args))
    return out


def _filter_unconfirmed_video_ops(
    client_calls: list[tuple[str, str, dict[str, Any]]],
    *,
    user_text: str,
    snapshot: dict[str, Any] | None,
) -> tuple[list[tuple[str, str, dict[str, Any]]], list[tuple[str, str]]]:
    """未说要视频时剥空视频节点；未确认出片时剥视频 generate / batch。

    返回 (允许投影的调用, 硬闸拦截的 (toolCallId, 反馈文案))。
    拦截项不进 canvasOps，由 runtime 回传模型或强制 ask_user。
    """
    allow_layout = allows_video_layout(user_text)
    allow_gen = allows_video_generate(user_text)
    if allow_layout and allow_gen:
        return (
            [(cid, name, dict(args or {})) for cid, name, args in client_calls],
            [],
        )

    video_ids = _snapshot_video_ids(snapshot)
    # 本批即将新建的 video tempId，也视为视频目标
    for _cid, name, args in client_calls:
        if name == "add_node" and str((args or {}).get("type") or "") == "video_input":
            tid = str((args or {}).get("tempId") or (args or {}).get("id") or "").strip()
            if tid:
                video_ids.add(tid)

    out: list[tuple[str, str, dict[str, Any]]] = []
    blocked: list[tuple[str, str]] = []
    for cid, name, args in client_calls:
        args = dict(args or {})
        if name == "add_node" and str(args.get("type") or "") == "video_input":
            if not allow_layout:
                continue
            out.append((cid, name, args))
            continue
        if name == "run_canvas_tool":
            tool = str(args.get("tool") or "").strip()
            if tool in _BATCH_VIDEO_TOOLS and not allow_gen:
                blocked.append((cid, VIDEO_CONFIRM_NEEDED_TEXT))
                continue
            out.append((cid, name, args))
            continue
        if name == "generate_node" and not allow_gen:
            nid = str(args.get("nodeId") or "").strip()
            if nid in video_ids:
                blocked.append((cid, VIDEO_CONFIRM_NEEDED_TEXT))
                continue
            # 无 nodeId 时保守：若本轮在搭视频且不允许出片，硬闸拦截裸 generate
            if (not nid) and allow_layout and not allow_gen:
                blocked.append((cid, VIDEO_CONFIRM_NEEDED_TEXT))
                continue
            out.append((cid, name, args))
            continue
        out.append((cid, name, args))
    return out, blocked


def _filter_storyboard_sheet_only(
    client_calls: list[tuple[str, str, dict[str, Any]]],
    *,
    snapshot: dict[str, Any] | None,
    user_text: str,
) -> list[tuple[str, str, dict[str, Any]]]:
    """只要一张故事板：最多一个出板工具 + 一个图片锚点；禁止一切视频节点。"""
    already_has = (
        snapshot_has_completed_storyboard(snapshot)
        and not wants_storyboard_regen(user_text)
    )
    out: list[tuple[str, str, dict[str, Any]]] = []
    sheet_count = 0
    image_add_count = 0
    for cid, name, args in client_calls:
        args = dict(args or {})
        if name == "add_node":
            ntype = str(args.get("type") or "").strip()
            if ntype == "video_input":
                continue
            if ntype == "image_input":
                if already_has:
                    continue
                if image_add_count >= 1:
                    continue
                image_add_count += 1
                out.append((cid, name, args))
                continue
            # 其它类型（文本等）也先丢掉，避免顺手铺剧本
            continue
        if name == "generate_node":
            continue
        if name == "connect_nodes":
            # 只要板时不连视频
            continue
        if name == "run_canvas_tool":
            tool = str(args.get("tool") or "").strip()
            if tool in _SHEET_TOOLS:
                if already_has:
                    continue
                if sheet_count >= 1:
                    continue
                sheet_count += 1
                out.append((cid, name, args))
                continue
            # 其它画布工具一律丢掉
            continue
        # ask_user / 只读工具不在 client_calls 里
        out.append((cid, name, args))
    return out


def _filter_short_video_shot(
    client_calls: list[tuple[str, str, dict[str, Any]]],
    *,
    user_text: str,
    snapshot: dict[str, Any] | None,
    named: list[dict[str, str]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """准星 + 生成视频：只出一条，禁止铺板/批量。"""
    targets, allow_one_new = resolve_video_generate_targets(
        named, snapshot, short_shot=True
    )
    max_gen = max(1, len(targets)) if targets else 1
    out: list[tuple[str, str, dict[str, Any]]] = []
    added_video = 0
    generated_ids: set[str] = set()

    for cid, name, args in client_calls:
        args = dict(args or {})
        if name == "add_node":
            ntype = str(args.get("type") or "")
            if ntype == "video_input":
                if targets:
                    continue
                if allow_one_new and added_video < 1:
                    added_video += 1
                    out.append((cid, name, args))
                continue
            if ntype in ("image_input", "storyboard_grid"):
                continue
            out.append((cid, name, args))
            continue
        if name == "run_canvas_tool":
            tool = str(args.get("tool") or "").strip()
            if tool in _LAYOUT_OR_BATCH_TOOLS:
                continue
            out.append((cid, name, args))
            continue
        if name == "generate_node":
            nid = str(args.get("nodeId") or "").strip()
            if targets:
                if nid not in targets:
                    nid = targets[0]
                    args["nodeId"] = nid
                if nid in generated_ids or len(generated_ids) >= max_gen:
                    continue
                generated_ids.add(nid)
            else:
                if len(generated_ids) >= 1:
                    continue
                generated_ids.add(nid or f"new-{len(generated_ids)}")
            out.append((cid, name, args))
            continue
        out.append((cid, name, args))

    has_gen = any(name == "generate_node" for _, name, _ in out)
    if (not has_gen) and targets and wants_generate(user_text):
        out.append(
            (
                "call_intent_gen_named",
                "generate_node",
                {"nodeId": targets[0]},
            )
        )
    return out


def _filter_named_confirm_soft(
    client_calls: list[tuple[str, str, dict[str, Any]]],
    *,
    snapshot: dict[str, Any] | None,
    named: list[dict[str, str]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """配方确认 + 准星：不拦批量成片；不要对已有媒体的参考片误 generate。"""
    if named_only_reference_media(named, snapshot):
        # 准星只是带参考：放行配方工具；丢掉对参考片本身的 generate_node
        ref_ids = {str(n.get("id") or "") for n in named if n.get("id")}
        out: list[tuple[str, str, dict[str, Any]]] = []
        for cid, name, args in client_calls:
            args = dict(args or {})
            if name == "generate_node":
                nid = str(args.get("nodeId") or "").strip()
                if nid in ref_ids and node_has_media(snapshot, nid):
                    continue
            out.append((cid, name, args))
        return out

    idle = named_idle_video_ids(named, snapshot)
    if not idle:
        return [(cid, name, dict(args or {})) for cid, name, args in client_calls]

    # 点名了空视频镜头：generate 只对准这些；仍允许 batch 工具
    out = []
    generated_ids: set[str] = set()
    for cid, name, args in client_calls:
        args = dict(args or {})
        if name == "generate_node":
            nid = str(args.get("nodeId") or "").strip()
            if nid not in idle:
                nid = idle[0]
                args["nodeId"] = nid
            if nid in generated_ids:
                continue
            generated_ids.add(nid)
            out.append((cid, name, args))
            continue
        out.append((cid, name, args))
    return out


@dataclass
class ClientIntentFilterResult:
    """写画布意图过滤结果：允许投影的调用 + 视频出片硬闸拦截反馈。"""

    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    # (toolCallId, 给模型的硬闸说明)；不进 canvasOps
    video_gate_blocks: list[tuple[str, str]] = field(default_factory=list)


def apply_client_intent_filter(
    client_calls: list[tuple[str, str, dict[str, Any]]],
    *,
    user_text: str,
    snapshot: dict[str, Any] | None,
    named: list[dict[str, str]],
    already_generated: bool,
) -> ClientIntentFilterResult:
    """丢掉误伤调用；未确认视频 generate/batch 记入 video_gate_blocks（执行层硬闸）。"""
    if already_generated:
        return ClientIntentFilterResult(calls=_filter_already_generated(client_calls))

    # 只要一张故事板：不依赖准星；禁视频、最多一张板
    if is_storyboard_sheet_only(user_text):
        return ClientIntentFilterResult(
            calls=_filter_storyboard_sheet_only(
                client_calls, snapshot=snapshot, user_text=user_text
            )
        )

    # 已有板且未说重做：剥重复出板（允许继续搭视频，留给配方）
    if (
        snapshot_has_completed_storyboard(snapshot)
        and not wants_storyboard_regen(user_text)
        and _STORYBOARD_SHEET_RE.search(user_text or "")
    ):
        trimmed: list[tuple[str, str, dict[str, Any]]] = []
        for cid, name, args in client_calls:
            args = dict(args or {})
            if name == "run_canvas_tool":
                tool = str(args.get("tool") or "").strip()
                if tool in _SHEET_TOOLS:
                    continue
            if name == "add_node":
                label = str(args.get("label") or "")
                if "故事板" in label and "锚点" in label:
                    continue
            trimmed.append((cid, name, args))
        client_calls = trimmed

    if named:
        # 准星 +「生成视频」→ 硬短出片
        if is_short_video_shot(user_text):
            return ClientIntentFilterResult(
                calls=_filter_short_video_shot(
                    client_calls,
                    user_text=user_text,
                    snapshot=snapshot,
                    named=named,
                )
            )

        # 准星 + 确认生成 / 其它生成 → 软处理（保技能包批量）
        if wants_generate(user_text):
            client_calls = _filter_named_confirm_soft(
                client_calls, snapshot=snapshot, named=named
            )

    # 只要出图（海报/封面/生一张）：禁视频与分镜管线
    if is_image_only_request(user_text):
        client_calls = _filter_image_only_ops(client_calls)

    # 未明确要视频时剥空视频节点；未确认出片时硬闸拦截视频 generate / batch
    allowed, blocked = _filter_unconfirmed_video_ops(
        client_calls, user_text=user_text, snapshot=snapshot
    )
    if allows_video_generate(user_text):
        allowed = inject_confirmed_video_generate_calls(
            allowed, snapshot=snapshot, user_text=user_text
        )
    return ClientIntentFilterResult(calls=allowed, video_gate_blocks=blocked)


def filter_client_calls_for_intent(
    client_calls: list[tuple[str, str, dict[str, Any]]],
    *,
    user_text: str,
    snapshot: dict[str, Any] | None,
    named: list[dict[str, str]],
    already_generated: bool,
) -> list[tuple[str, str, dict[str, Any]]]:
    """丢掉「点名短出片还新建 / 已经生成还再生成 / 只要板却搭视频 / 未确认却出视频」。"""
    return apply_client_intent_filter(
        client_calls,
        user_text=user_text,
        snapshot=snapshot,
        named=named,
        already_generated=already_generated,
    ).calls
