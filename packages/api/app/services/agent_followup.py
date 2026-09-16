"""编排完成后的续聊：解析用户指令 → 更新 Project Graph → 投影 canvasOps。

支持加节点 / 连线 / 客户端触发生成（轨 G 由前端 runOneGeneratableNode 预扣）。
不重新预扣轨 S。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.llm.chat import chat_completion
from ..models.agent_session import AgentSession
from ..models.user import User
from ..services import agent_sessions, project_graphs
from ..services.agent_controller import get_session_controller_model, pick_controller_model
from ..services.agent_default_models import (
    AGENT_DEFAULT_IMAGE_T2I,
    agent_image_node_params_for_snapshot,
    agent_video_node_params_for_snapshot,
    preferred_models_from_snapshot,
)
from ..services.agent_vision_refs import (
    collect_vision_image_urls,
)

logger = logging.getLogger(__name__)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _model_catalog_block() -> str:
    from .agent_node_models import agent_switchable_models_prompt_block

    return agent_switchable_models_prompt_block()


def _strip_json_fence(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_json_obj(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_strip_json_fence(text))
        return data if isinstance(data, dict) else None
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text or "")
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except Exception:
            return None


def _graph_summary(graph: dict[str, Any]) -> str:
    """给 LLM 的紧凑 Graph 上下文。"""
    script = graph.get("script") if isinstance(graph.get("script"), dict) else {}
    style = graph.get("style") if isinstance(graph.get("style"), dict) else {}
    shots = graph.get("shots") if isinstance(graph.get("shots"), list) else []
    chars = graph.get("characters") if isinstance(graph.get("characters"), list) else []
    scenes = graph.get("scenes") if isinstance(graph.get("scenes"), list) else []
    audio = graph.get("audio") if isinstance(graph.get("audio"), dict) else {}
    lines = [
        f"标题：{script.get('title') or ''}",
        f"风格：{style.get('labels') or []} / {style.get('aspectRatio') or ''} / styleId={style.get('styleId')}",
        f"角色数：{len(chars)}；场景数：{len(scenes)}；镜头数：{len(shots)}",
    ]
    for i, sh in enumerate(shots[:12]):
        if isinstance(sh, dict):
            lines.append(
                f"  镜{i+1}[shotId={sh.get('id')}, canvasNodeId={sh.get('canvasNodeId')}]："
                f"{sh.get('action') or sh.get('imagePrompt') or ''}"
            )
    if audio:
        bgm = audio.get("bgm") if isinstance(audio.get("bgm"), dict) else {}
        lines.append(f"BGM：{bgm.get('prompt') or bgm.get('mood') or '无'}")
    return "\n".join(lines)


def _snapshot_missing_text() -> str:
    return (
        "（无画布快照——勿假设画布为空而大拆重建；"
        "请用户在画布页再发一句，前端会带上最新节点）"
    )


def _snapshot_catalog(snapshot: dict[str, Any] | None) -> str:
    """给 LLM 的画布目录（id+名称成对，不含 prompt/正文）。

    正文只在焦点块或 inspect_node；目录尽量列出快照里已有的全部节点/边。
    """
    if not isinstance(snapshot, dict):
        return _snapshot_missing_text()
    from .agent_tool_catalog import (
        format_node_model_bit,
        format_preferred_models_line,
        node_catalog_capability,
    )

    nodes_raw = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    edges = snapshot.get("edges") if isinstance(snapshot.get("edges"), list) else []
    # 焦点节点优先：前端已排序，此处再稳一次
    nodes_sorted: list[dict[str, Any]] = []
    nodes_rest: list[dict[str, Any]] = []
    for n in nodes_raw:
        if not isinstance(n, dict):
            continue
        if n.get("focused"):
            nodes_sorted.append(n)
        else:
            nodes_rest.append(n)
    nodes = nodes_sorted + nodes_rest

    focused_n = sum(1 for n in nodes if n.get("focused"))
    total_nodes = snapshot.get("nodeCount")
    total_edges = snapshot.get("edgeCount")
    try:
        total_n = int(total_nodes) if total_nodes is not None else len(nodes)
    except (TypeError, ValueError):
        total_n = len(nodes)
    try:
        total_e = int(total_edges) if total_edges is not None else len(edges)
    except (TypeError, ValueError):
        total_e = len(edges)
    truncated = bool(snapshot.get("truncated")) or total_n > len(nodes) or total_e > len(edges)
    omitted_n = snapshot.get("omittedNodeCount")
    omitted_e = snapshot.get("omittedEdgeCount")
    try:
        omit_n = int(omitted_n) if omitted_n is not None else max(0, total_n - len(nodes))
    except (TypeError, ValueError):
        omit_n = max(0, total_n - len(nodes))
    try:
        omit_e = int(omitted_e) if omitted_e is not None else max(0, total_e - len(edges))
    except (TypeError, ValueError):
        omit_e = max(0, total_e - len(edges))
    lines = [
        "【先读快照再动手】已有能用的节点就改、就连；禁止无故新建「故事板锚点」。",
        "节点依据 = nodeId + 节点名称（二者成对；改已有节点必须写目录里的真实 id，并带上名称）",
        f"画布规模：节点 {total_n}，边 {total_e}；本目录列出节点 {len(nodes)}、边 {len(edges)}"
        + (
            f"（已截断：另有约 {omit_n} 个节点、{omit_e} 条边未列出；"
            "未列出的仍存在，改它们前必须 inspect_node，禁止当画布只有目录里这些）"
            if truncated
            else ""
        )
        + (f"；其中焦点 focused={focused_n}（用户选中/引用，优先操作）" if focused_n else ""),
        "字段：x,y=粗位置；type；hasMedia；model(+展示名)；can=可跑的工具/生成；"
        "status；focused=1；shotCount=分镜行数；durationRange=该视频模型合法秒数（改时长必须落在此区间）。"
        "目录不含正文；焦点见【焦点内容】，其余节点改文案/提示词前必须 inspect_node。"
        "选画布工具对照【画布工具速查】（官方名→id→主模型）。",
    ]
    src = str(snapshot.get("source") or "").strip()
    if src == "workflow":
        lines.append(
            "本目录来自最近一次已保存工作流，可能落后于画布未保存改动。"
        )
    elif src == "empty":
        lines.append("当前项目还没有已保存工作流，按空画布理解。")
    pref_line = format_preferred_models_line(snapshot)
    if pref_line:
        lines.append(pref_line)
    id_to_name: dict[str, str] = {}
    for n in nodes:
        nid0 = str(n.get("id") or "").strip()
        if nid0:
            id_to_name[nid0] = str(n.get("label") or "").strip()
    for n in nodes:
        nid = str(n.get("id") or "").strip()
        name = str(n.get("label") or "").strip()
        model_bit = format_node_model_bit(str(n.get("model") or ""))
        asset_id = str(n.get("assetId") or "").strip()
        asset_bit = f" assetId={asset_id}" if asset_id else ""
        label_l = str(n.get("label") or "")
        role = str(n.get("role") or "").strip()
        board_bit = ""
        if role == "storyboard_sheet" or any(
            k in label_l for k in ("故事板", "调度故事板", "storyboard")
        ):
            board_bit = " role=storyboard_sheet"
        shot_n = n.get("shotCount")
        shot_bit = ""
        try:
            if shot_n is not None and int(shot_n) > 0:
                shot_bit = f" shotCount={int(shot_n)}"
        except (TypeError, ValueError):
            shot_bit = ""
        go = n.get("generationOptions")
        opts_bit = ""
        if isinstance(go, dict) and go:
            # 紧凑展示当前「16:9 · 720P · 5s」类选项，便于增量修改
            parts: list[str] = []
            for key in (
                "ratio",
                "aspectRatio",
                "resolution",
                "clarity",
                "duration",
                "realPerson",
            ):
                val = go.get(key)
                if val is not None and str(val).strip():
                    parts.append(f"{key}={val}")
            if not parts:
                for k, v in list(go.items())[:6]:
                    if v is not None and str(v).strip():
                        parts.append(f"{k}={v}")
            if parts:
                opts_bit = " opts={" + ", ".join(parts) + "}"
        # 视频节点附带该模型合法时长，禁止 LLM 写越界秒数（如 H3 写 4s）
        dur_range_bit = ""
        if str(n.get("type") or "") == "video_input":
            from ..core.model_registry import video_duration_limits

            limits = video_duration_limits(str(n.get("model") or "").strip())
            if limits:
                dur_range_bit = f" durationRange={int(limits[0])}-{int(limits[1])}s"
        focused = bool(n.get("focused"))
        focus_bit = " focused=1" if focused else ""
        status = str(n.get("status") or "").strip()
        status_bit = f" status={status}" if status else ""
        err = str(n.get("lastError") or "").strip().replace("\n", " ")
        err_bit = ""
        if err:
            if len(err) > 80:
                err = err[:79] + "…"
            err_bit = f' lastError="{err}"'
        xy_bit = ""
        if n.get("x") is not None or n.get("y") is not None:
            xy_bit = f" x={n.get('x')} y={n.get('y')}"
        can = node_catalog_capability(
            node_type=str(n.get("type") or ""),
            has_media=bool(n.get("hasMedia")),
            role=role,
            label=label_l,
        )
        can_bit = f" can={can}" if can else ""
        lines.append(
            f"  - 【id={nid} | 名称={name}】 type={n.get('type')}"
            f"{xy_bit}{model_bit}{asset_bit}{board_bit}{shot_bit}{opts_bit}{dur_range_bit}{focus_bit}"
            f"{status_bit}{err_bit} hasMedia={bool(n.get('hasMedia'))}{can_bit}"
        )
    # 边：优先展示触及焦点节点的连线，再列其余（前端已按上限截断）
    focus_ids = {str(n.get("id") or "") for n in nodes_sorted if n.get("id")}
    edges_touch: list[dict[str, Any]] = []
    edges_other: list[dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = str(e.get("source") or "")
        tgt = str(e.get("target") or "")
        if src in focus_ids or tgt in focus_ids:
            edges_touch.append(e)
        else:
            edges_other.append(e)
    for e in edges_touch + edges_other:
        src = str(e.get("source") or "")
        tgt = str(e.get("target") or "")
        src_n = id_to_name.get(src, "")
        tgt_n = id_to_name.get(tgt, "")
        lines.append(
            f"  edge 【{src}|{src_n}】:{e.get('sourceHandle') or '?'} → "
            f"【{tgt}|{tgt_n}】:{e.get('targetHandle') or 'ref_in'}"
        )

    # 画布健康诊断（前端 buildCanvasSnapshot 注入）
    failed = snapshot.get("failedNodes") if isinstance(snapshot.get("failedNodes"), list) else []
    broken = snapshot.get("brokenEdges") if isinstance(snapshot.get("brokenEdges"), list) else []
    missing = (
        snapshot.get("missingAssetHints")
        if isinstance(snapshot.get("missingAssetHints"), list)
        else []
    )
    if failed or broken or missing:
        lines.append(
            f"诊断：failed={len(failed)} brokenEdges={len(broken)} missingAsset={len(missing)}"
            "（有诊断时优先 intent=repair 修复）"
        )
        for it in failed[:8]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"  - failed 【id={it.get('id')} | 名称={it.get('label') or ''}】"
                f" reason={it.get('reason') or ''}"
            )
        for it in broken[:6]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"  - broken {it.get('source')}→{it.get('target')} ({it.get('reason')})"
            )
        for it in missing[:8]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"  - missing 【id={it.get('id')} | 名称={it.get('label') or ''}】"
                f" kind={it.get('kind')}"
            )
    return "\n".join(lines)


def _clip_focused_field(raw: Any, limit: int) -> str:
    text = str(raw or "").strip().replace("\n", " ")
    if not text:
        return ""
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


# 焦点短正文：普通节点 1500；文档/长文本 2k 摘要进观察块
_FOCUSED_FIELD_MAX = 1500
_FOCUSED_DOC_MAX = 2000
_DOC_FOCUS_TYPES = frozenset({"document_input", "text_input"})


def _focus_text_limit(ntype: str) -> int:
    return _FOCUSED_DOC_MAX if ntype in _DOC_FOCUS_TYPES else _FOCUSED_FIELD_MAX


def _live_text_excerpt(snapshot: dict[str, Any], node_id: str, limit: int) -> tuple[str, str]:
    """从 liveParams 取焦点文档/长文本摘要（未保存正文优先）。"""
    live_map = snapshot.get("liveParams")
    if not isinstance(live_map, dict):
        return "", ""
    item = live_map.get(node_id)
    if not isinstance(item, dict):
        return "", ""
    return (
        _clip_focused_field(item.get("prompt"), limit),
        _clip_focused_field(item.get("content"), limit),
    )


def _append_focus_line(
    lines: list[str],
    *,
    nid: str,
    name: str,
    ntype: str,
    prompt: str,
    content: str,
    shot_n: Any = None,
    shots: Any = None,
) -> None:
    head = f"- 【id={nid} | 名称={name}】 type={ntype}"
    try:
        if shot_n is not None and int(shot_n) > 0:
            head += f" shotCount={int(shot_n)}"
    except (TypeError, ValueError):
        pass
    lines.append(head)
    if prompt:
        lines.append(f'  prompt="{prompt}"')
    if content:
        lines.append(f'  content="{content}"')
    if isinstance(shots, list) and shots:
        lines.append("  分镜摘要：")
        for row in shots[:8]:
            if not isinstance(row, dict):
                continue
            no = str(row.get("shotNo") or "").strip()
            dur = str(row.get("duration") or "").strip()
            desc = _clip_focused_field(row.get("description"), 160)
            dlg = _clip_focused_field(row.get("dialogue"), 80)
            bits = [b for b in (f"#{no}" if no else "", dur, desc, f"对白={dlg}" if dlg else "") if b]
            if bits:
                lines.append("    - " + " · ".join(bits))


def _focused_content_block(snapshot: dict[str, Any] | None) -> str:
    """焦点节点短正文（选中 / @）；文档与长文本补 live/OSS 摘要（约前 2k）。"""
    if not isinstance(snapshot, dict):
        return ""
    items = snapshot.get("focusedContent")
    lines: list[str] = []
    seen: set[str] = set()
    if isinstance(items, list) and items:
        for it in items:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("id") or "").strip()
            name = str(it.get("label") or "").strip()
            ntype = str(it.get("type") or "").strip()
            limit = _focus_text_limit(ntype)
            prompt = _clip_focused_field(it.get("prompt"), limit)
            content = _clip_focused_field(it.get("content"), limit)
            live_p, live_c = _live_text_excerpt(snapshot, nid, limit)
            if len(live_p) > len(prompt):
                prompt = live_p
            if len(live_c) > len(content):
                content = live_c
            if nid:
                seen.add(nid)
            _append_focus_line(
                lines,
                nid=nid,
                name=name,
                ntype=ntype,
                prompt=prompt,
                content=content,
                shot_n=it.get("shotCount"),
                shots=it.get("shotsPreview"),
            )

    # 焦点文档/长文本若未进 focusedContent，仍从 liveParams 补摘要
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("focused"):
            continue
        ntype = str(n.get("type") or "").strip()
        if ntype not in _DOC_FOCUS_TYPES:
            continue
        nid = str(n.get("id") or "").strip()
        if not nid or nid in seen:
            continue
        limit = _FOCUSED_DOC_MAX
        prompt, content = _live_text_excerpt(snapshot, nid, limit)
        if not prompt:
            prompt = _clip_focused_field(n.get("promptPreview"), limit)
        if not content:
            content = _clip_focused_field(n.get("textPreview"), limit)
        if not prompt and not content:
            continue
        seen.add(nid)
        _append_focus_line(
            lines,
            nid=nid,
            name=str(n.get("label") or "").strip(),
            ntype=ntype,
            prompt=prompt,
            content=content,
        )

    if lines:
        return "\n".join(lines)

    # 兼容旧快照：焦点节点上仍带 promptPreview / textPreview
    for n in nodes:
        if not isinstance(n, dict) or not n.get("focused"):
            continue
        nid = str(n.get("id") or "").strip()
        if nid in seen:
            continue
        ntype = str(n.get("type") or "").strip()
        limit = _focus_text_limit(ntype)
        prompt = _clip_focused_field(n.get("promptPreview"), limit)
        content = _clip_focused_field(n.get("textPreview"), limit)
        live_p, live_c = _live_text_excerpt(snapshot, nid, limit)
        if len(live_p) > len(prompt):
            prompt = live_p
        if len(live_c) > len(content):
            content = live_c
        if not prompt and not content:
            continue
        lines.append(f"- 【id={nid} | 名称={n.get('label') or ''}】 type={n.get('type')}")
        if prompt:
            lines.append(f'  prompt="{prompt}"')
        if content:
            lines.append(f'  content="{content}"')
    return "\n".join(lines) if lines else ""


def _snapshot_summary(snapshot: dict[str, Any] | None) -> str:
    """get_canvas_state：目录 + 文末焦点正文。"""
    if not isinstance(snapshot, dict):
        return _snapshot_missing_text()
    catalog = _snapshot_catalog(snapshot)
    focused = _focused_content_block(snapshot)
    if focused:
        return catalog + "\n\n【焦点内容】\n" + focused
    return catalog


def _snapshot_image_node_ids(snapshot: dict[str, Any] | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    out: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "image_input" and n.get("id"):
            out.append(str(n["id"]))
    return out


def _snapshot_text_node_id(snapshot: dict[str, Any] | None, *, label_hint: str) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") != "text_input":
            continue
        label = str(n.get("label") or "")
        if label_hint in label or n.get("id") == label_hint:
            return str(n.get("id") or "") or None
    return None


def _parse_gen_opts_from_text(text: str) -> dict[str, str]:
    """从自然语言提取 duration / aspectRatio / resolution（供启发式与 LLM 计划补全）。"""
    from .agent_generation_options import normalize_generation_options

    t = text or ""
    raw: dict[str, Any] = {}
    # 4s / 4秒 / 时长4秒 / 改成5秒 / 配置4s时长
    m = re.search(
        r"(?:时长|duration)?\s*(?:改成|换成|调成|设为|设置为|配置为|配置|为)?\s*(\d{1,2})\s*(?:s|秒)",
        t,
        re.I,
    )
    if not m:
        m = re.search(r"(\d{1,2})\s*(?:s|秒)", t, re.I)
    if m:
        raw["duration"] = m.group(1)
    if re.search(r"9\s*[:：x×]\s*16|竖屏", t, re.I):
        raw["aspectRatio"] = "9:16"
    elif re.search(r"16\s*[:：x×]\s*9|横屏", t, re.I):
        raw["aspectRatio"] = "16:9"
    elif re.search(r"1\s*[:：x×]\s*1|方形", t, re.I):
        raw["aspectRatio"] = "1:1"
    m2 = re.search(r"(720|1080)\s*[pP]?", t)
    if m2 and ("清晰" in t or "分辨率" in t or "720" in t or "1080" in t):
        raw["resolution"] = m2.group(1)
    return normalize_generation_options(raw)


def _inject_gen_opts_into_plan(
    plan: dict[str, Any],
    opts: dict[str, str],
    snapshot: dict[str, Any] | None,
    *,
    user_text: str = "",
) -> dict[str, Any]:
    """用户口令含时长等时，确保 plan 的 update/add 带上归一化后的 generationOptions。"""
    if not opts:
        return plan
    out = dict(plan)
    t = user_text or ""
    explicit_change = any(
        k in t
        for k in (
            "改成",
            "换成",
            "调成",
            "设为",
            "设置为",
            "配置",
            "改时长",
            "改参数",
            "时长改",
            "单镜",
            "每镜",
        )
    )

    updates = list(out.get("updateNodeParams") or []) if isinstance(out.get("updateNodeParams"), list) else []
    videos = (
        snapshot.get("nodes")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("nodes"), list)
        else []
    )
    video_ids = [
        str(n.get("id"))
        for n in videos
        if isinstance(n, dict) and str(n.get("type") or "") == "video_input" and n.get("id")
    ]

    def _merge_params(params: dict[str, Any], node_id: str = "") -> dict[str, Any]:
        from .agent_generation_options import (
            clamp_generation_options_for_model,
            normalize_generation_options,
        )

        p = dict(params)
        go = dict(p.get("generationOptions") or {}) if isinstance(p.get("generationOptions"), dict) else {}
        go.update(opts)
        go = normalize_generation_options(go)
        mid = str(p.get("model") or "").strip() or _snapshot_node_model(snapshot, node_id)
        if go:
            go, _n = clamp_generation_options_for_model(mid, go)
            p["generationOptions"] = go
        return p

    if updates:
        new_updates = []
        for it in updates:
            if not isinstance(it, dict):
                continue
            item = dict(it)
            params = dict(item.get("params") or {}) if isinstance(item.get("params"), dict) else {}
            item["params"] = _merge_params(params, str(item.get("nodeId") or ""))
            new_updates.append(item)
        out["updateNodeParams"] = new_updates
    elif video_ids and explicit_change:
        # 仅当用户明确说改/配置时，才对已有视频节点补 update（按时长合法区间钳制）
        out["updateNodeParams"] = [
            {
                "nodeId": nid,
                "params": _merge_params({"generationOptions": dict(opts)}, nid),
            }
            for nid in video_ids[:8]
        ]
        if not out.get("intent") or out.get("intent") == "chat":
            out["intent"] = "params"

    add_v = list(out.get("addVideoNodes") or []) if isinstance(out.get("addVideoNodes"), list) else []
    if add_v:
        new_v = []
        for it in add_v:
            if not isinstance(it, dict):
                continue
            item = dict(it)
            params = dict(item.get("params") or {}) if isinstance(item.get("params"), dict) else {}
            item["params"] = _merge_params(params)
            new_v.append(item)
        out["addVideoNodes"] = new_v
    return out


def _template_followup(
    user_text: str,
    graph: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """无 LLM / JSON 解析失败时的启发式续聊。

    关键词极明确时机械建节点；否则用搭档口吻追问，禁止菜单机器人话术。
    """
    t = (user_text or "").strip()
    low = t.lower()
    append_shots: list[dict[str, Any]] = []
    update_style = None
    script_note = None
    character_note = None
    append_sfx: list[dict[str, Any]] = []
    bgm_prompt = None
    connect: list[dict[str, Any]] = []
    generate: list[dict[str, Any]] = []
    add_video: list[dict[str, Any]] = []
    add_text: list[dict[str, Any]] = []
    add_image: list[dict[str, Any]] = []
    add_storyboard: list[dict[str, Any]] = []
    run_tools: list[dict[str, Any]] = []
    update_params: list[dict[str, Any]] = []
    intent = "chat"
    # 默认：追问补全，而非固定菜单
    reply = (
        "我没完全听清你想改画布的哪一步。"
        "你是想出一张图、做一个视频，还是先搭节点不生成？"
        "也可以直接描述画面或点选节点后再说。"
    )

    want_connect = any(k in t for k in ("连接", "连线", "接线", "连起来", "串起来", "接到"))
    want_generate = any(
        k in t for k in ("生成", "出图", "出视频", "全部生成", "都生成", "跑起来", "开始生成")
    )
    want_build_only = any(
        k in t for k in ("先别生成", "先不生成", "只要搭", "先搭节点", "先铺节点", "别生成")
    )
    want_switch_model = any(
        k in t
        for k in (
            "换模型",
            "切换模型",
            "改模型",
            "模型改",
            "用即梦",
            "换成即梦",
            "改成即梦",
            "换成全能",
            "改成全能",
            "换成seedance",
            "换成 Seedance",
            "改成seedance",
            "换成豆包图片",
            "视频模型",
            "图片模型",
        )
    ) or (
        any(k in t for k in ("模型", "即梦", "seedance", "Seedance", "vidu", "万相", "全能图片"))
        and any(k in t for k in ("换", "改", "切", "用成", "改成", "换成"))
    )
    want_series = any(
        k in t
        for k in (
            "一系列视频",
            "系列视频",
            "多镜视频",
            "多镜头",
            "短片成片",
            "分镜表",
            "批量视频",
            "批量出视频",
            "整集",
            "成片流水线",
            "解析视频",
            "根据视频",
            "拆镜",
            "拉片",
            "多段视频",
            "按镜生成",
        )
    )
    want_image = any(k in t for k in ("出一张图", "生成图片", "画一张", "做一张图", "文生图")) and not want_series
    want_video = any(k in t for k in ("生成视频", "做一个视频", "出个视频", "图生视频")) and not want_series
    want_script = any(k in t for k in ("写剧本", "写个故事", "写文案", "写提示词", "生成文本"))

    script_id = _snapshot_text_node_id(snapshot, label_hint="剧本") or "script"
    char_id = _snapshot_text_node_id(snapshot, label_hint="角色") or "characters"
    image_ids = _snapshot_image_node_ids(snapshot)
    if not image_ids:
        shots = graph.get("shots") if isinstance(graph.get("shots"), list) else []
        for sh in shots:
            if isinstance(sh, dict) and sh.get("canvasNodeId"):
                image_ids.append(str(sh["canvasNodeId"]))
            elif isinstance(sh, dict) and sh.get("id"):
                image_ids.append(str(sh["id"]))

    # 优先：切换节点模型（不依赖 LLM）
    if want_switch_model and not want_series:
        from .agent_node_models import (
            category_for_node_type,
            resolve_model_mention,
        )

        nodes = (
            snapshot.get("nodes")
            if isinstance(snapshot, dict) and isinstance(snapshot.get("nodes"), list)
            else []
        )
        # 推断目标类别
        prefer_cat = None
        if any(k in t for k in ("视频", "video", "seedance", "vidu", "万相")):
            prefer_cat = "video"
        elif any(k in t for k in ("图片", "图", "即梦", "文生图", "图生图")):
            prefer_cat = "image"
        elif any(k in t for k in ("文本", "剧本", "豆包", "deepseek")):
            prefer_cat = "text"
        mid = resolve_model_mention(t, prefer_category=prefer_cat)
        if not mid:
            mid = resolve_model_mention(t)
        targets: list[str] = []
        if mid and nodes:
            # 按 label 关键字命中
            label_keys = []
            for hint in ("主视觉", "镜头", "剧本", "角色", "视频", "图片"):
                if hint in t:
                    label_keys.append(hint)
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("id") or "").strip()
                ntype = str(n.get("type") or "")
                label = str(n.get("label") or "")
                if not nid:
                    continue
                cat = category_for_node_type(ntype)
                if prefer_cat and cat and cat != prefer_cat:
                    continue
                if label_keys and not any(k in label for k in label_keys):
                    # 若用户说了标签但本节点不匹配，跳过；若完全没命中则后面放宽
                    continue
                if cat or ntype in ("image_input", "video_input", "text_input"):
                    targets.append(nid)
            # 标签过滤过严导致空：按类型放宽
            if not targets:
                for n in nodes:
                    if not isinstance(n, dict):
                        continue
                    nid = str(n.get("id") or "").strip()
                    ntype = str(n.get("type") or "")
                    cat = category_for_node_type(ntype)
                    if not nid:
                        continue
                    if prefer_cat and cat != prefer_cat:
                        continue
                    if cat in ("image", "video", "text"):
                        targets.append(nid)
            # 仍空：所有 image
            if not targets:
                targets = list(image_ids[:8])
        if mid and targets:
            intent = "params"
            for nid in targets[:12]:
                update_params.append({"nodeId": nid, "params": {"model": mid}})
            reply = f"已将 {len(update_params)} 个节点模型切换为 `{mid}`。"
        elif mid:
            intent = "params"
            reply = (
                f"已识别模型 `{mid}`，但画布快照里没有可改的节点；"
                "请先添加图片/视频节点，或指明节点名称。"
            )
        else:
            reply = "没听清要换成哪个模型。可以说：把图片换成即梦 / 视频换成 Seedance。"

    # 改时长/画幅/清晰度（可与换模型叠加；无 LLM 时也生效）
    gen_opts = _parse_gen_opts_from_text(t)
    if gen_opts and not want_series:
        nodes = (
            snapshot.get("nodes")
            if isinstance(snapshot, dict) and isinstance(snapshot.get("nodes"), list)
            else []
        )
        video_targets = [
            str(n.get("id"))
            for n in nodes
            if isinstance(n, dict) and str(n.get("type") or "") == "video_input" and n.get("id")
        ]
        image_targets = [
            str(n.get("id"))
            for n in nodes
            if isinstance(n, dict) and str(n.get("type") or "") == "image_input" and n.get("id")
        ]
        # 时长优先打到视频；仅画幅/清晰度也可打图片
        targets = video_targets if ("duration" in gen_opts or video_targets) else image_targets
        if "duration" in gen_opts:
            targets = video_targets
        elif not targets:
            targets = image_targets
        if targets and intent in ("chat", "params"):
            intent = "params"
            for nid in targets[:12]:
                # 与已有换模型条目合并
                merged = False
                for up in update_params:
                    if up.get("nodeId") == nid:
                        params = dict(up.get("params") or {})
                        go = dict(params.get("generationOptions") or {})
                        go.update(gen_opts)
                        params["generationOptions"] = go
                        up["params"] = params
                        merged = True
                        break
                if not merged:
                    update_params.append(
                        {"nodeId": nid, "params": {"generationOptions": dict(gen_opts)}}
                    )
            bits = []
            if gen_opts.get("duration"):
                bits.append(f"{gen_opts['duration']}s")
            if gen_opts.get("aspectRatio"):
                bits.append(gen_opts["aspectRatio"])
            if gen_opts.get("resolution"):
                bits.append(f"{gen_opts['resolution']}p")
            reply = f"已更新 {len(targets[:12])} 个节点生成参数（{' · '.join(bits)}）。"

    # 失败节点诊断：用户说重试/修复 → 对 failedNodes 直接 generate（intent=repair）
    want_repair = any(
        k in t
        for k in (
            "重试",
            "重新生成",
            "再生成",
            "再跑",
            "修复",
            "修一下",
            "失败了",
            "挂了",
            "出错了",
            "报错",
        )
    ) or ("失败" in t and any(k in t for k in ("修", "重", "再", "生成")))
    failed_nodes = (
        snapshot.get("failedNodes")
        if isinstance(snapshot, dict) and isinstance(snapshot.get("failedNodes"), list)
        else []
    )
    if intent == "chat" and failed_nodes and (want_repair or (want_generate and "失败" in t)):
        intent = "repair"
        for it in failed_nodes[:8]:
            if not isinstance(it, dict):
                continue
            nid = str(it.get("id") or "").strip()
            if nid:
                generate.append({"nodeId": nid})
        if generate:
            reply = (
                f"看到 {len(generate)} 个失败节点，按原参数帮你重新生成。"
                "若仍失败，把节点提示词或模型改一下再说一声。"
            )
        else:
            reply = "快照里有失败标记，但没有可用的节点 id；请点选失败节点后再说「重试」。"

    # 主体消除（视频工具）：点选/快照中的视频 + 提示词
    want_subject_remove = any(
        k in t for k in ("主体消除", "消除主体", "去掉主体", "移除主体", "擦掉人物", "去掉人物")
    )
    if intent == "chat" and want_subject_remove and not want_build_only:
        video_ids = [
            str(n.get("id"))
            for n in (
                snapshot.get("nodes")
                if isinstance(snapshot, dict) and isinstance(snapshot.get("nodes"), list)
                else []
            )
            if isinstance(n, dict)
            and str(n.get("type") or "") == "video_input"
            and n.get("id")
            and (n.get("hasMedia") or n.get("focused"))
        ]
        if not video_ids and isinstance(snapshot, dict):
            video_ids = [
                str(n.get("id"))
                for n in (snapshot.get("nodes") or [])
                if isinstance(n, dict)
                and str(n.get("type") or "") == "video_input"
                and n.get("id")
            ][:1]
        if video_ids:
            intent = "tool"
            nid = video_ids[0]
            run_tools.append(
                {
                    "tool": "video_subject_remove",
                    "nodeId": nid,
                    "params": {"userPrompt": t} if t else {},
                }
            )
            reply = "将对选中/可用的视频节点执行「主体消除」（源片须 ≤12 秒，会扣算力）。"
        else:
            reply = "主体消除需要已有视频节点。请先上传或生成一段 ≤12 秒的视频，再点选后说「主体消除」。"

    # 故事板合成图（优先于「生成视频」启发式，避免只建空锚点）
    # 已有合成图 / 读板续聊：禁止再新建锚点，否则会循环出板
    from .agent_canvas_orchestrator import (
        should_skip_storyboard_sheet_gate,
        user_confirmed_video_generate,
        wants_storyboard_regen,
    )

    skip_sheet = should_skip_storyboard_sheet_gate(
        t, canvas_snapshot=snapshot, cinematic_phase=None
    ) and not wants_storyboard_regen(t)
    want_sheet = (
        any(k in t for k in ("故事板", "调度故事板"))
        and "分镜表" not in t
        and intent == "chat"
        and not want_build_only
        and not skip_sheet
    )
    if want_sheet:
        intent = "tool"
        tool_name = "blocking_storyboard" if "调度故事板" in t else "storyboard"
        anchor_tid = _new_id("board_anchor")
        add_image.append(
            {
                "tempId": anchor_tid,
                "label": "故事板锚点",
                "prompt": t,
                "x": 420,
                "y": 120,
            }
        )
        run_tools.append(
            {
                "tool": tool_name,
                "nodeId": anchor_tid,
                "nodeName": "故事板锚点",
                "params": {"userPrompt": t},
            }
        )
        reply = (
            "已建故事板锚点，并调用「故事板」工具生成多镜合成图（扣图算力）。"
            "本轮只出故事板；若要搭镜头或出视频，请再说。"
        )
    elif skip_sheet and intent == "chat":
        # 合成图已在画布：模板不重出板
        from .agent_generate_intent import is_storyboard_sheet_only

        intent = "chat" if is_storyboard_sheet_only(t) else "edit"
        if is_storyboard_sheet_only(t):
            reply = (
                "画布上已有故事板合成图，本轮不再新建。"
                "若要搭镜头或出视频请再说；要重做板请说「重新生成故事板」。"
            )
        else:
            reply = (
                "画布上已有故事板合成图，本轮不再新建故事板。"
                "请根据你的原目标继续（可搭镜头节点）。要出视频请回复「确认生成」。"
                "若要重做板，请说「重新生成故事板」。"
            )

    if want_series and intent == "chat":
        intent = "series_video"
        add_text.append(
            {
                "tempId": "script",
                "label": "剧本",
                "content": f"# 剧本\n\n{t}\n",
                "x": 80,
                "y": 80,
                "params": {"textPromptKind": "text_script"},
            }
        )
        add_storyboard.append({"tempId": "sb_main", "label": "分镜表", "x": 80, "y": 360})
        connect.append(
            {
                "source": "script",
                "target": "sb_main",
                "sourceHandle": "text",
                "targetHandle": "ref_in",
            }
        )
        run_tools.extend(
            [
                {"tool": "storyboard_table", "nodeId": "sb_main", "scriptFromNodeId": "script"},
                {"tool": "text_subject", "nodeId": "sb_main", "scriptFromNodeId": "script"},
            ]
        )
        if want_generate or any(k in t for k in ("出视频", "生成视频", "批量")):
            run_tools.append({"tool": "storyboard_video", "nodeId": "sb_main"})
            # 批量出视频须用户「确认生成」；模板不擅自跑 storyboard_batch_videos
            if user_confirmed_video_generate(t):
                run_tools.append({"tool": "storyboard_batch_videos", "nodeId": "sb_main"})
        elif any(k in t for k in ("出图", "主体图", "生图")):
            run_tools.append({"tool": "storyboard_subject_image", "nodeId": "sb_main"})
        reply = (
            "已按系列视频帮你铺好：剧本接到分镜表，并开始解析分镜与主体"
            + ("；接着会跑视频提示词与批量出视频（扣算力）" if len(run_tools) > 2 else "。若要批量出视频，再说一声即可")
            + "。"
        )
    elif intent == "chat" and want_image:
        intent = "image"
        # 每次启发式新建用唯一 tempId，避免与画布已有 img_1 冲突被误复用
        img_tid = _new_id("img")
        add_image.append(
            {
                "tempId": img_tid,
                "label": "主视觉",
                "prompt": t,
                "x": 400,
                "y": 80,
            }
        )
        # 明确「先别生成」才空；否则同轮触发生成（含「画一只猫」类生图意图）
        if not want_build_only:
            generate.append({"nodeId": img_tid})
            reply = "已在画布加好图片节点并写入提示词，马上触发生成（会扣算力）。"
        else:
            reply = "已在画布加好图片节点并写入提示词；按你的要求先不生成，需要出图时再说一声。"
    elif intent == "chat" and want_video:
        intent = "video"
        gen_opts = _parse_gen_opts_from_text(t)
        vid_params: dict[str, Any] = {}
        if gen_opts:
            vid_params["generationOptions"] = gen_opts
        # 唯一 tempId：已有「镜头1」时仍应新建，而不是连到旧节点
        vid_tid = _new_id("vid")
        add_video.append(
            {
                "tempId": vid_tid,
                "label": "视频",
                "prompt": t,
                "x": 700,
                "y": 80,
                "fromImageNodeId": image_ids[0] if image_ids else "",
                "params": vid_params,
            }
        )
        if not want_build_only:
            generate.append({"nodeId": vid_tid})
            dur_note = f"（时长 {gen_opts['duration']}s）" if gen_opts.get("duration") else ""
            reply = f"已加好视频节点并写入提示词{dur_note}，马上触发生成（会扣算力）。"
        else:
            reply = "已加好视频节点并写入提示词；按你的要求先不生成。"
    elif intent == "chat" and want_script:
        intent = "text"
        add_text.append(
            {
                "tempId": "script",
                "label": "剧本",
                "content": f"# 剧本\n\n{t}\n",
                "x": 80,
                "y": 80,
            }
        )
        reply = "已创建剧本文本节点。若要出图/出视频/系列分镜，继续说即可。"

    if want_connect and not want_series and intent == "chat":
        for iid in image_ids[:12]:
            connect.append(
                {
                    "source": script_id,
                    "target": iid,
                    "sourceHandle": "text",
                    "targetHandle": "ref_in",
                }
            )
            if char_id:
                connect.append(
                    {
                        "source": char_id,
                        "target": iid,
                        "sourceHandle": "text",
                        "targetHandle": "ref_in",
                    }
                )
        reply = f"已规划把剧本/角色接到 {min(len(image_ids), 12)} 个镜头图节点。"

    if want_generate and not want_series and not want_image and not want_video and intent == "chat":
        for iid in image_ids[:12]:
            generate.append({"nodeId": iid})
        if generate:
            reply = (
                (reply + " ") if want_connect else ""
            ) + f"将触发生成 {len(generate)} 个镜头（按模型扣算力，与手点同价）。"
        elif intent == "chat":
            reply = "画布上还没有可生成的镜头图节点；请先说明要出图/出视频，或完成编排投影。"

    if intent == "chat" and not want_connect and not want_generate:
        if any(k in t for k in ("加镜", "加一镜", "多一镜", "补镜", "再来一镜", "加镜头", "多几个镜头")):
            n = 2 if any(k in t for k in ("两", "2", "几个", "多几")) else 1
            for _ in range(n):
                append_shots.append(
                    {
                        "action": f"按用户要求补充的镜头：{t[:40]}",
                        "imagePrompt": f"{t}, cinematic shot, detailed",
                    }
                )
            reply = f"已规划补充 {n} 个镜头节点到画布。"
        elif any(k in t for k in ("画幅", "9:16", "16:9", "竖屏", "横屏", "风格", "色调")):
            aspect = "16:9" if any(k in t for k in ("16:9", "横屏")) else "9:16"
            if "竖屏" in t or "9:16" in t:
                aspect = "9:16"
            labels = ["cinematic"]
            if "动漫" in t or "anime" in low:
                labels = ["anime"]
            if "水彩" in t:
                labels = ["watercolor"]
            update_style = {
                "labels": labels,
                "aspectRatio": aspect,
                "paletteNotes": t[:80],
            }
            reply = f"已更新风格/画幅为 {aspect}（{', '.join(labels)}）。"
        elif any(k in t for k in ("剧本", "台词", "对白", "故事", "改剧")):
            script_note = f"# 剧本修订\n\n按你的要求：{t}\n"
            reply = "已写入「剧本·修订」文本节点。"
        elif any(k in t for k in ("角色", "人物", "主角", "外形")):
            character_note = f"# 角色修订\n\n按你的要求：{t}\n"
            reply = "已写入「角色·修订」文本节点。"
        elif any(k in t for k in ("音效", "sfx", "SFX", "BGM", "bgm", "配乐", "音乐")):
            if any(k in t for k in ("BGM", "bgm", "配乐", "音乐")):
                bgm_prompt = t
                reply = "已添加 BGM 音频节点占位。"
            else:
                append_sfx.append({"prompt": t})
                reply = "已添加音效节点占位。"
        else:
            # 意图不清：只追问，不塞备忘节点（避免像填表机器人）
            reply = (
                "我想先确认一下：你是要我改当前画布上的某个节点，"
                "还是新建出图/出视频？可以说得更具体一点，或用准星点选节点后再发。"
            )

    return {
        "reply": reply,
        "intent": intent,
        "appendShots": append_shots,
        "updateStyle": update_style,
        "scriptNote": script_note,
        "characterNote": character_note,
        "appendSfx": append_sfx,
        "bgmPrompt": bgm_prompt,
        "addVideoNodes": add_video,
        "addTextNodes": add_text,
        "addImageNodes": add_image,
        "addStoryboardNodes": add_storyboard,
        "runCanvasTools": run_tools,
        "updateNodeParams": update_params,
        "connect": connect,
        "generate": generate,
    }


async def _llm_followup(
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
) -> dict[str, Any]:
    """兼容入口：实际编排已迁至 agent_canvas_orchestrator.compose_followup_plan。"""
    from .agent_canvas_orchestrator import compose_followup_plan

    plan, _src = await compose_followup_plan(
        user_text,
        graph,
        preferred_model=preferred_model,
        canvas_snapshot=canvas_snapshot,
        my_skill=my_skill,
        platform_skill=platform_skill,
        skill_context=skill_context,
        image_urls=image_urls,
        extra_rules_markdown=extra_rules_markdown,
    )
    return plan


def _append_generate_ops(ops: list[dict[str, Any]], plan: dict[str, Any]) -> int:
    raw = plan.get("generate") if isinstance(plan.get("generate"), list) else []
    n = 0
    seen: set[str] = set()
    for item in raw[:12]:
        node_name = ""
        if isinstance(item, dict):
            nid = str(item.get("nodeId") or item.get("tempId") or "").strip()
            node_name = str(item.get("nodeName") or item.get("label") or "").strip()
        else:
            nid = str(item or "").strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        op: dict[str, Any] = {"op": "generate_node", "tempId": nid, "nodeId": nid}
        if node_name:
            op["nodeName"] = node_name
        ops.append(op)
        n += 1
    return n


def _snapshot_node_model(snapshot: dict[str, Any] | None, node_id: str) -> str | None:
    """从画布快照取节点当前模型 id，供改参时按模型钳制时长。"""
    nid = (node_id or "").strip()
    if not nid or not isinstance(snapshot, dict):
        return None
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if str(n.get("id") or "").strip() != nid:
            continue
        mid = str(n.get("model") or "").strip()
        return mid or None
    return None


def _append_update_param_ops(
    ops: list[dict[str, Any]],
    plan: dict[str, Any],
    canvas_snapshot: dict[str, Any] | None = None,
) -> tuple[int, list[str]]:
    from .agent_generation_options import sanitize_agent_node_params
    from .agent_node_models import resolve_model_mention

    raw = plan.get("updateNodeParams") if isinstance(plan.get("updateNodeParams"), list) else []
    n = 0
    clamp_notes: list[str] = []
    for item in raw[:16]:
        if not isinstance(item, dict):
            continue
        nid = str(item.get("nodeId") or item.get("tempId") or "").strip()
        node_name = str(item.get("nodeName") or item.get("label") or "").strip()
        params = item.get("params") if isinstance(item.get("params"), dict) else None
        if not nid or not params:
            continue
        snap_model = _snapshot_node_model(canvas_snapshot, nid)
        # 时长/画幅等归一进 generationOptions；按快照模型钳制合法时长
        clean, notes = sanitize_agent_node_params(params, model_name=snap_model)
        clamp_notes.extend(notes)
        if "model" in clean and isinstance(clean["model"], str):
            resolved = resolve_model_mention(clean["model"]) or str(clean["model"]).strip()
            if resolved:
                clean["model"] = resolved
            else:
                clean.pop("model", None)
        if not clean:
            continue
        op: dict[str, Any] = {
            "op": "update_node_params",
            "tempId": nid,
            "nodeId": nid,
            "params": clean,
        }
        if node_name:
            op["nodeName"] = node_name
        ops.append(op)
        n += 1
    return n, clamp_notes


def _append_generic_add_nodes(
    ops: list[dict[str, Any]],
    plan: dict[str, Any],
    *,
    x0: float,
    y0: float,
    gap_x: float,
    gap_y: float,
    row0: int,
    canvas_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    """通用 addText/Image/Video/Storyboard/Audio → canvasOps；返回摘要 notes。"""
    notes: list[str] = []

    texts = plan.get("addTextNodes") if isinstance(plan.get("addTextNodes"), list) else []
    for i, raw in enumerate(texts[:8]):
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tempId") or _new_id("txt")).strip()
        label = str(raw.get("label") or f"文本{i+1}").strip()
        content = str(raw.get("content") or raw.get("prompt") or "").strip()
        try:
            tx = float(raw.get("x") if raw.get("x") is not None else x0 + (i % 3) * gap_x)
            ty = float(raw.get("y") if raw.get("y") is not None else y0 + row0 * gap_y)
        except (TypeError, ValueError):
            tx, ty = float(x0), float(y0 + row0 * gap_y)
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        ops.append(
            {
                "op": "add_text_node",
                "tempId": tid,
                "label": label,
                "content": content,
                "x": tx,
                "y": ty,
                "params": params,
            }
        )
    if texts:
        notes.append(f"文本节点×{min(len(texts), 8)}")

    images = plan.get("addImageNodes") if isinstance(plan.get("addImageNodes"), list) else []
    for i, raw in enumerate(images[:8]):
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tempId") or _new_id("img")).strip()
        label = str(raw.get("label") or f"图片{i+1}").strip()
        prompt = str(raw.get("prompt") or raw.get("imagePrompt") or "").strip()
        try:
            ix = float(raw.get("x") if raw.get("x") is not None else x0 + gap_x + (i % 3) * gap_x)
            iy = float(raw.get("y") if raw.get("y") is not None else y0 + row0 * gap_y)
        except (TypeError, ValueError):
            ix, iy = float(x0 + gap_x), float(y0 + row0 * gap_y)
        extra = raw.get("params") if isinstance(raw.get("params"), dict) else None
        ops.append(
            {
                "op": "add_image_node",
                "tempId": tid,
                "label": label,
                "prompt": prompt,
                "x": ix,
                "y": iy,
                "params": agent_image_node_params_for_snapshot(canvas_snapshot, extra),
            }
        )
        # 可选：从参考节点自动接线
        from_ref = str(raw.get("fromNodeId") or raw.get("fromTextNodeId") or "").strip()
        if from_ref:
            ops.append(
                {
                    "op": "connect_nodes",
                    "source": from_ref,
                    "target": tid,
                    "sourceHandle": str(raw.get("fromHandle") or "text"),
                    "targetHandle": "ref_in",
                }
            )
    if images:
        notes.append(f"图片节点×{min(len(images), 8)}")

    boards = plan.get("addStoryboardNodes") if isinstance(plan.get("addStoryboardNodes"), list) else []
    for i, raw in enumerate(boards[:4]):
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tempId") or _new_id("sb")).strip()
        label = str(raw.get("label") or "分镜表").strip()
        try:
            bx = float(raw.get("x") if raw.get("x") is not None else x0)
            by = float(raw.get("y") if raw.get("y") is not None else y0 + (row0 + 1) * gap_y)
        except (TypeError, ValueError):
            bx, by = float(x0), float(y0 + (row0 + 1) * gap_y)
        ops.append(
            {
                "op": "add_storyboard_node",
                "tempId": tid,
                "label": label,
                "x": bx,
                "y": by,
            }
        )
    if boards:
        notes.append(f"分镜表×{min(len(boards), 4)}")

    audios = plan.get("addAudioNodes") if isinstance(plan.get("addAudioNodes"), list) else []
    for i, raw in enumerate(audios[:4]):
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tempId") or _new_id("aud")).strip()
        label = str(raw.get("label") or f"音频{i+1}").strip()
        prompt = str(raw.get("prompt") or "").strip()
        try:
            ax = float(raw.get("x") if raw.get("x") is not None else x0 + (i % 3) * gap_x)
            ay = float(raw.get("y") if raw.get("y") is not None else y0 + (row0 + 2) * gap_y)
        except (TypeError, ValueError):
            ax, ay = float(x0), float(y0 + (row0 + 2) * gap_y)
        ops.append(
            {
                "op": "add_audio_node",
                "tempId": tid,
                "label": label,
                "prompt": prompt,
                "x": ax,
                "y": ay,
                "params": raw.get("params") if isinstance(raw.get("params"), dict) else {},
            }
        )
    if audios:
        notes.append(f"音频节点×{min(len(audios), 4)}")

    return notes


def _append_run_tool_ops(ops: list[dict[str, Any]], plan: dict[str, Any]) -> int:
    raw = plan.get("runCanvasTools") if isinstance(plan.get("runCanvasTools"), list) else []
    n = 0
    for item in raw[:6]:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        nid = str(item.get("nodeId") or item.get("tempId") or "").strip()
        if not tool or not nid:
            continue
        op: dict[str, Any] = {
            "op": "run_canvas_tool",
            "tool": tool,
            "tempId": nid,
            "nodeId": nid,
        }
        node_name = str(item.get("nodeName") or item.get("label") or "").strip()
        if node_name:
            op["nodeName"] = node_name
        script_from = str(item.get("scriptFromNodeId") or "").strip()
        if script_from:
            op["scriptFromNodeId"] = script_from
        script_from_name = str(item.get("scriptFromNodeName") or "").strip()
        if script_from_name:
            op["scriptFromNodeName"] = script_from_name
        if isinstance(item.get("params"), dict):
            op["params"] = item["params"]
        ops.append(op)
        n += 1
    return n


def _append_connect_ops(ops: list[dict[str, Any]], plan: dict[str, Any]) -> int:
    raw = plan.get("connect") if isinstance(plan.get("connect"), list) else []
    n = 0
    for item in raw[:32]:
        if not isinstance(item, dict):
            continue
        src = str(item.get("source") or "").strip()
        tgt = str(item.get("target") or "").strip()
        if not src or not tgt or src == tgt:
            continue
        sh = str(item.get("sourceHandle") or "").strip() or None
        th = str(item.get("targetHandle") or "ref_in").strip() or "ref_in"
        op: dict[str, Any] = {
            "op": "connect_nodes",
            "source": src,
            "target": tgt,
            "sourceHandle": sh,
            "targetHandle": th,
        }
        # 与 ID 成对透传名称，投影侧按「ID+名称」校验
        src_name = str(item.get("sourceName") or "").strip()
        tgt_name = str(item.get("targetName") or "").strip()
        if src_name:
            op["sourceName"] = src_name
        if tgt_name:
            op["targetName"] = tgt_name
        ops.append(op)
        n += 1
    return n


def _apply_plan_to_graph(
    graph: dict[str, Any],
    plan: dict[str, Any],
    canvas_snapshot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """合并 plan → 新 graph + canvasOps + 变更摘要。"""
    g = dict(graph or {})
    ops: list[dict[str, Any]] = []
    notes: list[str] = []
    x0, y0, gap_x, gap_y = 80, 80, 300, 220
    pref_img, _pref_vid = preferred_models_from_snapshot(canvas_snapshot)
    default_image_model = pref_img or AGENT_DEFAULT_IMAGE_T2I

    shots = list(g.get("shots") or []) if isinstance(g.get("shots"), list) else []
    base_idx = len(shots)
    row0 = 1 + (base_idx // 4)

    style = dict(g.get("style") or {}) if isinstance(g.get("style"), dict) else {}
    upd = plan.get("updateStyle")
    if isinstance(upd, dict) and upd:
        if upd.get("labels"):
            style["labels"] = list(upd.get("labels") or [])[:5]
        if upd.get("aspectRatio"):
            style["aspectRatio"] = str(upd.get("aspectRatio"))
        if upd.get("paletteNotes") is not None:
            style["paletteNotes"] = str(upd.get("paletteNotes") or "")
        g["style"] = style
        notes.append("更新风格")

    append_shots = plan.get("appendShots") if isinstance(plan.get("appendShots"), list) else []
    new_shot_temp_ids: list[str] = []
    for i, raw in enumerate(append_shots[:4]):
        if not isinstance(raw, dict):
            continue
        sid = _new_id("sh")
        action = str(raw.get("action") or "补充镜头").strip()
        prompt = str(raw.get("imagePrompt") or action).strip()
        shot = {
            "id": sid,
            "beatId": None,
            "characterIds": [],
            "sceneId": None,
            "action": action,
            "imagePrompt": prompt,
            "preferredModel": default_image_model,
            "status": "planned",
            "outputAssetId": None,
            "canvasNodeId": sid,  # 投影时节点 id = tempId
        }
        shots.append(shot)
        new_shot_temp_ids.append(sid)
        col, row = (base_idx + i) % 4, row0 + (base_idx + i) // 4
        params: dict[str, Any] = agent_image_node_params_for_snapshot(canvas_snapshot)
        if style.get("styleId"):
            params["visualStyleId"] = style["styleId"]
        ops.append(
            {
                "op": "add_image_node",
                "tempId": sid,
                "label": f"镜头{base_idx + i + 1}",
                "prompt": prompt,
                "x": x0 + col * gap_x,
                "y": y0 + gap_y + row * gap_y,
                "shotId": sid,
                "params": params,
            }
        )
    if append_shots:
        notes.append(f"加镜×{min(len(append_shots), 4)}")
    g["shots"] = shots

    timeline = dict(g.get("timeline") or {}) if isinstance(g.get("timeline"), dict) else {}
    timeline["shotIds"] = [s["id"] for s in shots if isinstance(s, dict) and s.get("id")]
    g["timeline"] = timeline

    script_note = plan.get("scriptNote")
    if isinstance(script_note, str) and script_note.strip():
        tid = f"script_rev_{uuid.uuid4().hex[:6]}"
        ops.append(
            {
                "op": "add_text_node",
                "tempId": tid,
                "label": "剧本·修订",
                "content": script_note.strip(),
                "x": x0,
                "y": y0 + gap_y * (row0 + 2),
                "params": {"textPromptKind": "text_script"},
            }
        )
        notes.append("剧本修订")

    character_note = plan.get("characterNote")
    if isinstance(character_note, str) and character_note.strip():
        tid = f"char_rev_{uuid.uuid4().hex[:6]}"
        ops.append(
            {
                "op": "add_text_node",
                "tempId": tid,
                "label": "角色·修订",
                "content": character_note.strip(),
                "x": x0 + gap_x,
                "y": y0 + gap_y * (row0 + 2),
                "params": {"textPromptKind": "text_subject"},
            }
        )
        notes.append("角色修订")

    audio = dict(g.get("audio") or {}) if isinstance(g.get("audio"), dict) else {}
    bgm_prompt = plan.get("bgmPrompt")
    if isinstance(bgm_prompt, str) and bgm_prompt.strip():
        audio["bgm"] = {
            "mood": audio.get("bgm", {}).get("mood")
            if isinstance(audio.get("bgm"), dict)
            else "cinematic",
            "prompt": bgm_prompt.strip(),
            "status": "planned",
        }
        ops.append(
            {
                "op": "add_audio_node",
                "tempId": f"bgm_{uuid.uuid4().hex[:6]}",
                "label": "BGM·修订",
                "prompt": bgm_prompt.strip(),
                "x": x0 + gap_x * 2,
                "y": y0 + gap_y * (row0 + 2),
            }
        )
        notes.append("BGM")

    append_sfx = plan.get("appendSfx") if isinstance(plan.get("appendSfx"), list) else []
    sfx_list = list(audio.get("sfx") or []) if isinstance(audio.get("sfx"), list) else []
    for i, raw in enumerate(append_sfx[:4]):
        if not isinstance(raw, dict):
            continue
        prompt = str(raw.get("prompt") or "").strip()
        if not prompt:
            continue
        sfx_list.append({"prompt": prompt, "status": "planned"})
        ops.append(
            {
                "op": "add_audio_node",
                "tempId": f"sfx_{uuid.uuid4().hex[:6]}",
                "label": f"SFX·{len(sfx_list)}",
                "prompt": prompt,
                "x": x0 + gap_x * (i % 3),
                "y": y0 + gap_y * (row0 + 3),
            }
        )
    if append_sfx:
        notes.append(f"SFX×{min(len(append_sfx), 4)}")
    if sfx_list:
        audio["sfx"] = sfx_list
    if audio:
        g["audio"] = audio

    # 视频节点 + 可选从图自动连线
    add_videos = plan.get("addVideoNodes") if isinstance(plan.get("addVideoNodes"), list) else []
    for i, raw in enumerate(add_videos[:4]):
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("tempId") or _new_id("vid")).strip()
        label = str(raw.get("label") or f"视频{i+1}").strip()
        prompt = str(raw.get("prompt") or "").strip()
        try:
            vx = float(raw.get("x") if raw.get("x") is not None else x0 + gap_x * 3)
            vy = float(raw.get("y") if raw.get("y") is not None else y0 + gap_y * (row0 + 1 + i))
        except (TypeError, ValueError):
            vx, vy = float(x0 + gap_x * 3), float(y0 + gap_y * (row0 + 1 + i))
        ops.append(
            {
                "op": "add_video_node",
                "tempId": tid,
                "label": label,
                "prompt": prompt,
                "x": vx,
                "y": vy,
                "params": agent_video_node_params_for_snapshot(
                    canvas_snapshot,
                    raw.get("params") if isinstance(raw.get("params"), dict) else None,
                ),
            }
        )
        from_img = str(raw.get("fromImageNodeId") or "").strip()
        if from_img:
            ops.append(
                {
                    "op": "connect_nodes",
                    "source": from_img,
                    "target": tid,
                    "sourceHandle": "image",
                    "targetHandle": "ref_in",
                }
            )
    if add_videos:
        notes.append(f"视频节点×{min(len(add_videos), 4)}")

    # 通用新节点（文本/图/分镜/音频）——放在旧字段之后，便于 tempId 覆盖复用
    notes.extend(
        _append_generic_add_nodes(
            ops,
            plan,
            x0=x0,
            y0=y0,
            gap_x=gap_x,
            gap_y=gap_y,
            row0=row0 + 3,
            canvas_snapshot=canvas_snapshot,
        )
    )

    # 改参数 → 连线 → 工具 → 生成（顺序：结构先齐，再扣费动作）
    un, clamp_notes = _append_update_param_ops(ops, plan, canvas_snapshot)
    notes.extend(clamp_notes)
    if un:
        notes.append(f"改参数×{un}")
    cn = _append_connect_ops(ops, plan)
    if cn:
        notes.append(f"连线×{cn}")
    tn = _append_run_tool_ops(ops, plan)
    if tn:
        notes.append(f"工具×{tn}")
    gn = _append_generate_ops(ops, plan)
    if gn:
        notes.append(f"生成×{gn}")

    return g, ops, notes


def _collect_existing_asset_ids(
    *,
    canvas_snapshot: dict[str, Any] | None,
    plan: dict[str, Any],
    ops: list[dict[str, Any]],
) -> set[str]:
    """画布快照 / plan / 已生成 ops 中已有的 assetId。"""
    existing: set[str] = set()
    for n in (canvas_snapshot or {}).get("nodes") or []:
        if not isinstance(n, dict):
            continue
        params = n.get("params") if isinstance(n.get("params"), dict) else {}
        aid = str(params.get("assetId") or n.get("assetId") or "").strip()
        if aid:
            existing.add(aid)
    for item in plan.get("addImageNodes") or []:
        if not isinstance(item, dict):
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        aid = str(params.get("assetId") or "").strip()
        if aid:
            existing.add(aid)
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "add_image_node":
            continue
        params = op.get("params") if isinstance(op.get("params"), dict) else {}
        aid = str(params.get("assetId") or "").strip()
        if aid:
            existing.add(aid)
    return existing


def _ensure_reference_image_ops(
    ops: list[dict[str, Any]],
    *,
    reference_asset_ids: list[str],
    canvas_snapshot: dict[str, Any] | None,
    plan: dict[str, Any],
) -> list[str]:
    """若参考图尚未以 image 节点落到画布，补 add_image_node + assetId。"""
    existing = _collect_existing_asset_ids(
        canvas_snapshot=canvas_snapshot, plan=plan, ops=ops
    )
    notes: list[str] = []
    for i, aid in enumerate(reference_asset_ids[:8]):
        aid = str(aid or "").strip()
        if not aid or aid in existing:
            continue
        ops.append(
            {
                "op": "add_image_node",
                "tempId": f"ref_{aid[:8]}_{i}",
                "label": f"参考图{i + 1}",
                "prompt": "",
                "x": 80.0 + float(i) * 280.0,
                "y": 40.0,
                "params": {"assetId": aid},
            }
        )
        existing.add(aid)
        notes.append(f"参考图→画布:{aid[:8]}")
    return notes


def _parse_assemble_editor_intent(text: str) -> tuple[bool, bool, bool]:
    """解析「组装/打开剪辑台」意图。

    返回 (matched, force_replace, want_assemble)：
    - force：强制覆盖已有草稿
    - want_assemble：需要写入草稿（打开且草稿空时也会尝试组装）
    """
    t = (text or "").strip()
    if not t:
        return False, False, False
    force = any(
        k in t
        for k in ("强制组装剪辑台", "强制组装时间线", "覆盖组装剪辑台", "覆盖组装时间线")
    )
    want_assemble = force or any(
        k in t for k in ("组装剪辑台", "组装时间线", "写入剪辑台", "写入时间线")
    )
    want_open = any(
        k in t
        for k in (
            "打开剪辑台",
            "打开剪辑页",
            "组装剪辑台",
            "组装时间线",
            "写入剪辑台",
            "写入时间线",
            "强制组装剪辑台",
            "强制组装时间线",
            "覆盖组装剪辑台",
            "覆盖组装时间线",
        )
    )
    if not want_assemble and not want_open and not force:
        return False, False, False
    return True, force, want_assemble or force


async def _try_assemble_editor_followup(
    db: AsyncSession,
    *,
    session: AgentSession,
    text: str,
) -> dict[str, Any] | None:
    """早路径：组装/打开剪辑台，不走 LLM 编排。"""
    matched, force, want_assemble = _parse_assemble_editor_intent(text)
    if not matched:
        return None

    from sqlalchemy import select as sa_select

    from ..models.project import Project
    from .project_scope import project_storage_folder
    from .video_editor_assemble import assemble_timeline_from_graph, read_editor_draft_raw

    project_id = int(session.project_id)
    pid = str(project_id)

    folder: str | None = None
    try:
        prow = (
            await db.execute(sa_select(Project).where(Project.id == project_id).limit(1))
        ).scalar_one_or_none()
        if prow is not None:
            folder = project_storage_folder(prow)
    except Exception:  # noqa: BLE001
        folder = None

    if want_assemble or force:
        result = await assemble_timeline_from_graph(db, project_id, replace=force)
    else:
        # 仅「打开剪辑台」：草稿空则尝试静默组装，再决定是否打开
        existing = await read_editor_draft_raw(pid, folder)
        clips = existing.get("clips") if isinstance(existing.get("clips"), list) else []
        if clips:
            result = {
                "projectId": pid,
                "skipped": True,
                "reason": "open_existing",
                "clipCount": len(clips),
                "videoClipCount": sum(
                    1 for c in clips if isinstance(c, dict) and c.get("kind") != "audio"
                ),
                "audioClipCount": sum(
                    1 for c in clips if isinstance(c, dict) and c.get("kind") == "audio"
                ),
                "message": "正在打开剪辑台…",
                "openEditor": True,
            }
        else:
            result = await assemble_timeline_from_graph(db, project_id, replace=False)
            if not result.get("openEditor") and int(result.get("videoClipCount") or 0) <= 0:
                result = {
                    **result,
                    "message": str(result.get("message") or "")
                    + " 仍可为你打开空剪辑台。",
                    "openEditor": True,
                }

    reply = str(result.get("message") or "已处理剪辑台请求。")
    ops: list[dict[str, Any]] = []
    if result.get("openEditor"):
        ops.append({"op": "open_video_editor"})

    saved = None
    if ops:
        payload = await project_graphs.get_graph_dict(db, project_id)
        graph = dict(payload.get("graph") or {})
        saved = await project_graphs.save_graph(
            db, project_id, graph, canvas_ops=ops, bump_revision=True
        )

    session.status = "completed"
    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        agent_role="editor",
        content=reply,
        graph_patch={
            "revision": (saved or {}).get("revision"),
            "assemble": {
                "skipped": bool(result.get("skipped")),
                "clipCount": int(result.get("clipCount") or 0),
                "videoClipCount": int(result.get("videoClipCount") or 0),
            },
        },
    )
    return saved if saved is not None else {"projectId": pid, "assembled": True}


async def run_session_followup(
    db: AsyncSession,
    *,
    session: AgentSession,
    user: User,
    message: str,
    canvas_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """传话层：收集上下文后投递黑箱编排，再把 plan 写入 Graph / canvasOps。

    禁止在本函数内润色用户文案；编排逻辑见 agent_canvas_orchestrator。
    """
    del user  # 预留鉴权扩展
    text = (message or "").strip()
    if not text:
        return None

    # 早路径：组装/打开剪辑台（不依赖画布快照、不调 LLM）
    early = await _try_assemble_editor_followup(db, session=session, text=text)
    if early is not None:
        return early

    # 无快照：勿当成空白画布大拆重建（前端每次思考必须带最新 canvasSnapshot）
    if not isinstance(canvas_snapshot, dict):
        logger.warning(
            "agent_followup: canvas_snapshot missing session=%s；本轮不按空白画布编排",
            session.id,
        )
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            agent_role="orchestrator",
            content="我这边没拿到当前画布快照。请在画布页再发一句，我会按最新节点来做。",
        )
        return None

    project_id = int(session.project_id)
    payload = await project_graphs.get_graph_dict(db, project_id)
    graph = dict(payload.get("graph") or {})
    pending_ops = payload.get("canvasOps") if isinstance(payload.get("canvasOps"), list) else []

    def _is_ref_only_ops(ops_list: list[Any]) -> bool:
        if not ops_list:
            return True
        for o in ops_list:
            if not isinstance(o, dict):
                return False
            if o.get("op") != "add_image_node":
                return False
            tid = str(o.get("tempId") or "")
            params = o.get("params") if isinstance(o.get("params"), dict) else {}
            # bind / ensure 写入的参考图节点
            if not (tid.startswith("ref_") or params.get("assetId")):
                return False
        return True

    def _is_nav_only_ops(ops_list: list[Any]) -> bool:
        """仅打开剪辑台等导航 ops：可清掉后继续本轮，避免卡住对话。"""
        if not ops_list:
            return False
        return all(
            isinstance(o, dict) and str(o.get("op") or "") == "open_video_editor"
            for o in ops_list
        )

    pending_ref_ops: list[dict[str, Any]] = []
    # 上一轮投影尚未被前端 ack：禁止覆盖，避免丢掉 Team 的加节点/连线
    # 例外：仅「参考图落点」ops（bind 竞态写入）可并入本轮继续操控
    # 例外：仅 open_video_editor 导航 ops 可清空后继续
    if pending_ops and _is_nav_only_ops(pending_ops):
        await project_graphs.clear_canvas_ops(db, project_id)
        pending_ops = []
    if pending_ops and not _is_ref_only_ops(pending_ops):
        session.status = "completed"
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            agent_role="orchestrator",
            content=(
                "上一轮节点/连线还在落到画布上。"
                "你这边可以稍等几秒，或把下一句先打好——"
                "前端会在投影完成后自动发出（也可刷新画布后再试）。"
            ),
        )
        return None
    if pending_ops and _is_ref_only_ops(pending_ops):
        pending_ref_ops = [o for o in pending_ops if isinstance(o, dict)]

    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        agent_role="orchestrator",
        content="好的，按你的原话开始编排…",
    )

    # 刷新 brief（首页可能在 create 后异步 bind 写入 referenceAssetIds）
    await db.refresh(session)
    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    my_defaults = (
        brief.get("mySkillDefaults")
        if isinstance(brief.get("mySkillDefaults"), dict)
        else {}
    )

    # 消息附件并入 brief.referenceAssetIds
    msg_aids = agent_sessions._extract_asset_ids_from_text(text)
    if msg_aids:
        prev_refs = [
            str(x).strip() for x in (brief.get("referenceAssetIds") or []) if str(x).strip()
        ]
        merged_refs: list[str] = []
        seen_r: set[str] = set()
        for aid in [*prev_refs, *msg_aids]:
            if aid in seen_r:
                continue
            seen_r.add(aid)
            merged_refs.append(aid)
            if len(merged_refs) >= 20:
                break
        brief["referenceAssetIds"] = merged_refs
        brief.pop("awaitingReferenceBind", None)
        session.brief_json = brief
        await db.flush()

    skill_ctx = ""
    # 技能包能力对齐：我的 Skill 若 execution_mode=canvas_manual（brief.preferCanvasManual
    # 已在建会话/方案确认时写入），也按平台技能包水平走整包注入 + 画布操控附录，
    # 不再因 my_defaults 存在而被强制降级为 Team 模板路径
    # 技能包能力对齐：preferCanvasManual 走画布操控。
    # 宣传片附录（禁止分镜表等）仅给「非我的 Skill」平台包；用户自建 canvas_manual
    # 只注入技能包正文 + 我的 Skill 附录，避免套上电影级宣传片硬规则。
    platform_skill = bool(brief.get("preferCanvasManual")) and not bool(my_defaults)
    canvas_rules = ""
    try:
        from .agent_clarify import get_session_skill_canvas_rules, resolve_session_skill_context

        skill_ctx = await resolve_session_skill_context(db, session, brief)
        canvas_rules = await get_session_skill_canvas_rules(db, session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_followup: skill context failed: %s", exc)
        skill_ctx = ""
        if my_defaults:
            try:
                from .agent_clarify import _skill_context_from_brief

                skill_ctx = _skill_context_from_brief(brief)
            except Exception:  # noqa: BLE001
                skill_ctx = ""
    if not platform_skill and skill_ctx and not my_defaults:
        platform_skill = True
    phase = str(brief.get("mySkillPhase") or "")
    if phase:
        skill_ctx = (skill_ctx + f"\n当前阶段 mySkillPhase={phase}").strip()
    # 电影级宣传片阶段：传给黑箱编排服务（确认门在 orchestrator 内强制执行）
    cinematic_phase = str(brief.get("cinematicPhase") or "").strip()

    # 消息附件 + 会话参考图 + 画布快照图片 → HTTPS URL，供视觉模型读画面（含故事板）
    ref_ids = [
        str(x).strip()
        for x in (brief.get("referenceAssetIds") or [])
        if str(x).strip()
    ]
    # 读板分镜时适当提高附图上限（故事板 + 产品参考）
    max_vision = 6 if any(
        k in text for k in (
            "故事板", "调度故事板", "读板", "分镜格", "合成板已就绪",
            "读板分配镜头", "这是最新画布", "根据用户原目标继续",
        )
    ) else 4
    image_urls = await collect_vision_image_urls(
        db,
        project_id,
        message_text=text,
        reference_asset_ids=ref_ids,
        canvas_snapshot=canvas_snapshot if isinstance(canvas_snapshot, dict) else None,
        max_images=max_vision,
    )
    if image_urls:
        logger.info(
            "agent_followup: vision refs session=%s images=%s",
            session.id,
            len(image_urls),
        )

    # —— 传话层 → 黑箱编排 ——
    # 用户原话不润色；快照/Skill/视觉仅作上下文。plan 由 agent_canvas_orchestrator 产出。
    from .agent_canvas_orchestrator import CanvasOrchestrateInput, orchestrate_canvas_plan

    orch = await orchestrate_canvas_plan(
        CanvasOrchestrateInput(
            user_text=text,
            graph=graph,
            canvas_snapshot=canvas_snapshot if isinstance(canvas_snapshot, dict) else None,
            preferred_model=get_session_controller_model(session),
            my_skill=bool(my_defaults),
            platform_skill=platform_skill,
            skill_context=skill_ctx or None,
            image_urls=image_urls or None,
            extra_rules_markdown=canvas_rules or None,
            cinematic_phase=cinematic_phase or None,
        )
    )
    plan = orch.plan
    new_graph, ops, notes = _apply_plan_to_graph(
        graph, plan, canvas_snapshot=canvas_snapshot
    )

    # 合并 bind 已写入、尚未投影的参考图 ops（去重 assetId）
    if pending_ref_ops:
        already = _collect_existing_asset_ids(
            canvas_snapshot=canvas_snapshot, plan=plan, ops=ops
        )
        for pop in pending_ref_ops:
            params = pop.get("params") if isinstance(pop.get("params"), dict) else {}
            aid = str(params.get("assetId") or "").strip()
            if aid and aid in already:
                continue
            ops.insert(0, pop)
            if aid:
                already.add(aid)

    # 兜底：参考图未出现在画布 image 节点时补投影
    # 再次 refresh brief，避免与 bind 竞态漏图
    await db.refresh(session)
    brief2 = session.brief_json if isinstance(session.brief_json, dict) else {}
    ref_ids2 = [
        str(x).strip()
        for x in (brief2.get("referenceAssetIds") or ref_ids or [])
        if str(x).strip()
    ]
    ref_notes = _ensure_reference_image_ops(
        ops,
        reference_asset_ids=ref_ids2,
        canvas_snapshot=canvas_snapshot,
        plan=plan,
    )
    notes.extend(ref_notes)

    reply = str(plan.get("reply") or "").strip() or "已处理你的指令。"
    if notes:
        reply = f"{reply}（变更：{'、'.join(notes)}）"

    # 故事板成片类平台配方：读板建 video 后进入「待确认生成」；
    # 若本轮用户已说确认短语，则进入 generating，禁止再追问。
    try:
        from .agent_sessions import _is_product_cinematic_skill, _skill_row_for_session
        from .agent_canvas_orchestrator import user_confirmed_video_generate

        sk = await _skill_row_for_session(db, session)
        if _is_product_cinematic_skill(sk):
            user_ok = user_confirmed_video_generate(text)
            add_videos = (
                plan.get("addVideoNodes") if isinstance(plan.get("addVideoNodes"), list) else []
            )
            has_batch = any(
                isinstance(v, dict)
                and str(
                    (v.get("params") if isinstance(v.get("params"), dict) else {}).get(
                        "cinematicBatchId"
                    )
                    or ""
                ).strip()
                for v in add_videos
            )
            gen_list = plan.get("generate") if isinstance(plan.get("generate"), list) else []
            brief_c = dict(session.brief_json or {})
            prev_phase = str(brief_c.get("cinematicPhase") or "").strip()

            if user_ok and (
                prev_phase == "awaiting_generate_confirm" or has_batch or gen_list
            ):
                # 已确认：进入出片，勿再写「请回复确认生成」
                brief_c["cinematicPhase"] = "generating_videos"
                session.brief_json = brief_c
                session.status = "active"
            elif has_batch and not user_ok:
                brief_c["cinematicPhase"] = "awaiting_generate_confirm"
                session.brief_json = brief_c
                session.status = "awaiting_user"
                if "确认生成" not in reply and "开始生成" not in reply:
                    reply = (
                        reply.rstrip()
                        + "\n\n视频镜头节点已就绪。是否现在开始生成？"
                        "可回复「确认生成 / 开始吧 / 需要生成 / 开始生成」"
                        "（出视频前不扣视频算力）。"
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_followup: cinematic phase update skipped: %s", exc)

    cinematic_awaiting_confirm = session.status == "awaiting_user"

    saved = None
    if ops:
        saved = await project_graphs.save_graph(
            db, project_id, new_graph, canvas_ops=ops, bump_revision=True
        )
    elif notes:
        saved = await project_graphs.save_graph(
            db, project_id, new_graph, canvas_ops=[], bump_revision=True
        )

    if not cinematic_awaiting_confirm:
        session.status = "completed"
    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        agent_role="orchestrator",
        content=reply,
        graph_patch={"revision": (saved or {}).get("revision"), "notes": notes},
    )
    return saved


async def run_session_followup_job(
    session_id: int,
    message: str,
    canvas_snapshot: dict[str, Any] | None = None,
) -> None:
    """后台任务：独立 DB session。"""
    from ..models.database import async_session
    from ..models.user import User as UserModel

    async with async_session() as db:
        try:
            session = (
                await db.execute(
                    select(AgentSession).where(AgentSession.id == int(session_id)).limit(1)
                )
            ).scalar_one_or_none()
            if session is None:
                return
            user = (
                await db.execute(
                    select(UserModel).where(UserModel.id == int(session.user_id)).limit(1)
                )
            ).scalar_one_or_none()
            if user is None:
                return
            await run_session_followup(
                db,
                session=session,
                user=user,
                message=message,
                canvas_snapshot=canvas_snapshot,
            )
            from .agent_session_credits import commit_orchestration_credits

            await commit_orchestration_credits(db, session)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("agent_followup session %s failed: %s", session_id, exc)
            async with async_session() as db2:
                try:
                    session2 = (
                        await db2.execute(
                            select(AgentSession)
                            .where(AgentSession.id == int(session_id))
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if session2 is not None:
                        from .agent_session_credits import release_orchestration_credits

                        await release_orchestration_credits(db2, session2)
                        session2.status = "completed"
                        await agent_sessions.append_message(
                            db2,
                            session2,
                            role="assistant",
                            agent_role="orchestrator",
                            content=f"续聊处理失败：{exc}。你可以再试一次，或换个说法。",
                        )
                        await db2.commit()
                except Exception:  # noqa: BLE001
                    await db2.rollback()


_CHAT_ONLY_SYSTEM = """你是聚梦画布助手。当前为「仅对话」模式：不要改画布、不要输出 JSON，用简洁中文回答用户。
若用户想改镜头/风格/剧本/连线/生成，提示其将生成模式切换为「画布操控」或「智能编排」。"""


async def run_session_chat_only(
    db: AsyncSession,
    *,
    session: AgentSession,
    message: str,
) -> None:
    """仅对话：LLM 纯文本回复，不写 Graph。"""
    text = (message or "").strip()
    if not text:
        return
    reply = "已收到。当前是仅对话模式；若要改画布请切换生成模式。"
    try:
        from .credential_service import refresh_runtime_if_credential_version_changed

        await refresh_runtime_if_credential_version_changed(db)
    except Exception:  # noqa: BLE001
        pass
    model = pick_controller_model(get_session_controller_model(session))
    if model:
        try:
            reply = await chat_completion(
                model,
                [
                    {"role": "system", "content": _CHAT_ONLY_SYSTEM},
                    {"role": "user", "content": text},
                ],
                temperature=0.6,
                max_tokens=1024,
                timeout_s=60.0,
            )
            reply = (reply or "").strip() or reply
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_chat_only LLM failed: %s", exc)
            reply = f"对话暂时失败：{exc}"
    session.status = "completed"
    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        agent_role="orchestrator",
        content=reply,
    )


async def run_session_chat_only_job(session_id: int, message: str) -> None:
    from ..models.database import async_session

    async with async_session() as db:
        try:
            session = (
                await db.execute(
                    select(AgentSession).where(AgentSession.id == int(session_id)).limit(1)
                )
            ).scalar_one_or_none()
            if session is None:
                return
            await run_session_chat_only(db, session=session, message=message)
            from .agent_session_credits import commit_orchestration_credits

            await commit_orchestration_credits(db, session)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("agent_chat_only session %s failed: %s", session_id, exc)
            async with async_session() as db2:
                try:
                    session2 = (
                        await db2.execute(
                            select(AgentSession)
                            .where(AgentSession.id == int(session_id))
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if session2 is not None:
                        from .agent_session_credits import release_orchestration_credits

                        await release_orchestration_credits(db2, session2)
                        session2.status = "completed"
                        await agent_sessions.append_message(
                            db2,
                            session2,
                            role="assistant",
                            agent_role="orchestrator",
                            content=f"对话失败：{exc}",
                        )
                        await db2.commit()
                except Exception:  # noqa: BLE001
                    await db2.rollback()
