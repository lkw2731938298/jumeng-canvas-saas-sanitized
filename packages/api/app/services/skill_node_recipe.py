"""我的 Skill · 画布节点配方（nodeRecipe）。

按 mediaKind 给出推荐节点、连线与规格说明；开跑时由 Agent 确认方案后建节点并填内容。
"""

from __future__ import annotations

import re
from typing import Any

_NODE_KINDS: frozenset[str] = frozenset({"text", "image", "video", "audio"})
_KEY_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,31}$")


def default_specs(media_kind: str) -> dict[str, Any]:
    """按类型给出默认创作规格（页数/风格/尺寸等，写入 SKILL.md 供 AI 遵守）。"""
    kind = str(media_kind or "video").strip().lower()
    if kind == "image":
        return {
            "pageCount": 1,
            "aspectRatio": "9:16",
            "clarity": "1080p",
            "styleHint": "主体清晰、构图完整；按用户描述出一张图",
            "sizeNote": "默认竖屏 9:16；单张图片节点",
        }
    if kind == "text":
        return {
            "sections": ["大纲", "正文", "角色设定"],
            "toneHint": "语气与受众在开跑时与用户确认",
            "lengthHint": "按用户素材长度自适应，可拆多文本节点",
        }
    if kind == "audio":
        return {
            "tracks": ["BGM", "SFX"],
            "moodHint": "先锁定情绪与场景再写提示词",
            "durationHint": "BGM 时长与画面/文案节奏对齐（用户可改）",
        }
    return {
        "clipCount": 4,
        "aspectRatio": "9:16",
        "clarity": "1080p",
        "durationSec": "30",
        "styleHint": "镜头连贯、角色一致；可再进剪辑页成片",
    }


def default_node_recipe(media_kind: str) -> dict[str, Any]:
    """按 mediaKind 返回默认节点配方（AI 失败或未填时兜底）。"""
    kind = str(media_kind or "video").strip().lower()
    specs = default_specs(kind)
    if kind == "text":
        nodes = [
            {
                "key": "source",
                "kind": "text",
                "label": "原始素材",
                "hint": "用户粘贴的原文 / 大纲；AI 开跑后润色但不丢主题",
            },
            {
                "key": "script",
                "kind": "text",
                "label": "文案 / 剧本",
                "hint": "按 Skill 规格扩写的完整文案或分场剧本",
            },
            {
                "key": "characters",
                "kind": "text",
                "label": "角色设定",
                "hint": "人物人设、口吻、关系；供后续节点引用",
            },
        ]
        edges = [
            {"from": "source", "to": "script"},
            {"from": "script", "to": "characters"},
        ]
        order_notes = (
            "编排：原始素材 → 文案/剧本 → 角色设定。"
            "AI 先出文字方案请用户确认，同意后再建节点并填入内容；"
            "用户说生成相关词后再扣费生成文本（若需）。"
        )
    elif kind == "image":
        page_n = max(1, int(specs.get("pageCount") or 1))
        nodes = [
            {
                "key": "brief",
                "kind": "text",
                "label": "画面描述",
                "hint": "要生成的画面主题、主体、风格、构图与禁忌",
            },
        ]
        img_keys: list[str] = []
        for i in range(1, page_n + 1):
            key = "image_1" if page_n == 1 else f"image_{i}"
            img_keys.append(key)
            nodes.append(
                {
                    "key": key,
                    "kind": "image",
                    "label": "生成图" if page_n == 1 else f"生成图{i}",
                    "hint": (
                        "完整生图提示词"
                        + ("" if page_n == 1 else f"（第{i}/{page_n}张）")
                        + f"；画幅{specs.get('aspectRatio')}、{specs.get('clarity')}；"
                        "含主体、场景、光影、构图"
                    ),
                }
            )
        edges = [{"from": "brief", "to": k} for k in img_keys]
        order_notes = (
            f"编排：画面描述 → 图片节点×{page_n}。"
            f"规格：{page_n} 张、{specs.get('aspectRatio')}、"
            f"{specs.get('clarity')}；{specs.get('styleHint')}。"
            "AI 先出画面方案，用户同意后再建节点并写提示词；"
            "完成后询问是否生成，用户说「生成/出图」等再扣算力生图。"
            + (
                "默认一张图；仅当用户明确要求多张/条漫时再增加图片节点。"
                if page_n == 1
                else ""
            )
        )
    elif kind == "audio":
        nodes = [
            {
                "key": "brief",
                "kind": "text",
                "label": "情绪 / 剧本",
                "hint": "情绪、场景、节奏与配乐需求说明",
            },
            {
                "key": "bgm",
                "kind": "audio",
                "label": "BGM",
                "hint": "背景音乐提示词（风格、BPM、乐器）",
            },
            {
                "key": "sfx",
                "kind": "audio",
                "label": "音效",
                "hint": "关键音效提示词（可选多条合并描述）",
            },
        ]
        edges = [
            {"from": "brief", "to": "bgm"},
            {"from": "brief", "to": "sfx"},
        ]
        order_notes = (
            "编排：情绪/剧本 → BGM + 音效节点。"
            "AI 先确认情绪与时长，用户同意后建节点并填提示词；"
            "用户说生成相关词后再扣费生成音频。"
        )
    else:
        clip_n = int(specs.get("clipCount") or 4)
        nodes = [
            {
                "key": "source",
                "kind": "text",
                "label": "创意说明",
                "hint": "用户一句话想法或分场大纲",
            },
            {
                "key": "script",
                "kind": "text",
                "label": "剧本",
                "hint": "分场剧本与对白",
            },
            {
                "key": "characters",
                "kind": "text",
                "label": "角色",
                "hint": "角色外观与身份锁提示",
            },
            *[
                {
                    "key": f"clip_{i}",
                    "kind": "video",
                    "label": f"镜头{i}",
                    "hint": (
                        f"第{i}/{clip_n}镜视频提示词；"
                        f"画幅{specs.get('aspectRatio')}、约{specs.get('durationSec')}秒总片长内分配"
                    ),
                }
                for i in range(1, clip_n + 1)
            ],
        ]
        edges = (
            [{"from": "source", "to": "script"}, {"from": "script", "to": "characters"}]
            + [{"from": "script", "to": f"clip_{i}"} for i in range(1, clip_n + 1)]
            + [{"from": "characters", "to": f"clip_{i}"} for i in range(1, clip_n + 1)]
        )
        order_notes = (
            f"编排：创意说明 → 剧本 → 角色 → 视频镜头×{clip_n}。"
            f"规格：约{specs.get('durationSec')}秒、{specs.get('aspectRatio')}、"
            f"{specs.get('clarity')}。"
            "AI 先出分镜方案请用户确认，同意后建节点填内容；"
            "用户说生成相关词后再扣算力出视频。"
        )
    return {
        "nodes": nodes,
        "edges": edges,
        "orderNotes": order_notes,
        "specs": specs,
    }


def normalize_node_recipe(
    raw: Any, *, media_kind: str | None = None
) -> dict[str, Any]:
    """校验并归一化 nodeRecipe；非法时回退默认配方。"""
    kind = str(media_kind or "video").strip().lower()
    fallback = default_node_recipe(kind)
    if not isinstance(raw, dict):
        return fallback
    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        return fallback
    nodes: list[dict[str, str]] = []
    keys: set[str] = set()
    for item in nodes_raw[:24]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        nkind = str(item.get("kind") or "").strip().lower()
        label = str(item.get("label") or "").strip()[:32]
        hint = str(item.get("hint") or "").strip()[:240]
        if not _KEY_RE.match(key) or key in keys:
            continue
        if nkind not in _NODE_KINDS:
            continue
        if not label:
            label = key
        keys.add(key)
        node: dict[str, str] = {"key": key, "kind": nkind, "label": label}
        if hint:
            node["hint"] = hint
        nodes.append(node)
    if len(nodes) < 1:
        return fallback
    edges: list[dict[str, str]] = []
    edges_raw = raw.get("edges")
    if isinstance(edges_raw, list):
        seen_e: set[tuple[str, str]] = set()
        for e in edges_raw[:48]:
            if not isinstance(e, dict):
                continue
            frm = str(e.get("from") or "").strip()
            to = str(e.get("to") or "").strip()
            if frm not in keys or to not in keys or frm == to:
                continue
            pair = (frm, to)
            if pair in seen_e:
                continue
            seen_e.add(pair)
            edges.append({"from": frm, "to": to})
    order_notes = str(raw.get("orderNotes") or raw.get("order_notes") or "").strip()
    if len(order_notes) < 4:
        order_notes = str(fallback.get("orderNotes") or "")
    specs_raw = raw.get("specs") if isinstance(raw.get("specs"), dict) else {}
    specs = {**default_specs(kind), **{k: v for k, v in specs_raw.items() if v is not None}}
    return {
        "nodes": nodes,
        "edges": edges,
        "orderNotes": order_notes[:1200],
        "specs": specs,
    }


def node_recipe_summary(recipe: dict[str, Any] | None) -> str:
    """卡片摘要：将铺：剧本→角色→分镜×4。"""
    if not isinstance(recipe, dict):
        return ""
    nodes = recipe.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return ""
    labels: list[str] = []
    i = 0
    while i < len(nodes) and len(labels) < 8:
        n = nodes[i]
        if not isinstance(n, dict):
            i += 1
            continue
        label = str(n.get("label") or n.get("key") or "").strip()
        kind = str(n.get("kind") or "")
        base = re.sub(r"\d+$", "", label).strip() or label
        count = 1
        j = i + 1
        while j < len(nodes):
            n2 = nodes[j]
            if not isinstance(n2, dict):
                break
            lab2 = str(n2.get("label") or "").strip()
            base2 = re.sub(r"\d+$", "", lab2).strip() or lab2
            if base2 == base and str(n2.get("kind") or "") == kind:
                count += 1
                j += 1
            else:
                break
        if count > 1:
            labels.append(f"{base}×{count}")
        else:
            labels.append(label or kind)
        i = j if count > 1 else i + 1
    if not labels:
        return ""
    return "节点：" + "→".join(labels)


def recipe_markdown_sections(recipe: dict[str, Any] | None) -> tuple[str, str, str]:
    """返回（推荐节点 Markdown，编排顺序，规格 Markdown）。"""
    r = recipe if isinstance(recipe, dict) else {}
    nodes = r.get("nodes") if isinstance(r.get("nodes"), list) else []
    edges = r.get("edges") if isinstance(r.get("edges"), list) else []
    lines: list[str] = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        label = str(n.get("label") or n.get("key") or f"节点{i+1}")
        kind = str(n.get("kind") or "text")
        hint = str(n.get("hint") or "").strip()
        kind_cn = {
            "text": "文本",
            "image": "图片",
            "video": "视频",
            "audio": "音频",
        }.get(kind, kind)
        line = f"{i+1}. **{label}**（{kind_cn}，key=`{n.get('key')}`）"
        if hint:
            line += f"：{hint}"
        lines.append(line)
    nodes_md = "\n".join(lines) if lines else "- （暂无推荐节点）"
    if edges:
        edge_lines = [
            f"- `{e.get('from')}` → `{e.get('to')}`"
            for e in edges
            if isinstance(e, dict)
        ]
        nodes_md += "\n\n### 连线\n" + ("\n".join(edge_lines) if edge_lines else "- （无）")
    order = (
        str(r.get("orderNotes") or "").strip()
        or "按上方节点顺序在画布从左到右、从上到下排列，并按配方连线。"
    )
    specs = r.get("specs") if isinstance(r.get("specs"), dict) else {}
    spec_lines: list[str] = []
    for k, v in specs.items():
        if v is None or v == "":
            continue
        if isinstance(v, list):
            v = "、".join(str(x) for x in v)
        spec_lines.append(f"- **{k}**：{v}")
    specs_md = "\n".join(spec_lines) if spec_lines else "- （按开跑时与用户确认）"
    return nodes_md, order, specs_md


def _handle_for_kind(kind: str) -> tuple[str, str]:
    """连线默认 sourceHandle / targetHandle。"""
    if kind == "text":
        return "text", "ref_in"
    if kind == "image":
        return "image", "ref_in"
    if kind == "video":
        return "video", "ref_in"
    if kind == "audio":
        return "audio", "ref_in"
    return "text", "ref_in"


def node_recipe_to_canvas_ops(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    """将 nodeRecipe 转为前端可投影的节点 + 连线 ops。"""
    r = normalize_node_recipe(recipe)
    nodes = r.get("nodes") or []
    edges = r.get("edges") or []
    ops: list[dict[str, Any]] = []
    x0, y0 = 80, 100
    gap_x, gap_y = 300, 220
    kind_pos: dict[str, int] = {"text": 0, "image": 0, "video": 0, "audio": 0}
    key_to_kind: dict[str, str] = {}

    for idx, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        key = str(n["key"])
        kind = str(n["kind"])
        label = str(n.get("label") or key)
        hint = str(n.get("hint") or "").strip()
        key_to_kind[key] = kind
        col = {"text": 0, "image": 1, "video": 1, "audio": 2}.get(kind, idx % 3)
        row = kind_pos.get(kind, 0)
        kind_pos[kind] = row + 1
        x = x0 + col * gap_x
        y = y0 + row * gap_y
        if kind == "text":
            ops.append(
                {
                    "op": "add_text_node",
                    "tempId": key,
                    "label": label,
                    "content": hint or "",
                    "x": x,
                    "y": y,
                    "params": {"textPromptKind": "text_script"}
                    if any(k in label for k in ("剧本", "文案", "素材", "说明"))
                    else (
                        {"textPromptKind": "text_subject"}
                        if "角色" in label
                        else {}
                    ),
                }
            )
        elif kind == "image":
            ops.append(
                {
                    "op": "add_image_node",
                    "tempId": key,
                    "label": label,
                    "prompt": hint or "",
                    "x": x,
                    "y": y,
                }
            )
        elif kind == "video":
            ops.append(
                {
                    "op": "add_video_node",
                    "tempId": key,
                    "label": label,
                    "prompt": hint or "",
                    "x": x,
                    "y": y,
                }
            )
        elif kind == "audio":
            ops.append(
                {
                    "op": "add_audio_node",
                    "tempId": key,
                    "label": label,
                    "prompt": hint or "",
                    "x": x,
                    "y": y,
                    "params": {"generationMode": "music"}
                    if "BGM" in label.upper() or "bgm" in key.lower()
                    else {},
                }
            )

    for e in edges:
        if not isinstance(e, dict):
            continue
        frm = str(e.get("from") or "")
        to = str(e.get("to") or "")
        if frm not in key_to_kind or to not in key_to_kind:
            continue
        src_h, _ = _handle_for_kind(key_to_kind[frm])
        ops.append(
            {
                "op": "connect_nodes",
                "source": frm,
                "target": to,
                "sourceHandle": src_h,
                "targetHandle": "ref_in",
            }
        )
    return ops


def format_recipe_assistant_message(
    *,
    skill_title: str,
    recipe: dict[str, Any],
) -> str:
    """建 Session 时的配方摘要（兼容旧路径；新流程走澄清反问）。"""
    summary = node_recipe_summary(recipe) or "推荐节点"
    nodes_md, order, specs_md = recipe_markdown_sections(recipe)
    return (
        f"已打开我的 Skill「{skill_title}」。我会先根据本 Skill 规格给出创作方案，"
        f"请你确认或补充建议；同意后我再新建节点、连线并填入内容。"
        f"节点就绪后我会再问是否生成——你说「生成/出图」等，我才扣算力开跑。\n\n"
        f"**摘要**：{summary}\n\n"
        f"**规格**\n{specs_md}\n\n"
        f"**推荐节点**\n{nodes_md}\n\n"
        f"**编排顺序**\n{order}"
    )
