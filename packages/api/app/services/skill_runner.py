"""Skill Runner：从 MD / DB 解析 pipeline，规划下一步客户端或服务端动作。

一期：适配现有 Wizard/Pipeline（爆款复刻前端流水线），不重写切镜/拉片。
权威顺序：MD YAML `steps` → DB `Skill.pipeline` → entry_kind 默认表。
"""

from __future__ import annotations

from typing import Any

from ..models.skill import Skill
from .skill_docs import load_skill_doc, parse_pipeline_from_markdown

# 无 MD/DB 时的兜底（与 skills_catalog 种子对齐）
_DEFAULT_PIPELINES: dict[str, list[dict[str, Any]]] = {
    "viral_remake": [
        {
            "agent": "scriptwriter",
            "action": "storyboard_from_video",
            "trigger": "on_video_upload",
            "clientAction": "run_analyze",
        },
        {
            "agent": "character_designer",
            "action": "extract_subjects",
            "trigger": "with_analyze",
            "clientAction": "run_analyze",
        },
        {
            "agent": "animator",
            "action": "batch_remake_clips",
            "trigger": "on_confirm_generate",
            "clientAction": "run_batch",
        },
        {
            "agent": "editor",
            "action": "assemble_timeline",
            "trigger": "reserved",
            "clientAction": "noop",
        },
    ],
    "overseas_localize": [
        {
            "agent": "scriptwriter",
            "action": "storyboard_overseas_localize",
            "trigger": "on_video_upload",
            "clientAction": "run_analyze",
        },
        {
            "agent": "character_designer",
            "action": "localize_subjects",
            "trigger": "with_analyze",
            "clientAction": "run_analyze",
        },
        {
            "agent": "animator",
            "action": "batch_overseas_clips",
            "trigger": "on_confirm_generate",
            "clientAction": "run_batch",
        },
        {
            "agent": "editor",
            "action": "assemble_timeline",
            "trigger": "reserved",
            "clientAction": "noop",
        },
    ],
    # 单一产品电影级宣传片：服务端 Agent Team（非客户端 Wizard）
    "product_cinematic_commercial": [
        {"agent": "art_director", "action": "set_style", "trigger": "on_confirm", "clientAction": "noop"},
        {"agent": "scriptwriter", "action": "expand_script", "trigger": "on_confirm", "clientAction": "noop"},
        {
            "agent": "character_designer",
            "action": "lock_identity",
            "trigger": "with_script",
            "clientAction": "noop",
        },
        {
            "agent": "scene_creator",
            "action": "build_scene_board",
            "trigger": "with_script",
            "clientAction": "noop",
        },
        {
            "agent": "animator",
            "action": "generate_shot",
            "trigger": "on_user_generate",
            "clientAction": "noop",
        },
        {"agent": "editor", "action": "assemble_timeline", "trigger": "after_shots", "clientAction": "noop"},
        {
            "agent": "sound_engineer",
            "action": "score_and_sfx",
            "trigger": "with_edit",
            "clientAction": "noop",
        },
    ],
    # 第一视角催泪短片：agent_recipe，故事板 → 确认 → 成片（非客户端 Wizard）
    "pov_tearjerker_short": [
        {"agent": "orchestrator", "action": "analyze_brief", "trigger": "on_confirm", "clientAction": "noop"},
        {"agent": "orchestrator", "action": "design_pov_arc", "trigger": "with_brief", "clientAction": "noop"},
        {"agent": "orchestrator", "action": "storyboard", "trigger": "with_arc", "clientAction": "noop"},
        {"agent": "orchestrator", "action": "layout_and_ask", "trigger": "after_board", "clientAction": "noop"},
        {
            "agent": "orchestrator",
            "action": "batch_generate",
            "trigger": "on_user_generate",
            "clientAction": "noop",
        },
    ],
}

# action → 默认 clientAction（DB 仅存 agent/action 时补齐）
_ACTION_CLIENT: dict[str, str] = {
    "storyboard_from_video": "run_analyze",
    "extract_subjects": "run_analyze",
    "batch_remake_clips": "run_batch",
    # Wizard 仍 noop；Agent 续聊早路径写 OSS 草稿 + canvasOp open_video_editor
    "assemble_timeline": "noop",
    "storyboard_overseas_localize": "run_analyze",
    "localize_subjects": "run_analyze",
    "batch_overseas_clips": "run_batch",
}

_ACTION_TRIGGER: dict[str, str] = {
    "storyboard_from_video": "on_video_upload",
    "extract_subjects": "with_analyze",
    "batch_remake_clips": "on_confirm_generate",
    "assemble_timeline": "reserved",
    "storyboard_overseas_localize": "on_video_upload",
    "localize_subjects": "with_analyze",
    "batch_overseas_clips": "on_confirm_generate",
}

_WIZARD_KINDS = frozenset({"viral_remake", "overseas_localize"})


def _normalize_step(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    agent = str(raw.get("agent") or "").strip()
    action = str(raw.get("action") or "").strip()
    if not agent or not action:
        return None
    trigger = str(raw.get("trigger") or _ACTION_TRIGGER.get(action) or "manual").strip()
    client = str(raw.get("clientAction") or raw.get("client_action") or "").strip()
    if not client:
        client = _ACTION_CLIENT.get(action) or "noop"
    out: dict[str, Any] = {
        "agent": agent,
        "action": action,
        "trigger": trigger,
        "clientAction": client,
    }
    note = str(raw.get("note") or "").strip()
    if note:
        out["note"] = note
    return out


def _normalize_steps(steps: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in steps or []:
        step = _normalize_step(raw)
        if step:
            out.append(step)
    return out


def resolve_pipeline(
    skill: Skill | None,
    *,
    slug: str | None = None,
    doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析 Skill 可执行 pipeline（MD YAML 优先，其次 DB，再次默认表）。

    doc 可由调用方传入（含库覆盖）；未传则同步读文件默认。
    """
    kind = ""
    skill_slug = (slug or (skill.slug if skill else "") or "").strip()
    if skill is not None:
        kind = (skill.entry_kind or "").strip()
    if doc is None:
        doc = load_skill_doc(skill_slug) if skill_slug else None
    md_pipe = parse_pipeline_from_markdown((doc or {}).get("raw") or "") if doc else None

    execution = "server_team"
    entry_kind = kind or skill_slug
    steps: list[dict[str, Any]] = []

    if md_pipe:
        execution = str(md_pipe.get("execution") or execution)
        entry_kind = str(md_pipe.get("entryKind") or entry_kind)
        steps = _normalize_steps(md_pipe.get("steps"))
        source = "skill_docs"
    elif skill is not None and isinstance(skill.pipeline, list) and skill.pipeline:
        steps = _normalize_steps(list(skill.pipeline))
        source = "db"
        if kind in _WIZARD_KINDS:
            execution = "client_wizard"
    else:
        fallback_key = kind or skill_slug
        steps = _normalize_steps(_DEFAULT_PIPELINES.get(fallback_key))
        source = "default"
        if fallback_key in _WIZARD_KINDS:
            execution = "client_wizard"

    if not steps and kind in _WIZARD_KINDS:
        steps = _normalize_steps(_DEFAULT_PIPELINES.get(kind))
        execution = "client_wizard"
        source = "default"

    return {
        "slug": skill_slug,
        "entryKind": entry_kind,
        "execution": execution,
        "source": source,
        "pipeline": steps,
        "title": (skill.title if skill else None) or (doc or {}).get("title") or skill_slug,
    }


def steps_for_trigger(pipeline: dict[str, Any], trigger: str) -> list[dict[str, Any]]:
    t = (trigger or "").strip()
    return [s for s in (pipeline.get("pipeline") or []) if str(s.get("trigger") or "") == t]


def primary_client_action(steps: list[dict[str, Any]]) -> str | None:
    """取步骤中第一个非 noop 的 clientAction。"""
    for s in steps:
        ca = str(s.get("clientAction") or "noop")
        if ca and ca != "noop":
            return ca
    return None


def plan_client_run(
    skill: Skill | None,
    *,
    event: str,
    brief: dict[str, Any] | None = None,
    doc: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """
    根据用户事件规划前端应执行的动作。

    event:
      - video_ready：已解析到参考视频
      - confirm_generate：用户确认成片
      - generate_subjects：用户一键生成主体图
    """
    if skill is None:
        return None
    resolved = resolve_pipeline(skill, doc=doc)
    if resolved.get("execution") != "client_wizard":
        return None

    ev = (event or "").strip()
    if ev == "video_ready":
        steps = steps_for_trigger(resolved, "on_video_upload")
        if not steps:
            steps = [
                s
                for s in resolved["pipeline"]
                if str(s.get("clientAction")) == "run_analyze"
            ]
        action = primary_client_action(steps) or "run_analyze"
        # MD/默认表声明 noop 时不触发前端流水线
        if action == "noop":
            return {
                **resolved,
                "event": ev,
                "clientAction": "noop",
                "activeSteps": steps,
                "message": "请使用对应向导完成流水线",
            }
        return {
            **resolved,
            "event": ev,
            "clientAction": action,
            "activeSteps": steps,
            "brief": brief,
        }

    if ev == "confirm_generate":
        steps = steps_for_trigger(resolved, "on_confirm_generate")
        action = primary_client_action(steps) or "run_batch"
        if action == "noop":
            return None
        return {
            **resolved,
            "event": ev,
            "clientAction": action,
            "activeSteps": steps,
        }

    if ev == "generate_subjects":
        # 主体图：独立客户端动作，不与拉片/成片混淆
        return {
            **resolved,
            "event": ev,
            "clientAction": "run_subjects",
            "activeSteps": [],
            "message": "正在生成主体图",
        }

    return None


def attach_skill_run_flags(result: dict[str, Any], plan: dict[str, Any] | None) -> None:
    """把 Runner 规划写入会话 API 响应（兼容旧 runViralRemake* 字段）。"""
    if not plan:
        return
    result["skillRun"] = {
        "slug": plan.get("slug"),
        "entryKind": plan.get("entryKind"),
        "execution": plan.get("execution"),
        "source": plan.get("source"),
        "pipeline": plan.get("pipeline") or [],
        "event": plan.get("event"),
        "clientAction": plan.get("clientAction"),
        "activeSteps": plan.get("activeSteps") or [],
        "message": plan.get("message"),
    }
    action = str(plan.get("clientAction") or "")
    brief = plan.get("brief")
    if action == "run_analyze" and isinstance(brief, dict):
        result["runViralRemake"] = brief
    elif action == "run_batch":
        result["runViralRemakeBatch"] = True
    elif action == "run_subjects":
        result["runViralRemakeSubjects"] = True

def pipeline_for_seed_sync(slug: str) -> list[dict[str, str]] | None:
    """供 ensure_platform_skills 从 MD 同步 DB pipeline（仅 agent/action）。"""
    doc = load_skill_doc(slug)
    if not doc:
        return None
    md_pipe = parse_pipeline_from_markdown(doc.get("raw") or "")
    steps = _normalize_steps((md_pipe or {}).get("steps"))
    if not steps:
        return None
    return [{"agent": s["agent"], "action": s["action"]} for s in steps]
