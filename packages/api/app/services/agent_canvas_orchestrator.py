"""画布黑箱编排服务（档 2 · 完整拆分）。

架构（对齐 LibTV「传话 + 后端编排」心智，保留本系统 canvasOps / 算力轨）：

```
用户原话
  → 传话层 agent_followup.run_session_followup
       （只收集快照 / Skill / 视觉 URL，禁止润色用户文案）
  → 本服务 orchestrate_canvas_plan
       （LLM plan 或模板兜底 + 原话透传 + genOpts + 宣传片确认门）
  → 传话层 _apply_plan_to_graph → canvasOps 投影
```

硬约束：
- 不改会话协议、Project Graph、前端 canvasOps 契约、对话轨 S / 媒体轨 G
- 宣传片 `awaiting_generate_confirm`：本服务强制 `generate=[]`（真正开跑由
  `agent_sessions` 短路 `runCinematicBatch`，不进本编排）
- Team / wizard（爆款/出海）主路径本阶段不大改
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from ..integrations.llm.chat import chat_completion
from ..services.agent_canvas_manual import followup_system_prompt
from ..services.agent_vision_refs import (
    build_controller_user_content,
    pick_vision_controller_model,
)

logger = logging.getLogger(__name__)

# 与 agent_generate_intent.allows_video_generate 对齐（勿单独维护两套正则）
from .agent_generate_intent import allows_video_generate as user_confirmed_video_generate

# 复杂管线：不做「原话强制写进 prompt」（交给分镜/Skill）
_COMPLEX_PIPELINE_RE = re.compile(
    r"分镜表|一系列视频|系列视频|多镜|短片成片|批量出视频|整集|拉片|拆镜|"
    r"出海|爆款|宣传片|故事板|调度故事板|确认生成|"
    r"读板|合成板已就绪|分配视频镜头",
    re.I,
)

_WANT_GENERATE_RE = re.compile(
    r"生成|出图|出视频|画一张|画一只|画个|做一张|做一个视频|跑起来|开始生成|全部生成",
    re.I,
)
_BUILD_ONLY_RE = re.compile(
    r"先别生成|先不生成|只要搭|先搭节点|先铺节点|别生成",
    re.I,
)

# 用户要「故事板合成图」工具（非分镜表 storyboard_grid）
_STORYBOARD_SHEET_RE = re.compile(r"调度故事板|故事板", re.I)
_STORYBOARD_GRID_ONLY_RE = re.compile(r"分镜表", re.I)
# 出板后续聊 / 读板：禁止再触发「新建锚点 + 再出一张板」
_STORYBOARD_KICK_RE = re.compile(
    r"合成板已就绪|读板分配镜头|请仔细用视觉阅读|按格分配合理视频镜头|"
    r"故事板/调度故事板已生成|已生成.{0,8}故事板|"
    r"这是最新画布|根据用户原目标继续",
    re.I,
)
_STORYBOARD_REGEN_RE = re.compile(
    r"再生成故事板|重新生成故事板|再出一张故事板|再做一张故事板|"
    r"换一张故事板|重做故事板|再画一张故事板",
    re.I,
)


@dataclass(frozen=True)
class CanvasOrchestrateInput:
    """传话层投递给黑箱的不可变入参。

    `user_text` 必须是用户原话（可含附件/节点标记），调用方禁止先润色拆解。
    """

    user_text: str
    graph: dict[str, Any]
    canvas_snapshot: dict[str, Any] | None = None
    preferred_model: str | None = None
    my_skill: bool = False
    platform_skill: bool = False
    skill_context: str | None = None
    image_urls: list[str] | None = None
    extra_rules_markdown: str | None = None
    cinematic_phase: str | None = None


@dataclass
class CanvasOrchestrateResult:
    """黑箱输出：结构化 plan + 来源标记（便于观测）。"""

    plan: dict[str, Any]
    source: Literal["llm", "template", "gated"]
    gates_applied: list[str]


def _augment_skill_context(
    skill_context: str | None,
    cinematic_phase: str | None,
) -> str | None:
    """把宣传片阶段机约束并进 Skill 块，供编排 LLM 阅读。"""
    parts: list[str] = []
    base = (skill_context or "").strip()
    if base:
        parts.append(base)
    phase = (cinematic_phase or "").strip()
    if phase:
        parts.append(f"当前阶段 cinematicPhase={phase}")
        if phase == "awaiting_generate_confirm":
            parts.append(
                "【强制·黑箱确认门】未确认时禁止 generate / storyboard_batch_videos；"
                "勿再改拓扑；引导用户回复「确认生成」。"
                "若用户本轮已说确认短语：允许 generate，禁止再追问确认。"
            )
        elif phase == "auto_pipeline":
            parts.append(
                "【画布感知】无合成图才出故事板；已有板则复用并按用户目标搭节点；"
                "视频 generate=[] 等用户「确认生成」；禁止定妆图（productAssetRole=sheet）与分镜表。"
            )
        elif phase == "generating_videos":
            parts.append("视频批量生成中；勿重复建节点或 generate。")
    if not parts:
        return None
    return "\n".join(parts)


def _strip_generate_ops(plan: dict[str, Any]) -> bool:
    """清空扣费生成；返回是否改动过。"""
    changed = False
    if plan.get("generate"):
        plan["generate"] = []
        changed = True
    tools = plan.get("runCanvasTools")
    if isinstance(tools, list) and tools:
        filtered = [
            t
            for t in tools
            if not (
                isinstance(t, dict)
                and str(t.get("tool") or "").strip()
                in ("storyboard_batch_videos", "storyboard_video")
            )
        ]
        if len(filtered) != len(tools):
            plan["runCanvasTools"] = filtered
            changed = True
    return changed


def apply_cinematic_confirm_gate(
    plan: dict[str, Any],
    *,
    cinematic_phase: str | None,
    user_text: str,
    canvas_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """宣传片确认门（服务端强制，不依赖模型自觉）。

    awaiting 阶段：用户本轮已说确认短语则放行 generate（由传话层清 phase / 出片）；
    未确认则剥 generate 并提示回复「确认生成」。
    """
    out = dict(plan or {})
    gates: list[str] = []
    phase = (cinematic_phase or "").strip()
    text = (user_text or "").strip()

    if phase == "awaiting_generate_confirm":
        # 用户已确认：放行，勿再剥 generate、勿再追问；缺 generate 时服务端补交
        if user_confirmed_video_generate(text):
            gates.append("cinematic_awaiting_user_confirmed")
            gen_list = out.get("generate") if isinstance(out.get("generate"), list) else []
            if not gen_list:
                from .agent_generate_intent import _idle_snapshot_video_ids

                idle = _idle_snapshot_video_ids(canvas_snapshot)
                if idle:
                    out["generate"] = [{"nodeId": idle[0]}]
                    gates.append("cinematic_confirm_inject_generate")
            return out, gates
        if _strip_generate_ops(out):
            gates.append("cinematic_awaiting_strip_generate")
        reply = str(out.get("reply") or "").strip()
        if "确认生成" not in reply and "开始生成" not in reply:
            out["reply"] = (
                (reply + "\n\n" if reply else "")
                + "视频镜头已就绪。是否现在开始生成？"
                "可回复「确认生成 / 开始吧 / 需要生成 / 开始生成」"
                "（此前不扣视频算力）。"
            )
            gates.append("cinematic_awaiting_nudge_reply")
        return out, gates

    if phase == "auto_pipeline":
        if _strip_generate_ops(out):
            gates.append("cinematic_auto_pipeline_strip_generate")
        return out, gates

    if phase == "generating_videos":
        if _strip_generate_ops(out):
            gates.append("cinematic_generating_strip_generate")
        for key in (
            "addImageNodes",
            "addVideoNodes",
            "addTextNodes",
            "addStoryboardNodes",
            "connect",
            "runCanvasTools",
        ):
            if out.get(key):
                out[key] = [] if isinstance(out.get(key), list) else out.get(key)
                gates.append(f"cinematic_generating_clear_{key}")
        return out, gates

    return out, gates


# user_confirmed_video_generate：见顶部 import（agent_generate_intent.allows_video_generate）

def _collect_video_node_ids(
    plan: dict[str, Any],
    canvas_snapshot: dict[str, Any] | None,
) -> set[str]:
    """本轮可能被 generate 命中的视频节点 id（快照已有 + 本轮新建）。"""
    ids: set[str] = set()
    if isinstance(canvas_snapshot, dict):
        nodes = canvas_snapshot.get("nodes")
        if isinstance(nodes, list):
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                if str(n.get("type") or "") == "video_input" and n.get("id"):
                    ids.add(str(n["id"]))
    add_v = plan.get("addVideoNodes")
    if isinstance(add_v, list):
        for it in add_v:
            if not isinstance(it, dict):
                continue
            tid = str(it.get("tempId") or it.get("nodeId") or "").strip()
            if tid:
                ids.add(tid)
    return ids


_VIDEO_GENERATE_TOOLS = frozenset({"storyboard_batch_videos"})


def _strip_video_generate_ops(
    plan: dict[str, Any],
    video_ids: set[str],
) -> bool:
    """剥视频 generate 与批量出视频工具；保留图片 generate / 故事板工具。"""
    changed = False
    gen = plan.get("generate")
    if isinstance(gen, list) and gen:
        kept: list[Any] = []
        for g in gen:
            if isinstance(g, dict):
                nid = str(g.get("nodeId") or g.get("tempId") or "").strip()
            else:
                nid = str(g or "").strip()
            if nid and nid in video_ids:
                changed = True
                continue
            kept.append(g)
        if changed:
            plan["generate"] = kept
    tools = plan.get("runCanvasTools")
    if isinstance(tools, list) and tools:
        filtered = [
            t
            for t in tools
            if not (
                isinstance(t, dict)
                and str(t.get("tool") or "").strip() in _VIDEO_GENERATE_TOOLS
            )
        ]
        if len(filtered) != len(tools):
            plan["runCanvasTools"] = filtered
            changed = True
    return changed


def apply_video_confirm_rail(
    plan: dict[str, Any],
    *,
    user_text: str,
    cinematic_phase: str | None,
    canvas_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """未「确认生成」则剥视频 generate / 批量出视频；图片与故事板工具保留。

    cinematic 有 phase 时由 apply_cinematic_confirm_gate 处理，本护栏跳过以免双重改写。
    """
    if (cinematic_phase or "").strip():
        return plan, []
    if user_confirmed_video_generate(user_text):
        return plan, []
    out = dict(plan or {})
    gates: list[str] = []
    video_ids = _collect_video_node_ids(out, canvas_snapshot)
    if _strip_video_generate_ops(out, video_ids):
        gates.append("video_confirm_strip_generate")
        reply = str(out.get("reply") or "").strip()
        if "确认生成" not in reply:
            out["reply"] = (
                (reply + "\n\n" if reply else "")
                + "视频镜头可先搭在画布上。要出片请回复「确认生成」（此前不扣视频算力）。"
            )
            gates.append("video_confirm_nudge_reply")
    return out, gates


def _prompt_for_passthrough(user_text: str) -> str:
    """清洗附件/节点标记外壳，保留用户创作原意作 prompt。"""
    t = (user_text or "").strip()
    # 去掉方括号标记外壳，保留中间自然语言
    t = re.sub(r"\[附件:[^\]]*\]", " ", t)
    t = re.sub(r"\[节点:[^\]]*\]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:2000] if t else (user_text or "").strip()[:2000]


def _new_temp_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def snapshot_has_completed_storyboard(snapshot: dict[str, Any] | None) -> bool:
    """画布快照是否已有带媒体的故事板/调度板合成图。"""
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
        has_media = bool(n.get("hasMedia") or n.get("assetId") or n.get("url") or n.get("imageUrl"))
        is_board = role == "storyboard_sheet" or any(
            k in label for k in ("故事板", "调度故事板")
        )
        if is_board and has_media:
            return True
    return False


def wants_storyboard_regen(user_text: str) -> bool:
    """用户明确要重做一张合成板（覆盖复用）。"""
    return bool(_STORYBOARD_REGEN_RE.search(user_text or ""))


def should_skip_storyboard_sheet_gate(
    user_text: str,
    *,
    canvas_snapshot: dict[str, Any] | None = None,
    cinematic_phase: str | None = None,
) -> bool:
    """已有合成图或读板续聊时，禁止再强制出板。

    ``cinematic_phase`` 为 awaiting/generating 时同样跳过（确认门阶段勿再出板）。
    auto_pipeline 且尚无合成图时不跳过注入（交由本闸门或 LLM 出第一张板）。
    """
    phase = (cinematic_phase or "").strip()
    if phase in ("awaiting_generate_confirm", "generating_videos"):
        if wants_storyboard_regen(user_text or ""):
            return False
        return True
    text = (user_text or "").strip()
    if not text:
        return True
    if wants_storyboard_regen(text):
        return False
    if _STORYBOARD_KICK_RE.search(text):
        return True
    if snapshot_has_completed_storyboard(canvas_snapshot):
        return True
    return False


def _strip_repeat_storyboard_ops(plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """剥掉本轮再出板：合成图工具 + 新「故事板锚点」，保留读板建 video。"""
    out = dict(plan or {})
    gates: list[str] = []
    tools = list(out.get("runCanvasTools") or []) if isinstance(out.get("runCanvasTools"), list) else []
    kept_tools = [
        t
        for t in tools
        if not (
            isinstance(t, dict)
            and str(t.get("tool") or "").strip() in ("storyboard", "blocking_storyboard")
        )
    ]
    if len(kept_tools) != len(tools):
        out["runCanvasTools"] = kept_tools
        gates.append("storyboard_sheet_reuse_strip_tool")
    add_imgs = list(out.get("addImageNodes") or []) if isinstance(out.get("addImageNodes"), list) else []
    kept_imgs = []
    for it in add_imgs:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "")
        if any(k in label for k in ("故事板锚点", "调度故事板锚点")) or (
            "锚点" in label and "故事板" in label
        ):
            gates.append("storyboard_sheet_reuse_strip_anchor")
            continue
        kept_imgs.append(it)
    if len(kept_imgs) != len(add_imgs):
        out["addImageNodes"] = kept_imgs
    return out, gates


def _strip_sheet_only_extras(plan: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """只要一张故事板：清空视频节点 / 出片，且最多保留一个出板工具与一个锚点。"""
    out = dict(plan or {})
    gates: list[str] = []
    if out.get("addVideoNodes"):
        out["addVideoNodes"] = []
        gates.append("storyboard_sheet_only_strip_videos")
    gens = out.get("generate")
    if isinstance(gens, list) and gens:
        out["generate"] = []
        gates.append("storyboard_sheet_only_strip_generate")
    tools = list(out.get("runCanvasTools") or []) if isinstance(out.get("runCanvasTools"), list) else []
    kept_tools: list[Any] = []
    sheet_kept = 0
    for t in tools:
        if not isinstance(t, dict):
            continue
        tool = str(t.get("tool") or "").strip()
        if tool in ("storyboard", "blocking_storyboard"):
            if sheet_kept >= 1:
                gates.append("storyboard_sheet_only_cap_tool")
                continue
            sheet_kept += 1
            kept_tools.append(t)
            continue
        if tool in ("storyboard_batch_videos", "storyboard_from_video"):
            gates.append("storyboard_sheet_only_strip_tool")
            continue
        kept_tools.append(t)
    if len(kept_tools) != len(tools):
        out["runCanvasTools"] = kept_tools
    add_imgs = list(out.get("addImageNodes") or []) if isinstance(out.get("addImageNodes"), list) else []
    if len(add_imgs) > 1:
        # 优先保留带「故事板」标签的锚点
        preferred = [
            it
            for it in add_imgs
            if isinstance(it, dict) and "故事板" in str(it.get("label") or "")
        ]
        out["addImageNodes"] = (preferred[:1] or add_imgs[:1])
        gates.append("storyboard_sheet_only_cap_anchor")
    return out, gates


def apply_storyboard_sheet_gate(
    plan: dict[str, Any],
    *,
    user_text: str,
    cinematic_phase: str | None,
    canvas_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """护栏：用户要故事板且快照没有合成图时，补 storyboard 工具（防空锚点冒充）。

    已有合成图：剥再出板；「只要一张板」时还剥 addVideoNodes。
    """
    out = dict(plan or {})
    gates: list[str] = []
    text = (user_text or "").strip()
    if not text:
        return out, gates

    from .agent_generate_intent import is_storyboard_sheet_only

    sheet_only = is_storyboard_sheet_only(text)
    if sheet_only:
        out, only_gates = _strip_sheet_only_extras(out)
        gates.extend(only_gates)

    # 已有板 / 读板续聊 / 确认等待：剥再出板
    if should_skip_storyboard_sheet_gate(
        text, canvas_snapshot=canvas_snapshot, cinematic_phase=cinematic_phase
    ):
        out, skip_gates = _strip_repeat_storyboard_ops(out)
        if skip_gates:
            gates.extend(skip_gates)
            gates.append("storyboard_sheet_reuse")
        if sheet_only:
            out, only_gates = _strip_sheet_only_extras(out)
            gates.extend(only_gates)
        return out, gates
    # 宣传片自动管线尚未出板：不在此注入（避免与 Skill 抢流程），也不剥 LLM 出板
    if (cinematic_phase or "").strip():
        return out, gates
    if not _STORYBOARD_SHEET_RE.search(text):
        return out, gates
    if _STORYBOARD_GRID_ONLY_RE.search(text) and "故事板" not in text and "调度故事板" not in text:
        return out, gates

    tool_name = "blocking_storyboard" if "调度故事板" in text else "storyboard"
    prompt = _prompt_for_passthrough(text) or text

    tools = list(out.get("runCanvasTools") or []) if isinstance(out.get("runCanvasTools"), list) else []
    has_sheet_tool = any(
        isinstance(t, dict)
        and str(t.get("tool") or "").strip() in ("storyboard", "blocking_storyboard")
        for t in tools
    )

    add_imgs = list(out.get("addImageNodes") or []) if isinstance(out.get("addImageNodes"), list) else []
    anchor_tid = ""
    for it in add_imgs:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "")
        tid = str(it.get("tempId") or it.get("nodeId") or "").strip()
        if tid and any(k in label for k in ("故事板", "调度", "锚点")):
            anchor_tid = tid
            if not str(it.get("prompt") or "").strip():
                it["prompt"] = prompt
            break
    if not anchor_tid and add_imgs:
        first = add_imgs[0]
        if isinstance(first, dict):
            anchor_tid = str(first.get("tempId") or first.get("nodeId") or "").strip()
            if anchor_tid:
                first["label"] = str(first.get("label") or "故事板锚点")
                if not str(first.get("prompt") or "").strip():
                    first["prompt"] = prompt
                gates.append("storyboard_sheet_relabel_anchor")

    if not anchor_tid:
        anchor_tid = _new_temp_id("board_anchor")
        add_imgs.append(
            {
                "tempId": anchor_tid,
                "label": "故事板锚点",
                "prompt": prompt,
                "x": 420,
                "y": 120,
            }
        )
        out["addImageNodes"] = add_imgs
        gates.append("storyboard_sheet_add_anchor")
    else:
        out["addImageNodes"] = add_imgs

    if not has_sheet_tool:
        tools.append(
            {
                "tool": tool_name,
                "nodeId": anchor_tid,
                "nodeName": "故事板锚点",
                "params": {"userPrompt": prompt},
            }
        )
        out["runCanvasTools"] = tools
        gates.append("storyboard_sheet_inject_tool")
    else:
        new_tools: list[Any] = []
        for t in tools:
            if not isinstance(t, dict):
                new_tools.append(t)
                continue
            item = dict(t)
            tn = str(item.get("tool") or "").strip()
            if tn in ("storyboard", "blocking_storyboard"):
                params = (
                    dict(item.get("params") or {})
                    if isinstance(item.get("params"), dict)
                    else {}
                )
                if not str(params.get("userPrompt") or params.get("prompt") or "").strip():
                    params["userPrompt"] = prompt
                    item["params"] = params
                    gates.append("storyboard_sheet_fill_user_prompt")
                if not str(item.get("nodeId") or "").strip():
                    item["nodeId"] = anchor_tid
                    gates.append("storyboard_sheet_bind_anchor")
            new_tools.append(item)
        out["runCanvasTools"] = new_tools

    # 禁止对空锚点普通 generate（那是文生图，不是故事板工具）
    gen = list(out.get("generate") or []) if isinstance(out.get("generate"), list) else []
    if gen:
        filtered: list[Any] = []
        for g in gen:
            if isinstance(g, dict):
                nid = str(g.get("nodeId") or g.get("tempId") or "").strip()
            else:
                nid = str(g or "").strip()
            if nid == anchor_tid:
                gates.append("storyboard_sheet_strip_anchor_generate")
                continue
            filtered.append(g)
        out["generate"] = filtered

    # 只要一张板：再剥一次视频（LLM 可能与注入锚点同轮带了 video）
    if sheet_only:
        out, only_gates = _strip_sheet_only_extras(out)
        gates.extend(only_gates)
    if not str(out.get("intent") or "").strip() or out.get("intent") == "chat":
        out["intent"] = "tool"
    return out, gates


def apply_utterance_passthrough_gate(
    plan: dict[str, Any],
    *,
    user_text: str,
    cinematic_phase: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """简单出图/出视频：强制新建节点 prompt=用户原话，并补同轮 generate。

    复杂管线（分镜/宣传片/系列）与「先别生成」不强制。
    """
    out = dict(plan or {})
    gates: list[str] = []
    text = (user_text or "").strip()
    if not text or (cinematic_phase or "").strip():
        return out, gates
    if _COMPLEX_PIPELINE_RE.search(text):
        return out, gates

    prompt = _prompt_for_passthrough(text)
    if not prompt:
        return out, gates

    build_only = bool(_BUILD_ONLY_RE.search(text))
    want_gen = bool(_WANT_GENERATE_RE.search(text)) and not build_only

    # 新建图/视频节点：若模型把 prompt 扩写过长或跑题，压回原话
    for key in ("addImageNodes", "addVideoNodes"):
        items = out.get(key)
        if not isinstance(items, list) or not items:
            continue
        new_items: list[Any] = []
        changed = False
        for it in items:
            if not isinstance(it, dict):
                new_items.append(it)
                continue
            item = dict(it)
            cur = str(item.get("prompt") or "").strip()
            # 原话很短时模型常扩写；原话较长时若差异大也覆盖
            if (not cur) or (len(cur) > max(80, len(prompt) * 2)) or (prompt not in cur and len(prompt) <= 120):
                item["prompt"] = prompt
                changed = True
            new_items.append(item)
        if changed:
            out[key] = new_items
            gates.append(f"passthrough_{key}_prompt")

    if build_only:
        if _strip_generate_ops(out):
            gates.append("passthrough_build_only_strip_generate")
        return out, gates

    if want_gen:
        gen = list(out.get("generate") or []) if isinstance(out.get("generate"), list) else []
        existing: set[str] = set()
        for g in gen:
            if isinstance(g, dict):
                nid = str(g.get("nodeId") or g.get("tempId") or "").strip()
            else:
                nid = str(g or "").strip()
            if nid:
                existing.add(nid)
        added = 0
        for key in ("addImageNodes", "addVideoNodes"):
            for it in out.get(key) or []:
                if not isinstance(it, dict):
                    continue
                tid = str(it.get("tempId") or it.get("nodeId") or "").strip()
                if tid and tid not in existing:
                    gen.append({"nodeId": tid})
                    existing.add(tid)
                    added += 1
        if added:
            out["generate"] = gen
            gates.append("passthrough_ensure_generate")
            # 意图落空时补 image/video
            if not out.get("intent") or out.get("intent") == "chat":
                if out.get("addVideoNodes"):
                    out["intent"] = "video"
                elif out.get("addImageNodes"):
                    out["intent"] = "image"

    return out, gates


async def compose_followup_plan(
    user_text: str,
    graph: dict[str, Any],
    *,
    preferred_model: str | None = None,
    canvas_snapshot: dict[str, Any] | None = None,
    my_skill: bool = False,
    platform_skill: bool = False,
    skill_context: str | None = None,
    image_urls: list[str] | None = None,
    extra_rules_markdown: str | None = None,
) -> tuple[dict[str, Any], Literal["llm", "template"]]:
    """黑箱内部：调控制器 LLM 产出 plan；失败则模板兜底。

    返回 (plan, source)。LLM helpers 仍复用 agent_followup 的摘要/解析函数。
    """
    from .agent_followup import (
        _graph_summary,
        _model_catalog_block,
        _parse_json_obj,
        _snapshot_summary,
        _template_followup,
    )

    model = pick_vision_controller_model(
        preferred_model, has_images=bool(image_urls)
    )
    if not model:
        return _template_followup(user_text, graph, canvas_snapshot), "template"
    try:
        skill_block = ""
        if (skill_context or "").strip():
            # 与 agent_context_budget 技能附录对齐，禁止旁路整包 28KB
            from .agent_context_budget import BUDGET_SKILL_APPENDIX, clip_budget

            skill_block = (
                "Skill 规格（必须遵守）：\n"
                f"{clip_budget(skill_context.strip(), BUDGET_SKILL_APPENDIX, label='技能')}\n\n"
            )
        user_text_block = (
            f"{skill_block}"
            f"当前 Graph 摘要：\n{_graph_summary(graph)}\n\n"
            f"当前画布快照：\n{_snapshot_summary(canvas_snapshot)}\n\n"
            f"{_model_catalog_block()}\n\n"
            "【用户原话 · 传话直达黑箱 · 勿润色拆解 · 直接编排】\n"
            f"{user_text}"
        )
        user_content = build_controller_user_content(
            user_text_block, model_id=model, image_urls=image_urls
        )
        content = await chat_completion(
            model,
            [
                {
                    "role": "system",
                    "content": followup_system_prompt(
                        include_my_skill_appendix=my_skill,
                        include_platform_skill_appendix=platform_skill
                        or bool(skill_block and not my_skill),
                        extra_rules_markdown=extra_rules_markdown,
                    ),
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            temperature=0.55,
            max_tokens=6144,
            timeout_s=120.0,
        )
        parsed = _parse_json_obj(content)
        if not parsed:
            logger.warning("canvas_orchestrator: LLM JSON parse failed, template fallback")
            return _template_followup(user_text, graph, canvas_snapshot), "template"
        return parsed, "llm"
    except Exception as exc:  # noqa: BLE001
        logger.warning("canvas_orchestrator: LLM failed: %s", exc)
        return _template_followup(user_text, graph, canvas_snapshot), "template"


async def orchestrate_canvas_plan(
    inp: CanvasOrchestrateInput,
) -> CanvasOrchestrateResult:
    """黑箱入口：用户原话 + 画布上下文 → JSON plan。

    调用方（传话层）不得改写 `inp.user_text` 后再传入。
    """
    from .agent_followup import (
        _inject_gen_opts_into_plan,
        _parse_gen_opts_from_text,
        _template_followup,
    )

    text = (inp.user_text or "").strip()
    if not text:
        return CanvasOrchestrateResult(
            plan={"intent": "chat", "reply": "请再说一遍你想在画布上做什么。"},
            source="template",
            gates_applied=["empty_utterance"],
        )

    skill_ctx = _augment_skill_context(inp.skill_context, inp.cinematic_phase)

    plan, source = await compose_followup_plan(
        text,
        inp.graph,
        preferred_model=inp.preferred_model,
        canvas_snapshot=inp.canvas_snapshot,
        my_skill=inp.my_skill,
        platform_skill=inp.platform_skill,
        skill_context=skill_ctx,
        image_urls=inp.image_urls,
        extra_rules_markdown=inp.extra_rules_markdown,
    )
    if not isinstance(plan, dict):
        plan = _template_followup(text, inp.graph, inp.canvas_snapshot)
        source = "template"

    plan = _inject_gen_opts_into_plan(
        plan,
        _parse_gen_opts_from_text(text),
        inp.canvas_snapshot,
        user_text=text,
    )

    all_gates: list[str] = []
    plan, g0 = apply_storyboard_sheet_gate(
        plan,
        user_text=text,
        cinematic_phase=inp.cinematic_phase,
        canvas_snapshot=inp.canvas_snapshot,
    )
    all_gates.extend(g0)

    plan, g1 = apply_utterance_passthrough_gate(
        plan,
        user_text=text,
        cinematic_phase=inp.cinematic_phase,
    )
    all_gates.extend(g1)

    plan, g2 = apply_cinematic_confirm_gate(
        plan,
        cinematic_phase=inp.cinematic_phase,
        user_text=text,
        canvas_snapshot=inp.canvas_snapshot,
    )
    all_gates.extend(g2)

    plan, g3 = apply_video_confirm_rail(
        plan,
        user_text=text,
        cinematic_phase=inp.cinematic_phase,
        canvas_snapshot=inp.canvas_snapshot,
    )
    all_gates.extend(g3)

    result_source: Literal["llm", "template", "gated"] = source
    if all_gates and source == "llm":
        result_source = "gated"

    logger.info(
        "canvas_orchestrator: phase=%s source=%s gates=%s intent=%s",
        inp.cinematic_phase or "-",
        result_source,
        all_gates or [],
        plan.get("intent"),
    )
    return CanvasOrchestrateResult(
        plan=plan, source=result_source, gates_applied=all_gates
    )
