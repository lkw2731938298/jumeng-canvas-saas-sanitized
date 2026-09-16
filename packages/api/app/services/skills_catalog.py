"""Skill 目录：种子、列表、收藏、我的 Skill。"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.skill import Skill, SkillFavorite
from ..models.user import User
from .skill_docs import (
    ALLOWED_SKILL_DOC_SLUGS,
    build_user_skill_markdown,
    get_skill_doc_overrides,
    load_skill_doc_resolved,
    skill_doc_exists,
)
from .skill_node_recipe import (
    default_node_recipe,
    node_recipe_summary,
    node_recipe_to_canvas_ops,
    normalize_node_recipe,
)
from .skill_runner import resolve_pipeline
from .storage_urls import normalize_browser_storage_url, persistable_media_url

logger = logging.getLogger(__name__)

# 用户另存流水线默认 pipeline（自由创作 Team · 视频）
_USER_FREE_PIPELINE: list[dict[str, str]] = [
    {"agent": "art_director", "action": "set_style"},
    {"agent": "scriptwriter", "action": "expand_script"},
    {"agent": "character_designer", "action": "lock_identity"},
    {"agent": "scene_creator", "action": "build_scene_board"},
    {"agent": "animator", "action": "generate_shot"},
    {"agent": "editor", "action": "assemble_timeline"},
    {"agent": "sound_engineer", "action": "score_and_sfx"},
]

# 生图：默认单张；用户明确要求多张/条漫时再加图节点
_USER_IMAGE_PIPELINE: list[dict[str, str]] = [
    {"agent": "art_director", "action": "set_style"},
    {"agent": "scriptwriter", "action": "expand_script"},
    {"agent": "character_designer", "action": "lock_identity"},
    {"agent": "scene_creator", "action": "build_scene_board"},
    {"agent": "animator", "action": "generate_shot"},
]

# 文本：风格 + 剧本/文案（不出图、不剪辑）
_USER_TEXT_PIPELINE: list[dict[str, str]] = [
    {"agent": "art_director", "action": "set_style"},
    {"agent": "scriptwriter", "action": "expand_script"},
    {"agent": "character_designer", "action": "lock_identity"},
]

# 音频：剧本骨架 + 配乐音效
_USER_AUDIO_PIPELINE: list[dict[str, str]] = [
    {"agent": "art_director", "action": "set_style"},
    {"agent": "scriptwriter", "action": "expand_script"},
    {"agent": "sound_engineer", "action": "score_and_sfx"},
]

# 仅视频成片步骤（生图/文本流水线应剔除）
_VIDEO_ONLY_ACTIONS: frozenset[str] = frozenset({"assemble_timeline", "score_and_sfx"})
# 文本也不要出片分镜
_TEXT_SKIP_ACTIONS: frozenset[str] = frozenset(
    {"generate_shot", "assemble_timeline", "score_and_sfx", "build_scene_board"}
)
_AUDIO_SKIP_ACTIONS: frozenset[str] = frozenset(
    {"generate_shot", "assemble_timeline", "build_scene_board", "lock_identity"}
)

_MEDIA_KINDS: frozenset[str] = frozenset({"image", "video", "text", "audio"})

# 技能包能力对齐（追平平台内置技能包如 product_cinematic_commercial）：
# team=沿用确定性模板投影（默认，兼容存量我的 Skill）；
# canvas_manual=对齐平台技能包的精细画布操控（走 agent_followup JSON plan，而非 Team 模板）
_SKILL_EXECUTION_MODES: frozenset[str] = frozenset({"team", "canvas_manual"})
# 用户自建多文件技能包（package_files）大小上限：数量与总字符数对齐平台包整包注入上限（≤28000）
_PACKAGE_FILES_MAX_COUNT = 8
_PACKAGE_FILES_MAX_TOTAL_CHARS = 28000


def _unsupported_files_from_inputs(inputs: Any) -> list[str]:
    """从 inputs 里读 zip 导入时记录的 unsupportedFiles。"""
    if not isinstance(inputs, list):
        return []
    for item in inputs:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") != "unsupportedFiles":
            continue
        val = item.get("value")
        if isinstance(val, list):
            return [str(x).strip() for x in val if str(x).strip()][:64]
        if isinstance(val, str) and val.strip():
            return [val.strip()]
    return []


# 创建「我的 Skill」时默认写入的技能包参考文件（与平台包结构对齐）
_DEFAULT_PKG_LAYOUT = "references/node-layout.md"
_DEFAULT_PKG_GENERATION = "references/generation.md"
_DEFAULT_PKG_TOOLS = "references/canvas-tools.md"
_DEFAULT_PKG_FLOW = "references/flow.md"


def _normalize_package_files(raw: Any) -> dict[str, str] | None:
    """校验并归一化用户自建技能包的多文件 references（对齐平台 skill_docs 整包注入）。

    仅接受 {"references/xxx.md": "markdown 正文", ...} 形式的字典；相对路径需以
    .md 结尾、不含 .. 或绝对路径前缀（防止越权）；数量与总字符数设上限。
    例外：允许 ``agents/openai.yaml``（Codex 策略 sidecar，非可执行脚本）。
    """
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, str] = {}
    total = 0
    for rel, content in raw.items():
        path = str(rel or "").strip().replace("\\", "/").lstrip("/")
        if not path or ".." in path:
            continue
        lower = path.lower()
        is_openai_yaml = lower == "agents/openai.yaml"
        if not is_openai_yaml and not lower.endswith(".md"):
            continue
        body = str(content or "").strip()
        if not body:
            continue
        if len(out) >= _PACKAGE_FILES_MAX_COUNT:
            break
        total += len(body)
        if total > _PACKAGE_FILES_MAX_TOTAL_CHARS:
            body = body[: max(0, _PACKAGE_FILES_MAX_TOTAL_CHARS - (total - len(body)))]
            if not body:
                break
        out[path] = body
        if total >= _PACKAGE_FILES_MAX_TOTAL_CHARS:
            break
    return out or None


def _normalize_flow_steps(raw: Any) -> list[dict[str, str]]:
    """流程步骤：[{title, note}]，最多 12 步。"""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("label") or "").strip()[:64]
        note = str(item.get("note") or item.get("description") or "").strip()[:500]
        if not title:
            continue
        out.append({"title": title, "note": note})
        if len(out) >= 12:
            break
    return out


def default_flow_steps_for_media(media_kind: str) -> list[dict[str, str]]:
    """按产出类型给出默认可编辑流程（对齐平台技能包「读画布→布点→确认生成」）。"""
    kind = normalize_media_kind(media_kind, fallback="image")
    if kind == "image":
        return [
            {"title": "读画布与素材", "note": "get_canvas_state；复用已有节点；处理附件/准星"},
            {"title": "出方案并确认", "note": "用 ask_user 确认画面内容/风格后再改画布"},
            {"title": "布点连线填词", "note": "按 nodeRecipe add_node + connect_nodes；写入 prompt/content"},
            {"title": "确认后出图", "note": "用户说生成后再 generate_node；默认一张图；禁止未确认出图"},
        ]
    if kind == "text":
        return [
            {"title": "读画布与需求", "note": "读最新画布与用户原文"},
            {"title": "出文案方案", "note": "ask_user 确认结构/语气"},
            {"title": "写入文本节点", "note": "add/update text_input；不扣媒体生成算力"},
            {"title": "按需再生成", "note": "仅当用户明确要求润色/重写时再改"},
        ]
    if kind == "audio":
        return [
            {"title": "读画布与情绪", "note": "锁定情绪、场景、时长"},
            {"title": "确认方案", "note": "ask_user 确认 BGM/音效方向"},
            {"title": "布音频节点", "note": "add audio_input 并写 prompt"},
            {"title": "确认后生成", "note": "用户确认后再 generate_node"},
        ]
    return [
        {"title": "读画布与素材", "note": "get_canvas_state；复用参考图/视频；处理附件/准星"},
        {"title": "出方案并确认", "note": "ask_user 确认镜数/画幅/风格；未确认不 generate 视频"},
        {"title": "布点连线填词", "note": "按 node-layout 建节点与连线；写 cinematicShotIndex 等参数"},
        {"title": "确认后同批出片", "note": "用户确认生成后同批 generate_node；禁止一条条续跑"},
    ]


def build_user_skill_package_files(
    *,
    title: str,
    description: str,
    media_kind: str,
    node_recipe: dict[str, Any] | None,
    flow_steps: list[dict[str, str]] | None = None,
    output_content: str = "",
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """为我的 Skill 生成/补齐技能包参考文件（node-layout / generation / canvas-tools / flow）。

    已有同名文件不覆盖；保证创建后一定是可整包注入的多文件形态。
    """
    kind = normalize_media_kind(media_kind, fallback="image")
    kind_cn = {
        "image": "生图",
        "video": "短视频",
        "text": "文本",
        "audio": "音频",
    }.get(kind, kind)
    recipe = normalize_node_recipe(node_recipe, media_kind=kind)
    nodes = recipe.get("nodes") if isinstance(recipe.get("nodes"), list) else []
    edges = recipe.get("edges") if isinstance(recipe.get("edges"), list) else []
    steps = flow_steps if flow_steps else default_flow_steps_for_media(kind)

    node_lines: list[str] = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            continue
        label = str(n.get("label") or n.get("key") or f"节点{i+1}")
        nkind = str(n.get("kind") or "text")
        hint = str(n.get("hint") or "").strip()
        key = str(n.get("key") or f"n{i+1}")
        type_map = {
            "text": "text_input",
            "image": "image_input",
            "video": "video_input",
            "audio": "audio_input",
        }
        ntype = type_map.get(nkind, "text_input")
        node_lines.append(
            f"| {label} | `{ntype}` | `{key}` | {hint or '按方案填写'} |"
        )
    if not node_lines:
        node_lines.append("| （按类型默认） | — | — | 保存后由默认配方补齐 |")

    edge_lines = [
        f"- `{e.get('from')}` → `{e.get('to')}`（目标 `ref_in`）"
        for e in edges
        if isinstance(e, dict) and e.get("from") and e.get("to")
    ] or ["- （按类型默认连线）"]

    flow_md_lines = [
        f"# 流程设置 · {title or '我的 Skill'}",
        "",
        f"> 产出类型：**{kind_cn}**（`{kind}`）。助手按下列步骤选用工具；可跳步，须先读画布。",
        "",
    ]
    for i, step in enumerate(steps, 1):
        flow_md_lines.append(f"## {i}. {step.get('title') or f'步骤{i}'}")
        flow_md_lines.append("")
        flow_md_lines.append(str(step.get("note") or "按画布现状执行").strip() or "按画布现状执行")
        flow_md_lines.append("")

    layout_md = "\n".join(
        [
            f"# 节点布置 · {title or '我的 Skill'}",
            "",
            f"{(description or '').strip() or '按节点表布点。'}",
            "",
            "## 节点表",
            "",
            "| 名称 | type | key | 填写要点 |",
            "|------|------|-----|----------|",
            *node_lines,
            "",
            "## 连线",
            "",
            *edge_lines,
            "",
            "## 布局建议",
            "",
            "- 相关节点从左到右、从上到下排列，间距约 220，避免重叠；可用 `layout_hint`。",
            "- 能复用已有节点就改参/连线，不要无故新建重复节点。",
            "- 准星点名 +「生成视频」时只出一条，不要按整表铺满（见 generation.md）。",
            "",
        ]
    )

    gen_rules = {
        "image": "用户明确说生成/出图后，对图片节点同批 `generate_node`。",
        "video": "用户说确认生成/出片后，对空视频节点同批 `generate_node`；禁止一条条续跑。",
        "text": "文本以写入节点为主；仅用户要求重写时再改 content。",
        "audio": "用户确认后再对音频节点 `generate_node`。",
    }
    generation_md = "\n".join(
        [
            f"# 生成需求 · {title or '我的 Skill'}",
            "",
            f"**产出**：{kind_cn}",
            "",
            (output_content or "").strip() or "按节点 hint 与方案产出。",
            "",
            "## 何时生成",
            "",
            "1. 先布点、填词、连线；未确认不要扣媒体生成算力。",
            f"2. {gen_rules.get(kind, gen_rules['video'])}",
            "3. 本轮已成功提交生成后收束；用户再说「再生成/重做」才继续。",
            "4. 准星 +「生成视频」= 短出片（只出一条）；「做宣传片/确认生成」走完整流程。",
            "",
        ]
    )

    tools_md = "\n".join(
        [
            f"# 工具对照 · {title or '我的 Skill'}",
            "",
            "| 意图 | 工具 |",
            "|------|------|",
            "| 读画布 | `get_canvas_state` / `inspect_node` |",
            "| 新建节点 | `add_node`（text/image/video/audio_input） |",
            "| 改参数 | `update_node_params` |",
            "| 连参考 | `connect_nodes` → `ref_in` |",
            "| 画布工具 | `run_canvas_tool`（须官方全名） |",
            "| 出片/出图 | `generate_node`（须用户确认） |",
            "| 确认/缺信息 | `ask_user` |",
            "",
            "编排顺序见 `flow.md`；节点细节见 `node-layout.md`。",
            "",
        ]
    )

    built = {
        _DEFAULT_PKG_FLOW: "\n".join(flow_md_lines).strip() + "\n",
        _DEFAULT_PKG_LAYOUT: layout_md.strip() + "\n",
        _DEFAULT_PKG_GENERATION: generation_md.strip() + "\n",
        _DEFAULT_PKG_TOOLS: tools_md.strip() + "\n",
    }
    merged: dict[str, str] = dict(built)
    if isinstance(existing, dict):
        for k, v in existing.items():
            path = str(k or "").strip().replace("\\", "/").lstrip("/")
            body = str(v or "").strip()
            if path and body:
                merged[path] = body if body.endswith("\n") else body + "\n"
    # 流程设置权威：有 flow_steps 时始终重写 flow.md，避免表单步骤与包内文件脱节
    if flow_steps:
        merged[_DEFAULT_PKG_FLOW] = built[_DEFAULT_PKG_FLOW]
    # 保证四个默认文件存在（用户空覆盖不删结构）
    for path, body in built.items():
        if not str(merged.get(path) or "").strip():
            merged[path] = body
    return _normalize_package_files(merged) or built


def _normalize_execution_mode(raw: Any) -> str:
    """技能执行路径：非法/空值一律回退默认 team，保证存量 Skill 行为不变。"""
    mode = str(raw or "").strip().lower()
    return mode if mode in _SKILL_EXECUTION_MODES else "team"


def _normalize_canvas_rules_markdown(raw: Any) -> str | None:
    """技能自定义画布规则覆盖：自由文本，追加进 Agent 画布操控说明书附录。"""
    text_ = str(raw or "").strip()
    return text_[:8000] if text_ else None


# pipeline action → 卡片步骤文案（对用户可读）
_PIPELINE_ACTION_LABELS: dict[str, str] = {
    "set_style": "风格",
    "expand_script": "剧本",
    "lock_identity": "角色",
    "build_scene_board": "场景",
    "generate_shot": "出片",
    "assemble_timeline": "剪辑",
    "score_and_sfx": "配乐",
    "storyboard_from_video": "拉片",
    "extract_subjects": "主体",
    "batch_remake_clips": "成片",
    "storyboard_overseas_localize": "出海拉片",
    "localize_subjects": "本地化主体",
    "batch_overseas_clips": "出海成片",
}

# 意图关键词（一句话创建 / 懒回填）；优先匹配更具体的类型
_IMAGE_MEDIA_HINTS: tuple[str, ...] = (
    "漫画",
    "条漫",
    "插画",
    "绘本",
    "生图",
    "图片",
    "静帧",
    "分镜图",
    "四格",
    "九宫格",
    "comic",
    "manga",
    "illustration",
    "海报",
    "封面",
    "角色立绘",
)
_AUDIO_MEDIA_HINTS: tuple[str, ...] = (
    "配乐",
    "音效",
    "音频",
    "bgm",
    "BGM",
    "语音",
    "旁白",
    "播客",
    "有声",
    "music",
    "sfx",
    "歌曲",
    "作词",
)
_TEXT_MEDIA_HINTS: tuple[str, ...] = (
    "文案",
    "剧本",
    "台词",
    "文本",
    "小说",
    "故事大纲",
    "分场",
    "prompt",
    "提示词",
    "脚本",
    "对白",
    "copywriting",
)

_INPUT_DEFAULTS_NAME = "defaults"


def normalize_media_kind(value: str | None, *, fallback: str = "video") -> str:
    kind = str(value or "").strip().lower()
    return kind if kind in _MEDIA_KINDS else fallback


def infer_media_kind(*texts: str) -> str:
    """从标题/描述/想法推断 mediaKind：image | audio | text | video。"""
    blob = " ".join(str(t or "") for t in texts).lower()
    if any(h.lower() in blob for h in _IMAGE_MEDIA_HINTS):
        return "image"
    if any(h.lower() in blob for h in _AUDIO_MEDIA_HINTS):
        return "audio"
    if any(h.lower() in blob for h in _TEXT_MEDIA_HINTS):
        return "text"
    return "video"


def pipeline_for_media_kind(media_kind: str) -> list[dict[str, str]]:
    """按媒体类型返回默认声明式流水线。"""
    kind = normalize_media_kind(media_kind, fallback="video")
    if kind == "image":
        return [dict(s) for s in _USER_IMAGE_PIPELINE]
    if kind == "text":
        return [dict(s) for s in _USER_TEXT_PIPELINE]
    if kind == "audio":
        return [dict(s) for s in _USER_AUDIO_PIPELINE]
    return [dict(s) for s in _USER_FREE_PIPELINE]


def pipeline_summary(
    pipeline: list[Any] | None,
    *,
    media_kind: str | None = None,
    node_recipe: dict[str, Any] | None = None,
) -> str:
    """卡片摘要：优先 nodeRecipe「将铺：…」，否则回退 pipeline 步骤名。"""
    recipe_sum = node_recipe_summary(node_recipe)
    if recipe_sum:
        return recipe_sum
    kind = str(media_kind or "").strip().lower()
    labels: list[str] = []
    seen: set[str] = set()
    for step in pipeline or []:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip()
        label = _PIPELINE_ACTION_LABELS.get(action) or str(step.get("agent") or "").strip()
        # 按媒体类型润色步骤名
        if action == "generate_shot" and kind == "image":
            label = "出图"
        if action == "expand_script" and kind == "text":
            label = "文案"
        if action == "score_and_sfx" and kind == "audio":
            label = "音频"
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
        if len(labels) >= 6:
            break
    if not labels:
        # 无 pipeline 时按类型给默认配方摘要
        if kind in _MEDIA_KINDS:
            return node_recipe_summary(default_node_recipe(kind))
        return ""
    return "将铺：" + "→".join(labels)


def get_skill_defaults(skill: Skill | None) -> dict[str, Any]:
    """从 Skill.inputs 中读取另存时写入的 defaults 对象。"""
    if skill is None:
        return {}
    for item in skill.inputs or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") != _INPUT_DEFAULTS_NAME:
            continue
        value = item.get("value") if "value" in item else item.get("default")
        return dict(value) if isinstance(value, dict) else {}
    # 兼容：idea 写在独立 input 上
    idea = ""
    for item in skill.inputs or []:
        if isinstance(item, dict) and str(item.get("name") or "") == "idea":
            idea = str(item.get("value") or item.get("default") or "").strip()
            break
    out: dict[str, Any] = {}
    if idea:
        out["idea"] = idea
    if skill.default_style_id:
        out["styleId"] = str(skill.default_style_id).strip()
    return out


def _strip_user_idea_text(raw: str) -> str:
    """去掉附件标记，留下可复用的用户想法正文。"""
    text = str(raw or "").strip()
    text = re.sub(r"\[附件:[^\]]*\]", "", text)
    text = re.sub(r"\[assetId:[^\]]*\]", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    if text.startswith("启动 Skill：") or text.startswith("启动 Skill:"):
        return ""
    return text[:500]


async def _collect_session_defaults(
    db: AsyncSession,
    session: Any,
    graph: dict[str, Any],
) -> dict[str, Any]:
    """从会话消息 / brief / Graph 提取可复用默认（想法、风格、模型、画幅等）。"""
    from ..models.agent_session import AgentSessionMessage
    from .agent_controller import get_session_controller_model

    brief = session.brief_json if isinstance(session.brief_json, dict) else {}
    defaults: dict[str, Any] = {
        "genMode": str(brief.get("genMode") or "smart").strip() or "smart",
    }

    # 首条有意义的用户想法
    rows = (
        await db.execute(
            select(AgentSessionMessage)
            .where(
                AgentSessionMessage.session_id == int(session.id),
                AgentSessionMessage.role == "user",
            )
            .order_by(AgentSessionMessage.seq.asc())
            .limit(8)
        )
    ).scalars().all()
    idea = ""
    for msg in rows:
        idea = _strip_user_idea_text(str(getattr(msg, "content", "") or ""))
        if idea:
            break
    if not idea and isinstance(graph.get("script"), dict):
        idea = str(graph["script"].get("title") or graph["script"].get("logline") or "").strip()
    if idea:
        defaults["idea"] = idea

    style_id = getattr(session, "style_id", None)
    if not style_id and isinstance(graph.get("style"), dict):
        style_id = graph["style"].get("styleId")
    if style_id:
        defaults["styleId"] = str(style_id).strip()

    ctrl = get_session_controller_model(session)
    if ctrl:
        defaults["controllerModel"] = str(ctrl).strip()

    pref_img = str(brief.get("preferredImageModel") or "").strip()
    pref_vid = str(brief.get("preferredVideoModel") or "").strip()
    if not pref_img and isinstance(graph.get("style"), dict):
        pref_img = str(graph["style"].get("imageModel") or "").strip()
    if not pref_vid and isinstance(graph.get("style"), dict):
        pref_vid = str(graph["style"].get("videoModel") or "").strip()
    if pref_img:
        defaults["imageModel"] = pref_img
    if pref_vid:
        defaults["videoModel"] = pref_vid

    vr = brief.get("viralRemake") if isinstance(brief.get("viralRemake"), dict) else {}
    aspect = str(vr.get("aspectRatio") or brief.get("aspectRatio") or "").strip()
    clarity = str(vr.get("clarity") or brief.get("clarity") or "").strip()
    duration = str(
        brief.get("duration") or brief.get("durationSec") or vr.get("duration") or ""
    ).strip()
    # 从镜头 params 兜底画幅/时长
    shots = graph.get("shots") if isinstance(graph.get("shots"), list) else []
    for sh in shots:
        if not isinstance(sh, dict):
            continue
        params = sh.get("params") if isinstance(sh.get("params"), dict) else {}
        opts = params.get("generationOptions") if isinstance(params.get("generationOptions"), dict) else {}
        if not aspect:
            aspect = str(opts.get("ratio") or opts.get("aspectRatio") or "").strip()
        if not clarity:
            clarity = str(opts.get("resolution") or opts.get("clarity") or "").strip()
        if not duration:
            duration = str(opts.get("duration") or "").strip()
        if not pref_vid:
            m = str(params.get("model") or "").strip()
            if m:
                defaults["videoModel"] = m
                pref_vid = m
        if aspect and clarity and duration:
            break
    if aspect:
        defaults["aspectRatio"] = aspect
    if clarity:
        defaults["clarity"] = clarity
    if duration:
        defaults["durationSec"] = duration

    return {k: v for k, v in defaults.items() if v not in (None, "")}

# 平台内置 Skill（幂等种子）；说明正文见 app/skill_docs/{slug}.md
_PLATFORM_SKILL_SEEDS: list[dict[str, Any]] = [
    {
        "slug": "viral_remake",
        "title": "爆款拉片复刻",
        "description": (
            "可选配方：上传参考视频后拉片填分镜表；确认生成再出同款。"
            "成片每镜≥4秒。由画布 AI 助手用工具执行，不再自动拉片。"
        ),
        "category": "通用技能",
        "entry_kind": "viral_remake",
        "cover_url": None,
        "inputs": [
            {"name": "referenceVideo", "type": "video", "required": True},
            {"name": "replaceImages", "type": "image[]", "required": False},
            {"name": "aspectRatio", "type": "string", "required": False},
        ],
        "pipeline": [
            {"agent": "scriptwriter", "action": "storyboard_from_video"},
            {"agent": "character_designer", "action": "extract_subjects"},
            {"agent": "animator", "action": "batch_remake_clips"},
            {"agent": "editor", "action": "assemble_timeline"},
        ],
        "sort_order": 10,
    },
    {
        "slug": "overseas_localize",
        "title": "一键出海",
        "description": (
            "可选配方：参考视频 + 目标市场，本地化分镜与主体后确认再出出海同款。"
            "由画布 AI 助手执行，不再走 5 步向导卡。"
        ),
        "category": "通用技能",
        "entry_kind": "overseas_localize",
        "cover_url": (
            "https://images.unsplash.com/photo-1488646953014-85cb44e25828"
            "?auto=format&fit=crop&w=500&q=85"
        ),
        "inputs": [
            {"name": "referenceVideo", "type": "video", "required": True},
            {"name": "targetMarket", "type": "string", "required": True},
        ],
        "pipeline": [
            {"agent": "scriptwriter", "action": "storyboard_overseas_localize"},
            {"agent": "character_designer", "action": "localize_subjects"},
            {"agent": "animator", "action": "batch_overseas_clips"},
            {"agent": "editor", "action": "assemble_timeline"},
        ],
        "sort_order": 20,
    },
    {
        "slug": "product_cinematic_commercial",
        "title": "单一产品电影级宣传片",
        "description": (
            "可选配方：产品参考图 → 故事板合成图 → 读板建视频节点；确认后再出视频。"
            "禁止分镜表与定妆图。由画布 AI 助手用工具执行。"
        ),
        "category": "商业广告",
        # 平台技能包：文档见 skill_docs/{slug}/SKILL.md，运行时整包注入 agent_runtime
        "entry_kind": "product_cinematic_commercial",
        "cover_url": (
            "https://images.unsplash.com/photo-1492691527719-9d1e07e534b4"
            "?auto=format&fit=crop&w=500&q=85"
        ),
        "inputs": [
            {"name": "productName", "type": "string", "required": True},
            {"name": "sellingPoints", "type": "string", "required": False},
            {"name": "referenceImages", "type": "image[]", "required": False},
            {"name": "referenceVideo", "type": "video", "required": False},
            {"name": "aspectRatio", "type": "string", "required": False},
            {"name": "durationSec", "type": "string", "required": False},
        ],
        "pipeline": [
            {"agent": "art_director", "action": "set_style"},
            {"agent": "scriptwriter", "action": "expand_script"},
            {"agent": "character_designer", "action": "lock_identity"},
            {"agent": "scene_creator", "action": "build_scene_board"},
            {"agent": "animator", "action": "generate_shot"},
            {"agent": "editor", "action": "assemble_timeline"},
            {"agent": "sound_engineer", "action": "score_and_sfx"},
        ],
        "sort_order": 30,
    },
    {
        # 第一视角催泪短片：文档见 skill_docs/pov_tearjerker_short/SKILL.md
        "slug": "pov_tearjerker_short",
        "title": "第一视角催泪短片导演",
        "description": (
            "可选配方：先写细腻剧本（可丰富台词）→ 定 N 镜/多故事备选并询问 → "
            "确认后出板出片；优先单 video 节点出全片，禁整板并行多镜；可做长视频。"
        ),
        "category": "剧情短片",
        "entry_kind": "pov_tearjerker_short",
        "cover_url": (
            "https://images.unsplash.com/photo-1478144592103-25e218a04891"
            "?auto=format&fit=crop&w=500&q=85"
        ),
        "inputs": [
            {"name": "relationship", "type": "string", "required": True},
            {"name": "emotionalTheme", "type": "string", "required": True},
            {"name": "referenceImages", "type": "image[]", "required": False},
            {"name": "aspectRatio", "type": "string", "required": False},
            {"name": "durationSec", "type": "string", "required": False},
        ],
        "pipeline": [
            {"agent": "orchestrator", "action": "analyze_brief"},
            {"agent": "orchestrator", "action": "write_script"},
            {"agent": "orchestrator", "action": "lock_shots_and_ask"},
            {"agent": "orchestrator", "action": "storyboard"},
            {"agent": "orchestrator", "action": "layout_and_ask"},
            {"agent": "orchestrator", "action": "batch_generate"},
        ],
        "sort_order": 40,
    },
]


def public_owner_label(user: User | None) -> str | None:
    """社区 Skill 卡片展示用作者名：优先 display_name，否则手机尾号。"""
    if user is None:
        return None
    name = str(getattr(user, "display_name", None) or "").strip()
    if name:
        return name[:32]
    phone = str(getattr(user, "phone", None) or "").strip()
    if len(phone) >= 4:
        return f"用户{phone[-4:]}"
    return f"用户{int(user.id)}"


async def load_owner_labels(
    db: AsyncSession, owner_ids: set[int]
) -> dict[int, str]:
    """批量查作者展示名，避免列表 N+1。"""
    if not owner_ids:
        return {}
    rows = list(
        (await db.execute(select(User).where(User.id.in_(list(owner_ids))))).scalars().all()
    )
    out: dict[int, str] = {}
    for u in rows:
        label = public_owner_label(u)
        if label:
            out[int(u.id)] = label
    return out


def skill_to_dict(
    skill: Skill,
    *,
    favorited: bool = False,
    doc: dict[str, Any] | None = None,
    owner_display_name: str | None = None,
) -> dict[str, Any]:
    """序列化 Skill（驼峰字段给前端）；附带 SKILL.md 与 Runner 解析结果。

    doc 可由调用方传入（含库覆盖）；未传则同步读文件默认。
    """
    if doc is None:
        from .skill_docs import load_skill_doc

        doc = load_skill_doc(skill.slug)
    resolved = resolve_pipeline(skill, doc=doc)
    pipeline = resolved.get("pipeline") or skill.pipeline or []
    defaults = get_skill_defaults(skill)
    media_kind = str(defaults.get("mediaKind") or "").strip().lower()
    if media_kind not in _MEDIA_KINDS and skill.owner_user_id is not None:
        media_kind = infer_media_kind(
            skill.title, skill.description, defaults.get("idea")
        )
        if media_kind:
            defaults = {**defaults, "mediaKind": media_kind}
    # 我的 Skill：保证 defaults 带可铺节点的 nodeRecipe
    recipe = None
    if skill.owner_user_id is not None:
        recipe = normalize_node_recipe(
            defaults.get("nodeRecipe"),
            media_kind=media_kind or "video",
        )
        defaults = {**defaults, "nodeRecipe": recipe, "mediaKind": media_kind or defaults.get("mediaKind")}
    summary = pipeline_summary(
        pipeline if isinstance(pipeline, list) else [],
        media_kind=media_kind or None,
        node_recipe=recipe,
    )
    execution = resolved.get("execution")
    # 我的 Skill：Agent 方案确认 → 建节点 → 用户同意后再生成
    if skill.owner_user_id is not None:
        execution = "server_team"
    return {
        "id": str(skill.id),
        "slug": skill.slug,
        "title": skill.title,
        "description": skill.description or "",
        "category": skill.category,
        "visibility": skill.visibility,
        "ownerUserId": str(skill.owner_user_id) if skill.owner_user_id else None,
        # 社区 Skill 卡片展示作者昵称（平台 Skill 为 null）
        "ownerDisplayName": owner_display_name
        if skill.owner_user_id is not None
        else None,
        # 封面按当前 STORAGE_URL_MODE 重签（cdn 即稳定 CDN URL）
        "coverUrl": (
            normalize_browser_storage_url(skill.cover_url) if skill.cover_url else None
        ),
        "entryKind": skill.entry_kind,
        "inputs": skill.inputs or [],
        # 对外暴露 Runner 解析后的步骤（含 trigger/clientAction）
        "pipeline": pipeline,
        "pipelineSource": resolved.get("source"),
        "pipelineSummary": summary,
        "mediaKind": media_kind or None,
        "execution": execution,
        "nodeRecipe": recipe,
        # 技能包能力对齐：执行模式（team|canvas_manual）/ 画布规则覆盖 / 多文件参考包
        "executionMode": str(getattr(skill, "execution_mode", None) or "team"),
        "canvasRulesMarkdown": str(getattr(skill, "canvas_rules_markdown", None) or ""),
        "packageFiles": dict(getattr(skill, "package_files", None) or {}),
        "unsupportedFiles": _unsupported_files_from_inputs(skill.inputs),
        "defaults": defaults,
        "defaultStyleId": skill.default_style_id,
        "pricingHint": skill.pricing_hint,
        "version": int(skill.version or 1),
        "sortOrder": int(skill.sort_order or 0),
        "status": skill.status,
        # 社区发布审核态（平台 Skill 多为 none）
        "reviewStatus": str(getattr(skill, "review_status", None) or "none"),
        "reviewNote": str(getattr(skill, "review_note", None) or "") or None,
        "reviewedAt": to_cst_iso(getattr(skill, "reviewed_at", None))
        if getattr(skill, "reviewed_at", None)
        else None,
        "reviewedBy": str(skill.reviewed_by) if getattr(skill, "reviewed_by", None) else None,
        "reviewHistory": _normalize_review_history(getattr(skill, "review_history", None)),
        "displayStatus": skill_display_status(skill),
        "favorited": favorited,
        # 平台内置 slug / 行内 MD / 文件 MD 任一存在即可预览
        "hasSkillDoc": bool(str(getattr(skill, "doc_markdown", None) or "").strip())
        or bool(doc)
        or skill_doc_exists(skill.slug)
        or skill.slug in ALLOWED_SKILL_DOC_SLUGS
        or skill.visibility == "public",
        "skillDocSummary": (doc or {}).get("summary") or "",
        "skillDocSource": (doc or {}).get("source")
        or ("db" if getattr(skill, "doc_markdown", None) else ("file" if doc else None)),
        "createdAt": to_cst_iso(skill.created_at) if skill.created_at else None,
        "updatedAt": to_cst_iso(skill.updated_at) if skill.updated_at else None,
    }


async def skill_to_dict_resolved(
    db: AsyncSession,
    skill: Skill,
    *,
    favorited: bool = False,
    overrides: dict[str, str] | None = None,
    owner_display_name: str | None = None,
) -> dict[str, Any]:
    """带 DB 会话：读库覆盖 / 行内 doc_markdown / 文件后再序列化。"""
    doc = await load_skill_doc_resolved(
        db, skill.slug, overrides=overrides, skill_row=skill
    )
    label = owner_display_name
    # 未批量预取时按需查作者名（单条详情 / 管理路径）
    if label is None and skill.owner_user_id is not None:
        owner = (
            await db.execute(
                select(User).where(User.id == int(skill.owner_user_id)).limit(1)
            )
        ).scalar_one_or_none()
        label = public_owner_label(owner)
    return skill_to_dict(
        skill, favorited=favorited, doc=doc, owner_display_name=label
    )


def _assign_skill_doc_markdown(
    skill: Skill,
    *,
    defaults: dict[str, Any],
    pipeline: list[Any] | None,
    doc_markdown_override: str | None = None,
) -> None:
    """根据当前标题/步骤/defaults 重写 SKILL.md；可传入完整 MD 覆盖。"""
    override = str(doc_markdown_override or "").strip()
    if override:
        skill.doc_markdown = override if override.endswith("\n") else override + "\n"
        return
    skill.doc_markdown = build_user_skill_markdown(
        slug=skill.slug,
        title=skill.title or "我的 Skill",
        description=skill.description or "",
        pipeline=pipeline if isinstance(pipeline, list) else (skill.pipeline or []),
        defaults=defaults,
        media_kind=str((defaults or {}).get("mediaKind") or "") or None,
        use_cases=str((defaults or {}).get("useCases") or "") or None,
        how_to_use=str((defaults or {}).get("howToUse") or "") or None,
        output_content=str((defaults or {}).get("outputContent") or "") or None,
    )


async def ensure_platform_skills(db: AsyncSession) -> int:
    """启动时幂等写入平台 Skill；返回新增条数。"""
    added = 0
    for seed in _PLATFORM_SKILL_SEEDS:
        slug = seed["slug"]
        row = (
            await db.execute(select(Skill).where(Skill.slug == slug).limit(1))
        ).scalar_one_or_none()
        if row is None:
            db.add(
                Skill(
                    slug=slug,
                    title=seed["title"],
                    description=seed.get("description"),
                    category=seed.get("category") or "通用技能",
                    visibility="public",
                    owner_user_id=None,
                    cover_url=seed.get("cover_url"),
                    entry_kind=seed.get("entry_kind"),
                    inputs=seed.get("inputs"),
                    pipeline=seed.get("pipeline"),
                    pricing_hint="per_pipeline",
                    version=1,
                    sort_order=int(seed.get("sort_order") or 0),
                    status="active",
                )
            )
            added += 1
            continue
        # 已有行：同步展示字段（不覆盖用户私有 Skill）；pipeline 优先从 MD 同步
        if row.owner_user_id is None:
            row.title = seed["title"]
            row.description = seed.get("description")
            row.category = seed.get("category") or row.category
            # 封面：仅库为空时用种子补齐，禁止启动同步冲掉管理端上传的 CDN 封面
            if not str(row.cover_url or "").strip():
                row.cover_url = seed.get("cover_url") or None
            row.entry_kind = seed.get("entry_kind") or row.entry_kind
            row.inputs = seed.get("inputs") or row.inputs
            # MD YAML 为声明权威时覆盖 DB pipeline
            from .skill_runner import pipeline_for_seed_sync

            md_steps = pipeline_for_seed_sync(slug)
            row.pipeline = md_steps or seed.get("pipeline") or row.pipeline
            row.sort_order = int(seed.get("sort_order") or row.sort_order or 0)
            row.status = "active"
            row.visibility = "public"
            row.updated_at = now_cst_naive()
    if added:
        await db.flush()
    return added


async def get_skill_by_slug(db: AsyncSession, slug: str) -> Skill | None:
    s = (slug or "").strip()
    if not s:
        return None
    return (
        await db.execute(select(Skill).where(Skill.slug == s).limit(1))
    ).scalar_one_or_none()


async def require_skill_by_slug(
    db: AsyncSession,
    slug: str,
    *,
    user: User | None = None,
) -> Skill:
    """公开 Skill 任意可读；私有 Skill 仅所有者可读。"""
    skill = await get_skill_by_slug(db, slug)
    if skill is None or skill.status != "active":
        fail(ErrorCode.SKILL_NOT_FOUND, message="Skill 不存在或已下架")
    if skill.visibility == "private":
        if user is None or skill.owner_user_id is None or int(skill.owner_user_id) != int(user.id):
            fail(ErrorCode.SKILL_NOT_FOUND, message="Skill 不存在或已下架")
    return skill


def _slugify_user_title(title: str, user_id: int) -> str:
    base = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", (title or "").strip().lower())
    base = re.sub(r"-+", "-", base).strip("-")[:24] or "skill"
    # slug 仅 ASCII：中文标题用 hex 短码
    ascii_part = re.sub(r"[^a-z0-9-]+", "", base)[:16] or "mine"
    return f"u{user_id}-{ascii_part}-{uuid.uuid4().hex[:8]}"


def _synthesize_legacy_defaults(skill: Skill) -> dict[str, Any]:
    """历史我的 Skill 无 defaults 时，用标题/描述拼可开跑想法。"""
    existing = get_skill_defaults(skill)
    if existing.get("idea"):
        return existing
    idea = (skill.title or "我的流水线").strip()
    desc = str(skill.description or "").strip()
    for prefix in ("从会话另存：", "从会话另存:"):
        if desc.startswith(prefix):
            rest = desc[len(prefix) :].strip()
            if rest:
                idea = rest
            break
    out: dict[str, Any] = {"idea": idea[:500], "genMode": "smart"}
    if skill.default_style_id:
        out["styleId"] = str(skill.default_style_id).strip()
    out.update({k: v for k, v in existing.items() if v})
    return out


def _build_my_skill_inputs(
    *,
    defaults: dict[str, Any],
    session_id: str,
    idea_fallback: str,
) -> list[dict[str, Any]]:
    idea = str(defaults.get("idea") or idea_fallback).strip()
    items: list[dict[str, Any]] = [
        {
            "name": "idea",
            "type": "string",
            "required": True,
            "default": idea,
        },
    ]
    # 会话另存才写 sourceSessionId；一句话创建可写 fromPrompt 标记
    sid = str(session_id or "").strip()
    if sid:
        items.append(
            {
                "name": "sourceSessionId",
                "type": "string",
                "required": False,
                "value": sid,
            }
        )
    items.append(
        {
            "name": _INPUT_DEFAULTS_NAME,
            "type": "object",
            "value": defaults,
        }
    )
    return items


def _apply_media_kind_to_defaults_and_pipeline(
    skill: Skill,
    defaults: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, str]], bool]:
    """按 mediaKind 校正 defaults / pipeline；返回 (defaults, pipeline, changed)。"""
    changed = False
    out = dict(defaults or {})
    media = str(out.get("mediaKind") or "").strip().lower()
    if media not in _MEDIA_KINDS:
        media = infer_media_kind(skill.title, skill.description, out.get("idea"))
        out["mediaKind"] = media
        changed = True
    skip_actions: frozenset[str] = frozenset()
    if media == "image":
        skip_actions = _VIDEO_ONLY_ACTIONS
    elif media == "text":
        skip_actions = _TEXT_SKIP_ACTIONS
    elif media == "audio":
        skip_actions = _AUDIO_SKIP_ACTIONS
    pipe_raw = list(skill.pipeline or [])
    pipe: list[dict[str, str]] = []
    for step in pipe_raw:
        if not isinstance(step, dict):
            continue
        action = str(step.get("action") or "").strip()
        agent = str(step.get("agent") or "").strip()
        if not action or not agent:
            continue
        if action in skip_actions:
            changed = True
            continue
        pipe.append({"agent": agent, "action": action})
    expected = {s["action"] for s in pipeline_for_media_kind(media)}
    have = {s["action"] for s in pipe}
    if not pipe or not expected.issubset(have):
        # 缺关键步骤时重置为该类型默认流水线
        if media in ("image", "text", "audio") or not pipe:
            pipe = pipeline_for_media_kind(media)
            changed = True
    if media != "video" and out.get("durationSec"):
        out.pop("durationSec", None)
        changed = True
    if media == "video" and not pipe:
        pipe = pipeline_for_media_kind("video")
        changed = True
    # 非视频文档若仍混入成片/剪辑口吻则重写
    doc = str(getattr(skill, "doc_markdown", None) or "")
    if media == "image" and (
        "score_and_sfx" in doc or "assemble_timeline" in doc or "总时长" in doc
    ):
        changed = True
    if media == "text" and ("generate_shot" in doc or "assemble_timeline" in doc):
        changed = True
    if media == "audio" and "assemble_timeline" in doc and "score_and_sfx" not in doc:
        changed = True
    return out, pipe, changed


async def ensure_my_skill_defaults(db: AsyncSession, skill: Skill) -> bool:
    """列表时懒回填 defaults / SKILL.md / 生图流水线校正；有写入返回 True。"""
    if skill.owner_user_id is None:
        return False
    cur = get_skill_defaults(skill)
    has_defaults_obj = any(
        isinstance(x, dict) and str(x.get("name") or "") == _INPUT_DEFAULTS_NAME
        for x in (skill.inputs or [])
    )
    needs_inputs = not (has_defaults_obj and bool(cur.get("idea")))
    needs_doc = not str(getattr(skill, "doc_markdown", None) or "").strip()
    media_defaults, media_pipe, needs_media_fix = _apply_media_kind_to_defaults_and_pipeline(
        skill, cur if cur else _synthesize_legacy_defaults(skill)
    )
    if not needs_inputs and not needs_doc and not needs_media_fix:
        return False
    synthesized = _synthesize_legacy_defaults(skill)
    # 尝试从 sourceSessionId 再拉一版（失败则用 synthesized）
    source_id = ""
    for item in skill.inputs or []:
        if isinstance(item, dict) and str(item.get("name") or "") == "sourceSessionId":
            source_id = str(item.get("value") or "").strip()
            break
    if source_id and source_id not in ("from-prompt", "0"):
        try:
            from ..models.agent_session import AgentSession
            from . import project_graphs

            # 仅所有者列表路径调用；按 sourceSessionId 尽量回填真实 defaults
            session = (
                await db.execute(
                    select(AgentSession)
                    .where(AgentSession.id == int(source_id))
                    .limit(1)
                )
            ).scalar_one_or_none()
            if session is not None and int(session.user_id) == int(skill.owner_user_id or 0):
                graph_payload = await project_graphs.get_graph_dict(
                    db, int(session.project_id)
                )
                graph = (
                    (graph_payload or {}).get("graph")
                    if isinstance(graph_payload, dict)
                    else {}
                )
                if not isinstance(graph, dict):
                    graph = {}
                collected = await _collect_session_defaults(db, session, graph)
                if collected.get("idea"):
                    synthesized = {**synthesized, **collected}
        except Exception:  # noqa: BLE001
            pass
    if not synthesized.get("idea"):
        synthesized["idea"] = (skill.title or "我的流水线").strip()
    # 合并媒体校正（保留已有 idea / 模型等）
    synthesized = {**synthesized, **media_defaults}
    synthesized, media_pipe, _ = _apply_media_kind_to_defaults_and_pipeline(
        skill, synthesized
    )
    if needs_inputs or needs_media_fix:
        skill.inputs = _build_my_skill_inputs(
            defaults=synthesized,
            session_id=source_id or "0",
            idea_fallback=skill.title or "我的流水线",
        )
        if synthesized.get("styleId") and not skill.default_style_id:
            skill.default_style_id = str(synthesized["styleId"])
        if media_pipe:
            skill.pipeline = media_pipe
    if needs_doc or needs_media_fix:
        _assign_skill_doc_markdown(
            skill,
            defaults=get_skill_defaults(skill) or synthesized,
            pipeline=list(skill.pipeline or media_pipe or []),
        )
    skill.updated_at = now_cst_naive()
    await db.flush()
    return True


async def list_my_skills(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """当前用户私有 Skill；顺带懒回填旧卡 defaults。"""
    q = (
        select(Skill)
        .where(
            Skill.owner_user_id == int(user.id),
            Skill.status == "active",
        )
        .order_by(Skill.updated_at.desc(), Skill.id.desc())
    )
    rows = list((await db.execute(q)).scalars().all())
    for row in rows:
        await ensure_my_skill_defaults(db, row)
    overrides = await get_skill_doc_overrides(db)
    return [
        await skill_to_dict_resolved(db, r, favorited=False, overrides=overrides)
        for r in rows
    ]


# 一句话 AI 创建 Skill：允许的 pipeline action
_ACTION_TO_STEP: dict[str, dict[str, str]] = {
    str(s["action"]): dict(s)
    for s in (
        *_USER_FREE_PIPELINE,
        *_USER_IMAGE_PIPELINE,
        *_USER_TEXT_PIPELINE,
        *_USER_AUDIO_PIPELINE,
    )
    if s.get("action")
}
_ALLOWED_VIDEO_ACTIONS: tuple[str, ...] = tuple(
    str(s.get("action") or "") for s in _USER_FREE_PIPELINE if s.get("action")
)
_ALLOWED_IMAGE_ACTIONS: tuple[str, ...] = tuple(
    str(s.get("action") or "") for s in _USER_IMAGE_PIPELINE if s.get("action")
)
_ALLOWED_TEXT_ACTIONS: tuple[str, ...] = tuple(
    str(s.get("action") or "") for s in _USER_TEXT_PIPELINE if s.get("action")
)
_ALLOWED_AUDIO_ACTIONS: tuple[str, ...] = tuple(
    str(s.get("action") or "") for s in _USER_AUDIO_PIPELINE if s.get("action")
)

_CREATE_SKILL_SYSTEM = """你是聚梦画布的 Skill 设计师。用户描述想要的创作技能。
「我的 Skill」开跑流程：用户输入→AI 出方案并询问→用户同意→AI 建节点/连线/填内容→再问是否生成→用户说生成相关词后才扣算力生成。
你必须按用户指定的 mediaKind（若已指定）设计节点，不得擅自改成其它大类。
输出严格 JSON（不要 Markdown 代码围栏）：
{
  "title": "技能短标题（不超过16字）",
  "description": "一句话说明（不超过80字）",
  "idea": "默认可执行创意种子（80～200字）",
  "mediaKind": "image|video|text|audio",
  "aspectRatio": "9:16|16:9|1:1",
  "durationSec": "仅 video：15/30/45/60；其它类型空字符串",
  "clarity": "image/video 填 720p 或 1080p；其它空字符串",
  "useCases": "使用场景（中文，4～8句，写清适用什么素材）",
  "howToUse": "如何使用（编号步骤，须含：选技能→输入→确认方案→AI建节点→确认生成）",
  "outputContent": "输出内容（详细：页数/镜头数、风格、尺寸、每个节点应产出什么）",
  "nodeRecipe": {
    "nodes": [{"key":"source","kind":"text","label":"原始文本","hint":"该节点应写什么，尽量具体"}],
    "edges": [{"from":"brief","to":"image_1"}],
    "orderNotes": "编排顺序与 AI 操控要点（中文，3～6句）",
    "specs": {"pageCount":1,"aspectRatio":"9:16","clarity":"1080p","styleHint":"..."}
  },
  "executionMode": "canvas_manual（我的技能默认按技能包精细画布操控；简单配方也可填 team）",
  "canvasRulesMarkdown": "该技能对 AI 操控画布的专属规则（可选，简短中文；不需要则空字符串）",
  "flowSteps": [{"title":"读画布与素材","note":"get_canvas_state；处理附件/准星"},{"title":"出方案并确认","note":"ask_user"},{"title":"布点连线填词","note":"add_node + connect_nodes"},{"title":"确认后同批出片","note":"用户确认后再 generate_node"}],
  "packageFiles": {
    "references/flow.md": "流程说明（可与 flowSteps 一致）",
    "references/node-layout.md": "节点表与连线（必填级详细）",
    "references/generation.md": "何时 generate、短出片 vs 确认出片",
    "references/canvas-tools.md": "工具对照表"
  }
}
类型与节点硬性要求：
- image：画面描述 text + image×1（默认单张）；仅用户明确要多张/条漫时 pageCount>1；specs 含 pageCount/aspectRatio/clarity/styleHint
- text：原始素材 + 文案/剧本 + 角色；specs 含 sections/toneHint
- audio：情绪文本 + BGM + 音效；specs 含 tracks/moodHint
- video：创意/剧本/角色 + video 镜头×3～6；specs 含 clipCount/durationSec/aspectRatio
必须输出 flowSteps（3～6 步）与 packageFiles 四个 references 文件，便于整包注入。
hint 要写清该节点生成/填写的具体内容，便于 AI 操控画布。
不要输出 pipelineActions。"""


def _parse_llm_json_obj(text: str) -> dict[str, Any] | None:
    """解析 LLM 返回的 JSON 对象（容忍围栏）。"""
    import json

    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw.strip())
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


def _template_skill_from_prompt(prompt: str) -> dict[str, Any]:
    """无 LLM 时的确定性 Skill 草稿。"""
    idea = (prompt or "").strip()
    title = idea[:16] if len(idea) <= 16 else (idea[:15] + "…")
    media = infer_media_kind(idea)
    if media == "image":
        return {
            "title": title or "我的 Skill",
            "description": f"根据「{idea[:40]}」生成单张插画/视觉图",
            "idea": idea[:500] or "生图创作",
            "mediaKind": "image",
            "aspectRatio": "9:16",
            "durationSec": "",
            "clarity": "1080p",
            "pipelineActions": list(_ALLOWED_IMAGE_ACTIONS),
            "nodeRecipe": default_node_recipe("image"),
        }
    if media == "text":
        return {
            "title": title or "我的 Skill",
            "description": f"根据「{idea[:40]}」用文本节点写文案/剧本",
            "idea": idea[:500] or "文案创作",
            "mediaKind": "text",
            "aspectRatio": "9:16",
            "durationSec": "",
            "clarity": "",
            "pipelineActions": list(_ALLOWED_TEXT_ACTIONS),
            "nodeRecipe": default_node_recipe("text"),
        }
    if media == "audio":
        return {
            "title": title or "我的 Skill",
            "description": f"根据「{idea[:40]}」用音频节点规划配乐音效",
            "idea": idea[:500] or "音频创作",
            "mediaKind": "audio",
            "aspectRatio": "9:16",
            "durationSec": "",
            "clarity": "",
            "pipelineActions": list(_ALLOWED_AUDIO_ACTIONS),
            "nodeRecipe": default_node_recipe("audio"),
        }
    return {
        "title": title or "我的 Skill",
        "description": f"根据「{idea[:40]}」用镜头节点创作短片",
        "idea": idea[:500] or "短片创作",
        "mediaKind": "video",
        "aspectRatio": "9:16",
        "durationSec": "30",
        "clarity": "1080p",
        "pipelineActions": list(_ALLOWED_VIDEO_ACTIONS),
        "nodeRecipe": default_node_recipe("video"),
    }


def _normalize_created_skill_draft(
    raw: dict[str, Any],
    prompt: str,
    *,
    force_media_kind: str | None = None,
) -> dict[str, Any]:
    """校验并归一化 AI / 模板生成的 Skill 字段。"""
    fallback = _template_skill_from_prompt(prompt)
    title = str(raw.get("title") or "").strip()[:64] or fallback["title"]
    description = str(raw.get("description") or "").strip()[:200] or fallback["description"]
    idea = str(raw.get("idea") or "").strip()[:500] or (prompt.strip()[:500] or fallback["idea"])
    forced = str(force_media_kind or "").strip().lower()
    if forced in _MEDIA_KINDS:
        media = forced
    else:
        media = normalize_media_kind(
            str(raw.get("mediaKind") or ""),
            fallback=infer_media_kind(prompt, title, description, idea),
        )
    aspect = str(raw.get("aspectRatio") or "").strip()
    if aspect not in ("9:16", "16:9", "1:1"):
        aspect = "9:16"
    duration = str(raw.get("durationSec") or "").strip()
    if media != "video":
        duration = ""
    elif duration not in ("15", "30", "45", "60"):
        duration = "30"
    clarity = str(raw.get("clarity") or "").strip()
    if media in ("text", "audio"):
        clarity = ""
    elif clarity not in ("720p", "1080p", ""):
        clarity = "1080p"
    allowed_map = {
        "image": _ALLOWED_IMAGE_ACTIONS,
        "text": _ALLOWED_TEXT_ACTIONS,
        "audio": _ALLOWED_AUDIO_ACTIONS,
        "video": _ALLOWED_VIDEO_ACTIONS,
    }
    allowed = set(allowed_map[media])
    actions_raw = raw.get("pipelineActions")
    actions: list[str] = []
    if isinstance(actions_raw, list):
        for a in actions_raw:
            key = str(a or "").strip()
            if key in allowed and key not in actions:
                actions.append(key)
    need_shot = media == "video" or media == "image"
    if "expand_script" not in actions or (need_shot and "generate_shot" not in actions) or len(actions) < 2:
        actions = list(allowed_map[media])
    # 描述避免与媒体类型口吻冲突
    if media == "image" and ("短片" in description or "成片视频" in description):
        description = f"根据想法用图片节点生图：{title}"[:200]
    if media == "text" and ("短片" in description or "视频" in description):
        description = f"根据想法用文本节点创作：{title}"[:200]
    if media == "audio" and ("短片成片" in description or "条漫" in description):
        description = f"根据想法用音频节点创作：{title}"[:200]
    recipe = normalize_node_recipe(
        raw.get("nodeRecipe") or fallback.get("nodeRecipe"),
        media_kind=media,
    )
    use_cases = str(raw.get("useCases") or "").strip()[:8000]
    how_to = str(raw.get("howToUse") or "").strip()[:8000]
    output = str(raw.get("outputContent") or "").strip()[:8000]
    # 技能包能力对齐：执行模式 / 画布规则覆盖 / 多文件参考包（均可选，缺省不影响存量行为）
    execution_mode = _normalize_execution_mode(raw.get("executionMode"))
    canvas_rules_markdown = _normalize_canvas_rules_markdown(raw.get("canvasRulesMarkdown"))
    package_files = _normalize_package_files(raw.get("packageFiles"))
    flow_steps = _normalize_flow_steps(raw.get("flowSteps"))
    if not flow_steps:
        flow_steps = default_flow_steps_for_media(media)
    # 创建默认补齐技能包四文件（AI 已给的不覆盖）
    package_files = build_user_skill_package_files(
        title=title,
        description=description,
        media_kind=media,
        node_recipe=recipe,
        flow_steps=flow_steps,
        output_content=output,
        existing=package_files,
    )
    # 用户自建技能包默认走精细画布操控（与平台技能包对齐）
    if not str(raw.get("executionMode") or "").strip():
        execution_mode = "canvas_manual"
    return {
        "title": title,
        "description": description,
        "idea": idea,
        "mediaKind": media,
        "aspectRatio": aspect,
        "durationSec": duration,
        "clarity": clarity,
        "pipelineActions": actions,
        "nodeRecipe": recipe,
        "useCases": use_cases,
        "howToUse": how_to,
        "outputContent": output,
        "executionMode": execution_mode,
        "canvasRulesMarkdown": canvas_rules_markdown,
        "packageFiles": package_files,
        "flowSteps": flow_steps,
    }


async def _llm_draft_skill_from_prompt(
    prompt: str, *, media_kind: str | None = None
) -> dict[str, Any]:
    """调用控制器 LLM 生成 Skill 草稿；失败回退模板。"""
    from ..integrations.llm.chat import chat_completion
    from .agent_controller import pick_controller_model

    model = pick_controller_model(None)
    forced = str(media_kind or "").strip().lower()
    if forced not in _MEDIA_KINDS:
        forced = ""
    if not model:
        draft = _template_skill_from_prompt(prompt)
        if forced:
            draft = _normalize_created_skill_draft(draft, prompt, force_media_kind=forced)
        return draft
    try:
        kind_line = (
            f"\n用户已选定类型 mediaKind={forced}，必须严格按此类型设计节点与规格，禁止改成其它类型。"
            if forced
            else ""
        )
        content = await chat_completion(
            model,
            [
                {"role": "system", "content": _CREATE_SKILL_SYSTEM},
                {"role": "user", "content": f"用户一句话：{prompt}{kind_line}"},
            ],
            temperature=0.55,
            max_tokens=8192,
            timeout_s=90.0,
        )
        parsed = _parse_llm_json_obj(content)
        if not parsed:
            return _normalize_created_skill_draft(
                _template_skill_from_prompt(prompt),
                prompt,
                force_media_kind=forced or None,
            )
        return _normalize_created_skill_draft(
            parsed, prompt, force_media_kind=forced or None
        )
    except Exception:
        return _normalize_created_skill_draft(
            _template_skill_from_prompt(prompt),
            prompt,
            force_media_kind=forced or None,
        )


async def create_skill_from_prompt(
    db: AsyncSession,
    user: User,
    *,
    prompt: str,
) -> dict[str, Any]:
    """一句话让 AI 生成「我的 Skill」并落库（含 SKILL.md）。"""
    text = (prompt or "").strip()
    if len(text) < 2:
        fail(ErrorCode.SKILL_PROMPT_INVALID)
    if len(text) > 500:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="描述过长，请控制在 500 字以内")

    draft = await _llm_draft_skill_from_prompt(text)
    draft = _normalize_created_skill_draft(draft, text)

    media_kind = normalize_media_kind(str(draft.get("mediaKind") or "video"))
    pipeline = [
        dict(_ACTION_TO_STEP[a])
        for a in draft["pipelineActions"]
        if a in _ACTION_TO_STEP
        and not (media_kind == "image" and a in _VIDEO_ONLY_ACTIONS)
        and not (media_kind == "text" and a in _TEXT_SKIP_ACTIONS)
        and not (media_kind == "audio" and a in _AUDIO_SKIP_ACTIONS)
    ] or pipeline_for_media_kind(media_kind)
    recipe = normalize_node_recipe(draft.get("nodeRecipe"), media_kind=media_kind)

    defaults: dict[str, Any] = {
        "idea": draft["idea"],
        "genMode": "smart",
        "mediaKind": media_kind,
        "aspectRatio": draft["aspectRatio"],
        "nodeRecipe": recipe,
    }
    if media_kind == "video" and draft.get("durationSec"):
        defaults["durationSec"] = draft["durationSec"]
    if draft.get("clarity") and media_kind in ("image", "video"):
        defaults["clarity"] = draft["clarity"]

    skill_title = str(draft["title"])[:64]
    desc = str(draft["description"])
    inputs = _build_my_skill_inputs(
        defaults=defaults,
        session_id="from-prompt",
        idea_fallback=skill_title,
    )
    # 标记来源，便于列表识别
    inputs.append(
        {
            "name": "createdFrom",
            "type": "string",
            "value": "prompt",
        }
    )

    # 一句话创建同样落技能包四文件 + 默认精细画布操控
    pkg = draft.get("packageFiles")
    if not isinstance(pkg, dict) or not pkg:
        pkg = build_user_skill_package_files(
            title=skill_title,
            description=desc,
            media_kind=media_kind,
            node_recipe=recipe,
            flow_steps=_normalize_flow_steps(draft.get("flowSteps")),
            output_content=str(draft.get("outputContent") or ""),
            existing=None,
        )
    mode = str(draft.get("executionMode") or "").strip() or "canvas_manual"
    if mode not in ("team", "canvas_manual"):
        mode = "canvas_manual"

    slug = _slugify_user_title(skill_title, int(user.id))
    row = Skill(
        slug=slug,
        title=skill_title,
        description=desc,
        category="我的 Skill",
        visibility="private",
        owner_user_id=int(user.id),
        cover_url=None,
        entry_kind=None,
        inputs=inputs,
        pipeline=pipeline,
        default_style_id=None,
        pricing_hint="per_pipeline",
        version=1,
        sort_order=0,
        status="active",
        execution_mode=mode,
        canvas_rules_markdown=draft.get("canvasRulesMarkdown"),
        package_files=pkg,
    )
    _assign_skill_doc_markdown(row, defaults=defaults, pipeline=pipeline)
    db.add(row)
    await db.flush()
    return await skill_to_dict_resolved(db, row, favorited=False)


async def draft_skill_from_prompt(
    prompt: str, *, media_kind: str | None = None
) -> dict[str, Any]:
    """一句话 AI 草稿（不落库），供创建表单填充；可强制 mediaKind。"""
    text = (prompt or "").strip()
    if len(text) < 2:
        fail(ErrorCode.SKILL_PROMPT_INVALID)
    if len(text) > 500:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="描述过长，请控制在 500 字以内")
    draft = await _llm_draft_skill_from_prompt(text, media_kind=media_kind)
    draft = _normalize_created_skill_draft(
        draft, text, force_media_kind=media_kind
    )
    media = normalize_media_kind(str(draft.get("mediaKind") or "video"))
    pipeline = pipeline_for_media_kind(media)
    recipe = normalize_node_recipe(draft.get("nodeRecipe"), media_kind=media)
    use_cases = str(draft.get("useCases") or "").strip() or (
        f"适合需要「{draft['title']}」能力的创作：用户提供相关文本或想法，"
        f"由 AI 按 {media} 类型规格出方案并操控画布。"
    )
    how_to = str(draft.get("howToUse") or "").strip() or (
        "1. 选择本 Skill，填写内容（可加附件）后发送\n"
        "2. 进入画布：AI 给出创作方案并询问你的建议\n"
        "3. 你同意后：AI 新建节点、连线并填入各节点内容\n"
        "4. AI 询问是否生成；你回复「生成/出图/全部生成」等后开始扣费生成"
    )
    output_by_kind = {
        "image": (
            "单张生图：含画面描述文本节点与一张图片节点（完整提示词与画幅规格）；"
            "仅当用户明确要求多张/条漫时再增加图片节点；确认生成后出图。"
        ),
        "text": "结构化文案/剧本与角色设定等文本节点，内容由 AI 按方案写入。",
        "audio": "情绪说明 + BGM/音效节点（提示词已填），确认后生成音频。",
        "video": "剧本/角色与多镜视频节点；确认后按镜生成，可再进剪辑。",
    }
    output = str(draft.get("outputContent") or "").strip() or output_by_kind.get(
        media, output_by_kind["video"]
    )
    defaults = {
        "idea": draft["idea"],
        "genMode": "smart",
        "mediaKind": media,
        "aspectRatio": draft["aspectRatio"],
        "useCases": use_cases,
        "howToUse": how_to,
        "outputContent": output,
        "nodeRecipe": recipe,
    }
    if media == "video" and draft.get("durationSec"):
        defaults["durationSec"] = draft["durationSec"]
    if draft.get("clarity") and media in ("image", "video"):
        defaults["clarity"] = draft["clarity"]
    md = build_user_skill_markdown(
        slug="draft",
        title=str(draft["title"]),
        description=str(draft["description"]),
        pipeline=pipeline,
        defaults=defaults,
        media_kind=media,
        use_cases=use_cases,
        how_to_use=how_to,
        output_content=output,
    )
    return {
        "title": draft["title"],
        "description": draft["description"],
        "useCases": use_cases,
        "howToUse": how_to,
        "outputContent": output,
        "mediaKind": media,
        "aspectRatio": draft["aspectRatio"],
        "durationSec": draft.get("durationSec") or "",
        "clarity": draft.get("clarity") or "",
        "idea": draft["idea"],
        "docMarkdown": md,
        "nodeRecipe": recipe,
        "pipelineSummary": pipeline_summary(
            pipeline, media_kind=media, node_recipe=recipe
        ),
        # 技能包能力对齐：执行模式 / 流程步骤 / 多文件参考包，供创建表单预填
        "executionMode": draft.get("executionMode") or "canvas_manual",
        "canvasRulesMarkdown": draft.get("canvasRulesMarkdown") or "",
        "packageFiles": draft.get("packageFiles") or {},
        "flowSteps": draft.get("flowSteps")
        or default_flow_steps_for_media(media),
    }


async def create_skill_from_form(
    db: AsyncSession,
    user: User,
    *,
    title: str,
    description: str,
    use_cases: str,
    how_to_use: str,
    output_content: str,
    media_kind: str = "image",
    doc_markdown: str | None = None,
    cover_url: str | None = None,
    idea: str | None = None,
    aspect_ratio: str | None = None,
    duration_sec: str | None = None,
    clarity: str | None = None,
    node_recipe: dict[str, Any] | None = None,
    execution_mode: str | None = None,
    canvas_rules_markdown: str | None = None,
    package_files: dict[str, Any] | None = None,
    flow_steps: list[Any] | None = None,
) -> dict[str, Any]:
    """LibTV 风格表单创建「我的 Skill」（名称 / 介绍 / 场景 / 用法 / 输出 / MD）。"""
    skill_title = (title or "").strip()[:64]
    desc = (description or "").strip()[:200]
    use_cases_t = (use_cases or "").strip()[:8000]
    how_to_t = (how_to_use or "").strip()[:8000]
    output_t = (output_content or "").strip()[:8000]
    if len(skill_title) < 1:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="请填写 Skill 名称")
    if len(desc) < 2:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="请填写一句话介绍")
    if len(use_cases_t) < 2:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="请填写使用场景")
    if len(how_to_t) < 2:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="请填写如何使用")
    if len(output_t) < 2:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="请填写输出内容")

    kind = str(media_kind or "").strip().lower()
    if kind not in _MEDIA_KINDS:
        kind = infer_media_kind(skill_title, desc, use_cases_t, output_t)
    kind = normalize_media_kind(kind)
    pipeline = pipeline_for_media_kind(kind)
    recipe = normalize_node_recipe(node_recipe, media_kind=kind)
    aspect = str(aspect_ratio or "").strip()
    if aspect not in ("9:16", "16:9", "1:1"):
        aspect = "9:16" if kind != "audio" else "1:1"
    dur = str(duration_sec or "").strip()
    if kind != "video":
        dur = ""
    elif dur not in ("15", "30", "45", "60"):
        dur = "30"
    clar = str(clarity or "").strip()
    if kind not in ("image", "video"):
        clar = ""
    elif clar not in ("720p", "1080p", ""):
        clar = "1080p"

    seed_idea = (idea or "").strip()[:500] or use_cases_t[:500] or skill_title
    defaults: dict[str, Any] = {
        "idea": seed_idea,
        "genMode": "smart",
        "mediaKind": kind,
        "aspectRatio": aspect,
        "useCases": use_cases_t,
        "howToUse": how_to_t,
        "outputContent": output_t,
        "nodeRecipe": recipe,
    }
    if dur:
        defaults["durationSec"] = dur
    if clar:
        defaults["clarity"] = clar

    inputs = _build_my_skill_inputs(
        defaults=defaults,
        session_id="from-form",
        idea_fallback=skill_title,
    )
    inputs.append({"name": "createdFrom", "type": "string", "value": "form"})

    cover = persistable_media_url(cover_url)
    if cover and not (cover.startswith("https://") or cover.startswith("http://")):
        cover = None

    steps = _normalize_flow_steps(flow_steps) or default_flow_steps_for_media(kind)
    pkg = build_user_skill_package_files(
        title=skill_title,
        description=desc,
        media_kind=kind,
        node_recipe=recipe,
        flow_steps=steps,
        output_content=output_t,
        existing=_normalize_package_files(package_files),
    )
    # 表单创建默认技能包 + 精细画布操控（显式传 team 仍可覆盖）
    mode = _normalize_execution_mode(execution_mode)
    if not str(execution_mode or "").strip():
        mode = "canvas_manual"

    slug = _slugify_user_title(skill_title, int(user.id))
    row = Skill(
        slug=slug,
        title=skill_title,
        description=desc,
        category="我的 Skill",
        visibility="private",
        owner_user_id=int(user.id),
        cover_url=cover,
        entry_kind=None,
        inputs=inputs,
        pipeline=pipeline,
        default_style_id=None,
        pricing_hint="per_pipeline",
        version=1,
        sort_order=0,
        status="active",
        review_status="none",
        execution_mode=mode,
        canvas_rules_markdown=_normalize_canvas_rules_markdown(canvas_rules_markdown),
        package_files=pkg,
    )
    # 以所选 mediaKind + nodeRecipe + 表单分区为准生成详细 SKILL.md
    # （避免前端预览草稿缺节点时覆盖权威文档；手写整份 MD 仍可通过 override 传入）
    override = str(doc_markdown or "").strip()
    use_override = bool(
        override
        and (
            "execution: agent_recipe" in override
            or "execution: server_team" in override
        )
        and ("## Agent 必读" in override or "## 画布节点" in override)
        and len(override) > 400
    )
    _assign_skill_doc_markdown(
        row,
        defaults=defaults,
        pipeline=pipeline,
        doc_markdown_override=override if use_override else None,
    )
    db.add(row)
    await db.flush()
    return await skill_to_dict_resolved(db, row, favorited=False)


async def create_skill_from_package_zip(
    db: AsyncSession,
    user: User,
    *,
    zip_bytes: bytes,
    dry_run: bool = False,
) -> dict[str, Any]:
    """从 Agent Skills / Codex 风格 zip 创建「我的 Skill」（只入库 markdown）。

    scripts/ 等写入 inputs.unsupportedFiles，不进可执行路径。
    """
    from .skill_package_import import (
        import_result_to_preview_dict,
        parse_skill_package_zip,
    )

    try:
        parsed = parse_skill_package_zip(zip_bytes)
    except ValueError as exc:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message=str(exc) or "技能包无效")

    preview = import_result_to_preview_dict(parsed)
    if dry_run:
        return {"dryRun": True, "preview": preview}

    title = (parsed.title or parsed.name)[:64]
    desc = (parsed.description or parsed.name)[:200]
    # 导入包：用法/场景用 description 填充表单必填语义
    use_cases = f"按技能「{parsed.name}」配方执行。{desc}"[:8000]
    how_to = (
        f"在对话中点选本技能，或输入 ${parsed.name}。"
        "按 SKILL.md 步骤用画布工具落地；本环境不执行 scripts/。"
    )[:8000]
    output_t = "按技能配方产出画布节点与生成结果"[:8000]

    pkg = _normalize_package_files(parsed.package_files) or {}
    defaults: dict[str, Any] = {
        "idea": desc[:500] or title,
        "genMode": "smart",
        "mediaKind": "video",
        "aspectRatio": "9:16",
        "useCases": use_cases,
        "howToUse": how_to,
        "outputContent": output_t,
        "importedSkillName": parsed.name,
    }
    recipe = normalize_node_recipe(None, media_kind="video")
    defaults["nodeRecipe"] = recipe
    pipeline = pipeline_for_media_kind("video")
    inputs = _build_my_skill_inputs(
        defaults=defaults,
        session_id="from-zip",
        idea_fallback=title,
    )
    inputs.append({"name": "createdFrom", "type": "string", "value": "zip_import"})
    inputs.append({"name": "agentSkillsName", "type": "string", "value": parsed.name})
    if parsed.unsupported_files:
        inputs.append(
            {
                "name": "unsupportedFiles",
                "type": "string_list",
                "value": list(parsed.unsupported_files),
            }
        )

    slug = _slugify_user_title(parsed.name.replace("-", " "), int(user.id))
    row = Skill(
        slug=slug,
        title=title,
        description=desc,
        category="我的 Skill",
        visibility="private",
        owner_user_id=int(user.id),
        cover_url=None,
        entry_kind=parsed.entry_kind or None,
        inputs=inputs,
        pipeline=pipeline,
        default_style_id=None,
        pricing_hint="per_pipeline",
        version=1,
        sort_order=0,
        status="active",
        review_status="none",
        execution_mode="canvas_manual",
        canvas_rules_markdown=None,
        package_files=pkg or None,
        doc_markdown=parsed.skill_md,
    )
    db.add(row)
    await db.flush()
    data = await skill_to_dict_resolved(db, row, favorited=False)
    data["unsupportedFiles"] = list(parsed.unsupported_files)
    data["importWarnings"] = list(parsed.warnings)
    data["agentSkillsName"] = parsed.name
    return data


def _demote_published_skill_on_edit(skill: Skill) -> None:
    """已公开/待审的社区 Skill 被所有者改文档后：撤回公开并回到待审。"""
    if skill.owner_user_id is None:
        return
    rs = str(getattr(skill, "review_status", None) or "none")
    if rs in ("approved", "pending"):
        skill.visibility = "private"
        skill.review_status = "pending"
        skill.review_note = None
        skill.reviewed_at = None
        skill.reviewed_by = None


async def save_skill_from_session(
    db: AsyncSession,
    user: User,
    *,
    session_id: str,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """成功会话另存为我的 Skill（私有）；若会话已绑用户自建 Skill 则原地更新。"""
    from . import agent_sessions as agent_sessions_svc
    from . import project_graphs

    session = await agent_sessions_svc.get_session_for_user(db, user, session_id)
    if session.status == "failed":
        fail(ErrorCode.SKILL_SAVE_NOT_READY, message="失败会话无法另存")
    if session.status not in ("completed", "active"):
        fail(ErrorCode.SKILL_SAVE_NOT_READY, message="会话尚未完成，无法另存为 Skill")

    graph_payload = await project_graphs.get_graph_dict(db, int(session.project_id))
    graph = (graph_payload or {}).get("graph") if isinstance(graph_payload, dict) else None
    if not isinstance(graph, dict):
        graph = {}
    # active 且无 Graph：仍可另存（如 Wizard 会话），但 completed 更常见
    if session.status == "active" and not (graph.get("script") or graph.get("shots") or session.skill_id):
        fail(ErrorCode.SKILL_SAVE_NOT_READY, message="编排尚未完成，请稍后再另存")

    skill_title = (title or "").strip() or (session.title or "我的流水线")[:64]
    desc = (description or "").strip()
    if not desc:
        script_title = ""
        if isinstance(graph.get("script"), dict):
            script_title = str(graph["script"].get("title") or "")
        desc = f"从会话另存：{script_title or skill_title}"

    # 提取可复用默认：想法 / 风格 / 模型 / 画幅时长等
    defaults = await _collect_session_defaults(db, session, graph)
    style_id = defaults.get("styleId") or getattr(session, "style_id", None)
    if not style_id and isinstance(graph.get("style"), dict):
        style_id = graph["style"].get("styleId")
    if style_id:
        defaults["styleId"] = str(style_id).strip()

    pipeline = list(_USER_FREE_PIPELINE)
    update_row: Skill | None = None
    if session.skill_id:
        src = (
            await db.execute(select(Skill).where(Skill.id == int(session.skill_id)).limit(1))
        ).scalar_one_or_none()
        if src is not None:
            # 会话来自「我的 Skill」：更新同一张卡，避免重复堆积
            if (
                src.owner_user_id is not None
                and int(src.owner_user_id) == int(user.id)
                and src.status == "active"
            ):
                update_row = src
            elif src.pipeline:
                pipeline = list(src.pipeline)

    # 无想法时用标题兜底
    if not defaults.get("idea"):
        defaults["idea"] = skill_title
    media_for_recipe = str(defaults.get("mediaKind") or "video").strip().lower()
    if media_for_recipe not in _MEDIA_KINDS:
        media_for_recipe = infer_media_kind(skill_title, desc, defaults.get("idea"))
        defaults["mediaKind"] = media_for_recipe
    defaults["nodeRecipe"] = normalize_node_recipe(
        defaults.get("nodeRecipe"), media_kind=media_for_recipe
    )

    inputs = _build_my_skill_inputs(
        defaults=defaults,
        session_id=str(session.id),
        idea_fallback=skill_title,
    )
    style_val = str(style_id).strip() if style_id else None

    if update_row is not None:
        if title and title.strip():
            update_row.title = skill_title
        update_row.description = desc
        update_row.inputs = inputs
        update_row.pipeline = pipeline if update_row.pipeline else pipeline
        # 保留原 pipeline 若已有；仍刷新为当前（含从平台拷贝的）
        if pipeline:
            update_row.pipeline = pipeline
        update_row.default_style_id = style_val
        _assign_skill_doc_markdown(
            update_row, defaults=defaults, pipeline=list(update_row.pipeline or [])
        )
        _demote_published_skill_on_edit(update_row)
        update_row.version = int(update_row.version or 1) + 1
        update_row.updated_at = now_cst_naive()
        await db.flush()
        return await skill_to_dict_resolved(db, update_row, favorited=False)

    slug = _slugify_user_title(skill_title, int(user.id))
    row = Skill(
        slug=slug,
        title=skill_title,
        description=desc,
        category="我的 Skill",
        visibility="private",
        owner_user_id=int(user.id),
        cover_url=None,
        entry_kind=None,  # 无 Wizard；配方铺节点
        inputs=inputs,
        pipeline=pipeline,
        default_style_id=style_val,
        pricing_hint="per_pipeline",
        version=1,
        sort_order=0,
        status="active",
        review_status="none",
    )
    _assign_skill_doc_markdown(row, defaults=defaults, pipeline=pipeline)
    db.add(row)
    await db.flush()
    return await skill_to_dict_resolved(db, row, favorited=False)


async def apply_skill_recipe(
    db: AsyncSession,
    user: User,
    *,
    slug: str,
    project_id: str,
    session_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """将我的 Skill 的 nodeRecipe 铺到项目画布（canvas_ops）；不跑 Agent Team。"""
    from ..common.utils.redis_mark_lock import mark_claim, mark_exists, mark_seal
    from . import agent_sessions as agent_sessions_svc
    from . import project_graphs
    from .project_access import require_project_access

    skill = await require_skill_by_slug(db, slug, user=user)
    if skill.owner_user_id is None or int(skill.owner_user_id) != int(user.id):
        fail(ErrorCode.SKILL_FORBIDDEN, message="只能对自己的 Skill 铺节点")
    await require_project_access(db, user, str(project_id).strip())
    try:
        pid = int(str(project_id).strip())
    except (TypeError, ValueError):
        fail(ErrorCode.INVALID_PROJECT_ID)

    defaults = get_skill_defaults(skill)
    media_kind = normalize_media_kind(
        str(defaults.get("mediaKind") or ""),
        fallback=infer_media_kind(skill.title, skill.description, defaults.get("idea")),
    )
    recipe = normalize_node_recipe(defaults.get("nodeRecipe"), media_kind=media_kind)
    lock_key = f"lock:skill:recipe:{pid}"
    ttl = 300
    already = await mark_exists(lock_key, fail_closed=True)
    if already and not force:
        fail(ErrorCode.CONFLICT, message="该项目已铺过配方节点；如需再次铺入请确认后重试")
    if not already:
        claimed = await mark_claim(lock_key, ttl_sec=ttl, fail_closed=True)
        if not claimed and not force:
            fail(ErrorCode.RATE_LIMITED, message="铺节点处理中，请稍后再试")

    ops = node_recipe_to_canvas_ops(recipe)
    if not ops:
        fail(ErrorCode.SKILL_PROMPT_INVALID, message="配方为空，无法铺节点")

    graph_payload = await project_graphs.get_graph_dict(db, pid)
    graph = (graph_payload or {}).get("graph") if isinstance(graph_payload, dict) else None
    if not isinstance(graph, dict):
        graph = project_graphs.empty_graph(pid)
    saved = await project_graphs.save_graph(
        db, pid, graph, canvas_ops=ops, bump_revision=True
    )
    await mark_seal(lock_key, ttl_sec=86400, fail_closed=True)

    # 可选：在会话中旁白一条
    if session_id and str(session_id).strip():
        try:
            session = await agent_sessions_svc.get_session_for_user(
                db, user, str(session_id).strip()
            )
            if int(session.project_id) == pid:
                brief = (
                    dict(session.brief_json)
                    if isinstance(session.brief_json, dict)
                    else {}
                )
                brief["recipeApplied"] = True
                brief["recipePending"] = False
                session.brief_json = brief
                # 铺完后继续等待用户操作，勿留 active 以免 UI「处理中」
                session.status = "awaiting_user"
                await agent_sessions_svc.append_message(
                    db,
                    session,
                    role="agent",
                    agent_role="orchestrator",
                    content=(
                        f"已按配方铺到画布（{node_recipe_summary(recipe)}）。"
                        "请填写各节点内容后手动生成。"
                    ),
                )
        except Exception:
            pass

    return {
        "projectId": str(pid),
        "revision": int(saved.get("revision") or 0) if isinstance(saved, dict) else 0,
        "canvasOps": ops,
        "nodeRecipe": recipe,
        "pipelineSummary": node_recipe_summary(recipe),
        "graph": saved,
    }


async def archive_my_skill(db: AsyncSession, user: User, slug: str) -> dict[str, Any]:
    """归档用户自建 Skill（软删）。"""
    skill = await get_skill_by_slug(db, slug)
    if skill is None or skill.status != "active":
        fail(ErrorCode.SKILL_NOT_FOUND)
    if skill.owner_user_id is None or int(skill.owner_user_id) != int(user.id):
        fail(ErrorCode.SKILL_FORBIDDEN, message="只能删除自己的 Skill")
    skill.status = "archived"
    skill.updated_at = now_cst_naive()
    await db.flush()
    return await skill_to_dict_resolved(db, skill, favorited=False)


async def update_my_skill_meta(
    db: AsyncSession,
    user: User,
    slug: str,
    *,
    title: str | None = None,
    cover_url: str | None = None,
    category: str | None = None,
    clear_cover: bool = False,
    execution_mode: str | None = None,
    canvas_rules_markdown: str | None = None,
    package_files: dict[str, Any] | None = None,
    clear_package_files: bool = False,
) -> dict[str, Any]:
    """所有者更新「我的」Skill 封面/名称/分类/执行模式/画布规则/参考包；已公开则撤回并回待审。"""
    skill = await get_skill_by_slug(db, slug)
    if skill is None or skill.status != "active":
        fail(ErrorCode.SKILL_NOT_FOUND)
    # 平台种子不可被普通用户改元信息
    if skill.owner_user_id is None:
        fail(ErrorCode.SKILL_FORBIDDEN, message="平台内置 Skill 不可修改")
    if int(skill.owner_user_id) != int(user.id):
        fail(ErrorCode.SKILL_FORBIDDEN, message="只能编辑自己的 Skill")

    changed = False
    if title is not None:
        t = title.strip()
        if not t:
            fail(ErrorCode.VALIDATION_ERROR, message="名称不能为空")
        if t != str(skill.title or ""):
            skill.title = t[:64]
            changed = True

    if clear_cover:
        if skill.cover_url:
            skill.cover_url = None
            changed = True
    elif cover_url is not None:
        cover = persistable_media_url(cover_url)
        if cover != (skill.cover_url or None):
            skill.cover_url = cover
            changed = True

    if category is not None:
        cat = category.strip()
        if not cat or cat in ("推荐", "我的 Skill"):
            fail(ErrorCode.VALIDATION_ERROR, message="请选择有效分类")
        configured = await list_skill_categories(db, None)
        allowed = {c for c in configured if c not in ("推荐", "我的 Skill")}
        if allowed and cat not in allowed:
            fail(ErrorCode.VALIDATION_ERROR, message=f"分类不在可选列表：{cat}")
        if cat != str(skill.category or ""):
            skill.category = cat[:64]
            changed = True

    # 技能包能力对齐：执行模式 / 画布规则覆盖 / 多文件参考包（均可选，供高级用户后续追加）
    if execution_mode is not None:
        mode = _normalize_execution_mode(execution_mode)
        if mode != str(getattr(skill, "execution_mode", None) or "team"):
            skill.execution_mode = mode
            changed = True

    if canvas_rules_markdown is not None:
        rules = _normalize_canvas_rules_markdown(canvas_rules_markdown)
        if rules != (getattr(skill, "canvas_rules_markdown", None) or None):
            skill.canvas_rules_markdown = rules
            changed = True

    if clear_package_files:
        if getattr(skill, "package_files", None):
            skill.package_files = None
            changed = True
    elif package_files is not None:
        files = _normalize_package_files(package_files)
        if files != (getattr(skill, "package_files", None) or None):
            skill.package_files = files
            changed = True

    if not changed:
        return await skill_to_dict_resolved(db, skill, favorited=False)

    # 已公开/待审：改元信息后需重新审核
    _demote_published_skill_on_edit(skill)
    skill.updated_at = now_cst_naive()
    await db.flush()
    return await skill_to_dict_resolved(db, skill, favorited=False)


async def list_public_skills(
    db: AsyncSession,
    *,
    category: str | None = None,
    user: User | None = None,
) -> list[dict[str, Any]]:
    """公开活跃 Skill 列表；登录用户附带收藏标记。

    含：平台 Skill（owner 为空）+ 审核通过的社区 Skill。
    """
    from sqlalchemy import or_

    q = (
        select(Skill)
        .where(
            Skill.visibility == "public",
            Skill.status == "active",
            or_(
                Skill.owner_user_id.is_(None),
                Skill.review_status == "approved",
            ),
        )
        .order_by(Skill.sort_order.asc(), Skill.id.asc())
    )
    if category and category.strip() and category.strip() != "推荐":
        q = q.where(Skill.category == category.strip())
    rows = list((await db.execute(q)).scalars().all())

    fav_ids: set[int] = set()
    if user is not None and rows:
        skill_ids = [int(r.id) for r in rows]
        fav_rows = (
            await db.execute(
                select(SkillFavorite.skill_id).where(
                    SkillFavorite.user_id == int(user.id),
                    SkillFavorite.skill_id.in_(skill_ids),
                )
            )
        ).scalars().all()
        fav_ids = {int(x) for x in fav_rows}

    overrides = await get_skill_doc_overrides(db)
    owner_labels = await load_owner_labels(
        db, {int(r.owner_user_id) for r in rows if r.owner_user_id is not None}
    )
    return [
        await skill_to_dict_resolved(
            db,
            r,
            favorited=int(r.id) in fav_ids,
            overrides=overrides,
            owner_display_name=owner_labels.get(int(r.owner_user_id))
            if r.owner_user_id is not None
            else None,
        )
        for r in rows
    ]


async def list_favorite_skills(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """当前用户收藏的公开 Skill。"""
    q = (
        select(Skill)
        .join(SkillFavorite, SkillFavorite.skill_id == Skill.id)
        .where(
            SkillFavorite.user_id == int(user.id),
            Skill.status == "active",
        )
        .order_by(SkillFavorite.created_at.desc())
    )
    rows = list((await db.execute(q)).scalars().all())
    overrides = await get_skill_doc_overrides(db)
    owner_labels = await load_owner_labels(
        db, {int(r.owner_user_id) for r in rows if r.owner_user_id is not None}
    )
    return [
        await skill_to_dict_resolved(
            db,
            r,
            favorited=True,
            overrides=overrides,
            owner_display_name=owner_labels.get(int(r.owner_user_id))
            if r.owner_user_id is not None
            else None,
        )
        for r in rows
    ]


async def add_skill_favorite(db: AsyncSession, user: User, slug: str) -> dict[str, Any]:
    skill = await require_skill_by_slug(db, slug)
    existing = (
        await db.execute(
            select(SkillFavorite).where(
                SkillFavorite.user_id == int(user.id),
                SkillFavorite.skill_id == int(skill.id),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(SkillFavorite(user_id=int(user.id), skill_id=int(skill.id)))
        await db.flush()
    return await skill_to_dict_resolved(db, skill, favorited=True)


async def remove_skill_favorite(db: AsyncSession, user: User, slug: str) -> dict[str, Any]:
    skill = await require_skill_by_slug(db, slug)
    existing = (
        await db.execute(
            select(SkillFavorite).where(
                SkillFavorite.user_id == int(user.id),
                SkillFavorite.skill_id == int(skill.id),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.flush()
    return await skill_to_dict_resolved(db, skill, favorited=False)


def skill_display_status(skill: Skill) -> str:
    """合成展示态：listed | unlisted | pending | rejected | none。"""
    vis = str(skill.visibility or "")
    rs = str(getattr(skill, "review_status", None) or "none")
    is_platform = skill.owner_user_id is None
    if rs == "pending":
        return "pending"
    if rs == "rejected":
        return "rejected"
    if vis == "public" and (rs == "approved" or is_platform):
        return "listed"
    if rs == "approved" and vis != "public":
        return "unlisted"
    return "none"


def _normalize_review_history(raw: object | None) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "action": str(item.get("action") or ""),
                "note": str(item.get("note") or "") or None,
                "at": str(item.get("at") or "") or None,
                "by": str(item.get("by") or "") or None,
                "byName": str(item.get("byName") or "") or None,
            }
        )
    return out


def _append_skill_review_history(
    skill: Skill,
    *,
    action: str,
    note: str | None,
    at,
    admin: User,
) -> None:
    """追加一条审核记录（最多保留 50 条）。"""
    hist = _normalize_review_history(getattr(skill, "review_history", None))
    hist.append(
        {
            "action": action,
            "note": note,
            "at": to_cst_iso(at),
            "by": str(admin.id),
            "byName": (admin.display_name or "").strip() or str(admin.id),
        }
    )
    skill.review_history = hist[-50:]


async def list_skill_categories(
    db: AsyncSession,
    skills: list[dict[str, Any]] | None = None,
) -> list[str]:
    """推荐 + 平台配置分类 + 存量分类去重（配置优先排序）。"""
    from .platform_settings import get_skill_categories

    configured = await get_skill_categories(db)
    cats: list[str] = ["推荐"]
    seen = {"推荐"}
    for c in configured:
        name = str(c or "").strip()
        if name and name not in seen:
            seen.add(name)
            cats.append(name)
    for s in skills or []:
        c = str(s.get("category") or "").strip()
        if c and c not in seen and c != "我的 Skill":
            seen.add(c)
            cats.append(c)
    return cats


async def publish_my_skill(db: AsyncSession, user: User, slug: str) -> dict[str, Any]:
    """用户提交社区发布：进入 pending，通过前仍 private。"""
    skill = await require_skill_by_slug(db, slug, user=user)
    if skill.owner_user_id is None or int(skill.owner_user_id) != int(user.id):
        fail(ErrorCode.SKILL_FORBIDDEN, message="只能发布自己的 Skill")
    if skill.status != "active":
        fail(ErrorCode.SKILL_REVIEW_INVALID, message="已归档的 Skill 无法发布")
    rs = str(getattr(skill, "review_status", None) or "none")
    if rs == "pending":
        fail(ErrorCode.SKILL_REVIEW_PENDING)
    if rs == "approved" and skill.visibility == "public":
        return await skill_to_dict_resolved(db, skill, favorited=False)
    doc_md = str(getattr(skill, "doc_markdown", None) or "").strip()
    if len(doc_md) < 40:
        fail(ErrorCode.SKILL_PUBLISH_NOT_READY)
    skill.review_status = "pending"
    skill.visibility = "private"
    skill.review_note = None
    skill.reviewed_at = None
    skill.reviewed_by = None
    skill.updated_at = now_cst_naive()
    await db.flush()
    return await skill_to_dict_resolved(db, skill, favorited=False)


async def unpublish_my_skill(db: AsyncSession, user: User, slug: str) -> dict[str, Any]:
    """用户撤回公开或取消待审。"""
    skill = await require_skill_by_slug(db, slug, user=user)
    if skill.owner_user_id is None or int(skill.owner_user_id) != int(user.id):
        fail(ErrorCode.SKILL_FORBIDDEN, message="只能操作自己的 Skill")
    skill.visibility = "private"
    skill.review_status = "none"
    skill.review_note = None
    skill.reviewed_at = None
    skill.reviewed_by = None
    skill.updated_at = now_cst_naive()
    await db.flush()
    return await skill_to_dict_resolved(db, skill, favorited=False)


def _skill_display_status_clause(display_status: str):
    """按合成展示态过滤 Skill。"""
    from sqlalchemy import and_, or_

    ds = display_status.strip()
    if ds == "listed":
        return or_(
            and_(Skill.visibility == "public", Skill.review_status == "approved"),
            and_(Skill.owner_user_id.is_(None), Skill.visibility == "public"),
        )
    if ds == "unlisted":
        return and_(Skill.review_status == "approved", Skill.visibility != "public")
    if ds == "pending":
        return Skill.review_status == "pending"
    if ds == "rejected":
        return Skill.review_status == "rejected"
    if ds == "none":
        return and_(
            Skill.review_status == "none",
            or_(Skill.owner_user_id.is_not(None), Skill.visibility != "public"),
        )
    return None


async def admin_list_skills(
    db: AsyncSession,
    *,
    review_status: str | None = None,
    display_status: str | None = None,
    visibility: str | None = None,
    category: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """管理端 Skill 分页列表（含展示态筛选与角标计数）。"""
    from sqlalchemy import func, or_

    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    base = select(Skill).where(Skill.status != "archived")
    kw = (q or "").strip()
    if kw:
        like = f"%{kw}%"
        base = base.where(
            or_(Skill.title.like(like), Skill.slug.like(like), Skill.description.like(like))
        )
    cat = (category or "").strip()
    if cat and cat != "all":
        base = base.where(Skill.category == cat)
    if visibility and visibility.strip() and visibility != "all":
        base = base.where(Skill.visibility == visibility.strip())

    # 兼容旧 reviewStatus；优先 displayStatus
    ds = (display_status or "").strip()
    rs = (review_status or "").strip()
    if ds and ds != "all":
        clause = _skill_display_status_clause(ds)
        if clause is not None:
            base = base.where(clause)
    elif rs and rs != "all":
        base = base.where(Skill.review_status == rs)

    count_q = select(func.count()).select_from(base.subquery())
    total = int((await db.execute(count_q)).scalar_one() or 0)
    rows = list(
        (
            await db.execute(
                base.order_by(Skill.updated_at.desc(), Skill.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [await skill_to_dict_resolved(db, r, favorited=False) for r in rows]

    # 附审核人昵称
    reviewer_ids = {
        int(r.reviewed_by) for r in rows if getattr(r, "reviewed_by", None) is not None
    }
    reviewers: dict[int, User] = {}
    if reviewer_ids:
        rev_rows = (
            await db.execute(select(User).where(User.id.in_(list(reviewer_ids))))
        ).scalars().all()
        reviewers = {int(u.id): u for u in rev_rows}
    for item, row in zip(items, rows):
        rid = getattr(row, "reviewed_by", None)
        if rid is not None and int(rid) in reviewers:
            u = reviewers[int(rid)]
            item["reviewedByName"] = (u.display_name or "").strip() or str(u.id)
        else:
            item["reviewedByName"] = None

    # 角标计数（与关键词/分类同范围，不受展示态筛选影响）
    count_base = select(Skill).where(Skill.status != "archived")
    if kw:
        like = f"%{kw}%"
        count_base = count_base.where(
            or_(Skill.title.like(like), Skill.slug.like(like), Skill.description.like(like))
        )
    if cat and cat != "all":
        count_base = count_base.where(Skill.category == cat)
    all_for_counts = list((await db.execute(count_base)).scalars().all())
    status_counts: dict[str, int] = {
        "all": len(all_for_counts),
        "listed": 0,
        "unlisted": 0,
        "pending": 0,
        "rejected": 0,
        "none": 0,
    }
    for r in all_for_counts:
        key = skill_display_status(r)
        status_counts[key] = status_counts.get(key, 0) + 1

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "statusCounts": status_counts,
    }


async def admin_review_skill(
    db: AsyncSession,
    admin: User,
    slug: str,
    *,
    action: str,
    note: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """管理端审核：approve | reject | unpublish。下架保留 approved。"""
    skill = (
        await db.execute(select(Skill).where(Skill.slug == slug).limit(1))
    ).scalar_one_or_none()
    if skill is None:
        fail(ErrorCode.SKILL_NOT_FOUND)
    act = (action or "").strip().lower()
    now = now_cst_naive()
    note_s = (note or "").strip()[:500] or None
    cat_s = (category or "").strip()[:64] or None
    if act == "approve":
        if skill.owner_user_id is None:
            fail(ErrorCode.SKILL_REVIEW_INVALID, message="平台 Skill 无需审核")
        skill.review_status = "approved"
        skill.visibility = "public"
        skill.review_note = note_s
        skill.reviewed_at = now
        skill.reviewed_by = int(admin.id)
        if cat_s:
            skill.category = cat_s
        _append_skill_review_history(
            skill, action="approve", note=note_s, at=now, admin=admin
        )
    elif act == "reject":
        if skill.owner_user_id is None:
            fail(ErrorCode.SKILL_REVIEW_INVALID, message="平台 Skill 无需审核")
        skill.review_status = "rejected"
        skill.visibility = "private"
        skill.review_note = note_s or "未通过审核"
        skill.reviewed_at = now
        skill.reviewed_by = int(admin.id)
        _append_skill_review_history(
            skill,
            action="reject",
            note=skill.review_note,
            at=now,
            admin=admin,
        )
    elif act == "unpublish":
        # 下架：仅关闭公开；曾通过则保留 approved，与驳回区分
        skill.visibility = "private"
        if skill.owner_user_id is not None and str(skill.review_status or "") == "approved":
            skill.review_status = "approved"
        elif skill.owner_user_id is not None and str(skill.review_status or "") == "pending":
            skill.review_status = "none"
        skill.review_note = note_s or "已下架"
        skill.reviewed_at = now
        skill.reviewed_by = int(admin.id)
        _append_skill_review_history(
            skill,
            action="unpublish",
            note=skill.review_note,
            at=now,
            admin=admin,
        )
    else:
        fail(ErrorCode.BAD_REQUEST, message="action 须为 approve / reject / unpublish")
    skill.updated_at = now
    await db.flush()
    # 审核结果通知所有者
    if skill.owner_user_id is not None and act in ("approve", "reject", "unpublish"):
        try:
            from .user_notifications import CATEGORY_SKILL_REVIEW, notify_user

            title_map = {
                "approve": "Skill 审核通过",
                "reject": "Skill 审核未通过",
                "unpublish": "Skill 已下架",
            }
            note_part = f"：{note_s}" if note_s else ""
            await notify_user(
                db,
                int(skill.owner_user_id),
                category=CATEGORY_SKILL_REVIEW,
                title=title_map[act],
                body=f"「{skill.title}」{title_map[act]}{note_part}",
                dedupe_key=f"skill_review:{act}:{skill.slug}:{to_cst_iso(now) or now.isoformat()}",
                link_url="/skills",
                ref_type="skill",
                ref_id=str(skill.slug),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "notify skill review failed slug=%s", getattr(skill, "slug", None), exc_info=True
            )
    item = await skill_to_dict_resolved(db, skill, favorited=False)
    item["reviewedByName"] = (admin.display_name or "").strip() or str(admin.id)
    return item


async def admin_update_skill_meta(
    db: AsyncSession,
    slug: str,
    *,
    title: str | None = None,
    cover_url: str | None = None,
    clear_cover: bool = False,
    category: str | None = None,
) -> dict[str, Any]:
    """管理端修改 Skill 名称 / 封面 / 分类（含平台种子；不触发回待审）。"""
    skill = (
        await db.execute(select(Skill).where(Skill.slug == slug).limit(1))
    ).scalar_one_or_none()
    if skill is None:
        fail(ErrorCode.SKILL_NOT_FOUND)

    changed = False
    if title is not None:
        t = title.strip()
        if not t:
            fail(ErrorCode.VALIDATION_ERROR, message="名称不能为空")
        if t != str(skill.title or ""):
            skill.title = t[:64]
            changed = True

    if clear_cover:
        if skill.cover_url:
            skill.cover_url = None
            changed = True
    elif cover_url is not None:
        cover = persistable_media_url(cover_url)
        if cover != (skill.cover_url or None):
            skill.cover_url = cover
            changed = True

    if category is not None:
        cat = (category or "").strip()[:64]
        if not cat:
            fail(ErrorCode.VALIDATION_ERROR, message="分类不能为空")
        if cat != str(skill.category or ""):
            skill.category = cat
            changed = True

    if not changed:
        return await skill_to_dict_resolved(db, skill, favorited=False)

    skill.updated_at = now_cst_naive()
    await db.flush()
    return await skill_to_dict_resolved(db, skill, favorited=False)


async def admin_update_skill_category(
    db: AsyncSession,
    slug: str,
    category: str,
) -> dict[str, Any]:
    """管理端修改 Skill 分类（兼容旧接口）。"""
    return await admin_update_skill_meta(db, slug, category=category)
