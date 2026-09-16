"""Agent 澄清反问：先对齐需求/故事方向，用户确认后再跑完整 Team。

消息 artifacts 约定：
- kind=asked_summary: { items: [{q,a}] }
- kind=ask_choices: { prompt, options:[{id,label}], status: pending|answered|ignored }
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.llm.chat import chat_completion
from ..models.agent_session import AgentSession, AgentSessionMessage
from ..models.user import User
from ..services import agent_sessions
from ..services.agent_controller import get_session_controller_model
from ..services.agent_vision_refs import (
    build_controller_user_content,
    collect_vision_image_urls,
    pick_vision_controller_model,
)

logger = logging.getLogger(__name__)

_CLARIFY_SYSTEM = """你是聚梦画布创作助手。根据用户输入与（若有）Skill 规格给出创作方案，请用户确认后再建节点。
你需要：
1) 归纳已确认的需求（已询问）
2) 提出可执行的初步方案（含节点数量、风格/规格要点、每类节点将写什么）
3) 给出 4 个选项让用户确认或修改

只输出严格 JSON（不要 Markdown 围栏）：
{
  "asked": [{"q":"问题标题","a":"用户侧已确认的答案"}],
  "ack": "一句确认话术",
  "proposal": "方案描述（3～8句：规格、将建哪些节点、连线关系、各节点内容要点）",
  "question": "是否按这个方案继续？（同意后我会建节点并填内容，生成前会再问你一次）",
  "options": [
    {"id":"confirm","label":"可以，按这个方案建节点并填内容"},
    {"id":"tweak","label":"方向可以，但想改某个细节（请说明）"},
    {"id":"rewrite","label":"想换成完全不同的方案，我来描述"},
    {"id":"other","label":"其他，请直接输入"}
  ]
}
asked 至少 1～3 条；options 必须含 confirm / tweak / rewrite / other 四个 id。
若提供了 Skill 规格：遵守核心约束（如 mediaKind、品牌/产品边界）；镜头数与结构优先按你根据技能包与用户需求设计的方案，勿机械套固定分镜表；proposal 写清画幅、镜头要点与生成路径。
若消息附带参考图（多模态附图）：必须根据画面内容写 proposal（主体/风格/构图），并说明将把附件落到参考图节点（assetId）。"""


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


def _template_clarify(idea: str, *, skill_hint: str = "") -> dict[str, Any]:
    idea = (idea or "").strip() or "一个温暖的短片"
    hint = (skill_hint or "").strip()
    asked = [
        {"q": "创作内容", "a": f"用户想法：{idea}（细节可由我来补全）"},
    ]
    if hint:
        asked.append({"q": "Skill 规格", "a": hint[:200]})
    else:
        asked.append(
            {"q": "风格与规格", "a": "按默认规格；可在选项中要求调整页数/画幅/风格"}
        )
    return {
        "asked": asked,
        "ack": "好的，我先按你的输入和技能规格构思一版可执行方案。",
        "proposal": (
            f"围绕「{idea[:80]}」："
            + (f"遵守 Skill「{hint[:60]}」；" if hint else "")
            + "将先规划节点清单与各节点要点，你确认后再建节点填内容；生成前会再问你一次。"
        ),
        "question": "是否按这个方案继续？（同意后我会建节点并填内容，生成前会再问你一次）",
        "options": [
            {"id": "confirm", "label": "可以，按这个方案建节点并填内容"},
            {"id": "tweak", "label": "方向可以，但想改某个细节（请说明）"},
            {"id": "rewrite", "label": "想换成完全不同的方案，我来描述"},
            {"id": "other", "label": "其他，请直接输入"},
        ],
    }


async def _llm_clarify(
    idea: str,
    prior_asked: list[dict[str, str]] | None = None,
    *,
    preferred_model: str | None = None,
    skill_context: str | None = None,
    image_urls: list[str] | None = None,
) -> dict[str, Any]:
    model = pick_vision_controller_model(
        preferred_model, has_images=bool(image_urls)
    )
    skill_ctx = (skill_context or "").strip()
    if not model:
        return _template_clarify(idea, skill_hint=skill_ctx[:120])
    prior = ""
    if prior_asked:
        prior = "已确认：" + json.dumps(prior_asked, ensure_ascii=False)
    user_parts = [f"用户创意：{idea}"]
    if skill_ctx:
        from .agent_context_budget import BUDGET_SKILL_APPENDIX, clip_budget

        user_parts.append(
            "Skill 规格（必须遵守）：\n"
            + clip_budget(skill_ctx, BUDGET_SKILL_APPENDIX, label="技能")
        )
    if prior:
        user_parts.append(prior)
    try:
        user_content = build_controller_user_content(
            "\n\n".join(user_parts), model_id=model, image_urls=image_urls
        )
        content = await chat_completion(
            model,
            [
                {"role": "system", "content": _CLARIFY_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0.55,
            max_tokens=3072,
            timeout_s=90.0,
        )
        parsed = _parse_json_obj(content)
        if not parsed:
            return _template_clarify(idea, skill_hint=skill_ctx[:120])
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("clarify LLM failed: %s", exc)
        return _template_clarify(idea, skill_hint=skill_ctx[:120])


def _normalize_clarify(raw: dict[str, Any], idea: str) -> dict[str, Any]:
    tpl = _template_clarify(idea)
    asked = []
    for row in raw.get("asked") or []:
        if not isinstance(row, dict):
            continue
        q = str(row.get("q") or "").strip()
        a = str(row.get("a") or "").strip()
        if q and a:
            asked.append({"q": q, "a": a})
    if not asked:
        asked = tpl["asked"]

    options = []
    for row in raw.get("options") or []:
        if not isinstance(row, dict):
            continue
        oid = str(row.get("id") or "").strip()
        label = str(row.get("label") or "").strip()
        if oid and label:
            options.append({"id": oid, "label": label})
    # 保证四类 id 齐全
    by_id = {o["id"]: o for o in options}
    for o in tpl["options"]:
        by_id.setdefault(o["id"], o)
    options = [by_id[k] for k in ("confirm", "tweak", "rewrite", "other") if k in by_id]

    return {
        "asked": asked[:5],
        "ack": str(raw.get("ack") or tpl["ack"]).strip(),
        "proposal": str(raw.get("proposal") or tpl["proposal"]).strip(),
        "question": str(raw.get("question") or tpl["question"]).strip(),
        "options": options,
    }


def get_session_brief(session: AgentSession) -> dict[str, Any]:
    raw = getattr(session, "brief_json", None)
    return dict(raw) if isinstance(raw, dict) else {}


def set_session_brief(session: AgentSession, brief: dict[str, Any]) -> None:
    session.brief_json = brief


def _skill_context_from_brief(brief: dict[str, Any]) -> str:
    """从 brief.mySkillDefaults 拼澄清/方案上下文。"""
    my = brief.get("mySkillDefaults") if isinstance(brief.get("mySkillDefaults"), dict) else {}
    if not my:
        return ""
    from .skill_node_recipe import normalize_node_recipe, recipe_markdown_sections

    kind = str(my.get("mediaKind") or "video")
    recipe = normalize_node_recipe(my.get("nodeRecipe"), media_kind=kind)
    nodes_md, order, specs_md = recipe_markdown_sections(recipe)
    from .agent_canvas_manual import MY_SKILL_CONTROL_APPENDIX, canvas_manual_for_skill_doc

    bits = [
        f"mediaKind={kind}",
        f"idea={my.get('idea') or ''}",
        f"aspectRatio={my.get('aspectRatio') or ''}",
        f"clarity={my.get('clarity') or ''}",
        f"durationSec={my.get('durationSec') or ''}",
        "## 规格\n" + specs_md,
        "## 节点\n" + nodes_md,
        "## 编排\n" + order,
        "## 画布操控速查\n" + canvas_manual_for_skill_doc(),
        MY_SKILL_CONTROL_APPENDIX,
    ]
    return "\n".join(bits)


async def _skill_doc_context_for_session(
    db: AsyncSession,
    session: AgentSession,
) -> str:
    """绑定 Skill：与 runtime 一致，目录 + SKILL.md（渐进披露，非整包 references）。"""
    if not session.skill_id:
        return ""
    try:
        from sqlalchemy import select

        from ..models.skill import Skill
        # 与 runtime 一致：目录 + 绑定 SKILL.md，禁止整包 28KB references
        from .skill_docs import format_runtime_skill_context

        skill = (
            await db.execute(
                select(Skill).where(Skill.id == int(session.skill_id)).limit(1)
            )
        ).scalar_one_or_none()
        if skill is None:
            return ""
        return await format_runtime_skill_context(db, bound_skill=skill)
    except Exception as exc:  # noqa: BLE001
        logger.warning("load skill doc context failed: %s", exc)
        return ""


async def get_session_skill_canvas_rules(
    db: AsyncSession,
    session: AgentSession,
) -> str:
    """读取会话绑定 Skill 的画布规则覆盖（skills.canvas_rules_markdown）。

    供续聊 system prompt 追加为最高优先级附录，解决通用规则（如 MY_SKILL_
    CONTROL_APPENDIX「多视频走分镜表」）与该技能实际诉求冲突的问题。
    """
    if not session.skill_id:
        return ""
    try:
        from sqlalchemy import select

        from ..models.skill import Skill

        skill = (
            await db.execute(
                select(Skill).where(Skill.id == int(session.skill_id)).limit(1)
            )
        ).scalar_one_or_none()
        if skill is None:
            return ""
        return str(getattr(skill, "canvas_rules_markdown", None) or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("load skill canvas rules failed: %s", exc)
        return ""


async def resolve_session_skill_context(
    db: AsyncSession,
    session: AgentSession,
    brief: dict[str, Any] | None = None,
) -> str:
    """合并我的 Skill 配方 + 平台 Skill 文档（包则整包），供澄清与 Team 使用。"""
    b = brief if isinstance(brief, dict) else get_session_brief(session)
    bits: list[str] = []
    my_ctx = _skill_context_from_brief(b)
    if my_ctx.strip():
        bits.append(my_ctx.strip())
    doc_ctx = await _skill_doc_context_for_session(db, session)
    if doc_ctx.strip():
        bits.append(doc_ctx.strip())
    # 用户已选画幅/时长仅作软提示，不强制固定分镜编排
    ar = str(b.get("aspectRatio") or "").strip()
    dur = str(b.get("durationSec") or "").strip()
    if ar or dur:
        soft = "用户当前偏好（可改，非强制固定编排）："
        if ar:
            soft += f"画幅 {ar}；"
        if dur:
            soft += f"时长约 {dur} 秒；"
        bits.append(soft.rstrip("；") + "。")
    return "\n\n".join(bits).strip()


async def run_clarify_round(
    db: AsyncSession,
    *,
    session: AgentSession,
    user: User,
    idea: str,
) -> None:
    """产出一轮反问：已询问摘要 + 方案选项。"""
    idea = (idea or "").strip() or "一个温暖的短片"
    brief = get_session_brief(session)
    prior_asked = brief.get("asked") if isinstance(brief.get("asked"), list) else []
    prior_asked_norm = [
        {"q": str(x.get("q") or ""), "a": str(x.get("a") or "")}
        for x in prior_asked
        if isinstance(x, dict)
    ]
    skill_ctx = await resolve_session_skill_context(db, session, brief)
    ref_ids = [
        str(x).strip()
        for x in (brief.get("referenceAssetIds") or [])
        if str(x).strip()
    ]
    image_urls = await collect_vision_image_urls(
        db,
        session.project_id,
        message_text=idea,
        reference_asset_ids=ref_ids,
    )

    await agent_sessions.append_message(
        db,
        session,
        role="agent",
        agent_role="orchestrator",
        content=(
            "我先根据你的输入和技能规格整理一版方案，确认后再建节点填内容。"
            if skill_ctx
            else "我需要先了解你的具体需求，才能更好地规划创作方案。"
        ),
    )

    raw = await _llm_clarify(
        idea,
        prior_asked_norm or None,
        preferred_model=get_session_controller_model(session),
        skill_context=skill_ctx or None,
        image_urls=image_urls or None,
    )
    norm = _normalize_clarify(raw, idea)

    # 合并已询问
    merged: list[dict[str, str]] = []
    seen_q: set[str] = set()
    for row in [*prior_asked_norm, *norm["asked"]]:
        q = row["q"]
        if q in seen_q:
            # 同题覆盖答案
            for m in merged:
                if m["q"] == q:
                    m["a"] = row["a"]
                    break
        else:
            seen_q.add(q)
            merged.append(dict(row))

    # 合并 brief：保留 mySkillDefaults / 模型偏好等，禁止整表覆盖丢失
    prev_ctrl = get_session_controller_model(session)
    next_brief = dict(brief)
    next_brief["idea"] = idea
    next_brief["asked"] = merged
    next_brief["draftStory"] = norm["proposal"]
    next_brief["mySkillPhase"] = "awaiting_plan_confirm"
    if prev_ctrl:
        next_brief["controllerModel"] = prev_ctrl
    set_session_brief(session, next_brief)

    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        agent_role="orchestrator",
        content=norm["ack"],
        artifacts=[{"kind": "asked_summary", "items": merged}],
    )

    body = f"{norm['proposal']}\n\n{norm['question']}"
    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        agent_role="orchestrator",
        content=body,
        artifacts=[
            {
                "kind": "ask_choices",
                "prompt": norm["question"],
                "proposal": norm["proposal"],
                "options": norm["options"],
                "status": "pending",
            }
        ],
    )
    session.status = "awaiting_user"
    await db.flush()


async def run_session_clarify_job(session_id: int, idea: str) -> None:
    """后台：澄清反问。"""
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
            await run_clarify_round(db, session=session, user=user, idea=idea)
            from .agent_session_credits import commit_orchestration_credits

            await commit_orchestration_credits(db, session)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("clarify session %s failed: %s", session_id, exc)
            async with async_session() as db2:
                try:
                    session = (
                        await db2.execute(
                            select(AgentSession)
                            .where(AgentSession.id == int(session_id))
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if session is not None:
                        session.status = "failed"
                        await agent_sessions.append_message(
                            db2,
                            session,
                            role="assistant",
                            agent_role="orchestrator",
                            content=f"澄清反问失败：{exc}",
                        )
                        from .agent_session_credits import release_orchestration_credits

                        await release_orchestration_credits(db2, session)
                        await db2.commit()
                except Exception:  # noqa: BLE001
                    await db2.rollback()


async def _mark_pending_choices_answered(db: AsyncSession, session_id: int) -> None:
    rows = (
        await db.execute(
            select(AgentSessionMessage)
            .where(AgentSessionMessage.session_id == int(session_id))
            .order_by(AgentSessionMessage.seq.desc())
            .limit(20)
        )
    ).scalars().all()
    for msg in rows:
        arts = msg.artifacts
        if not isinstance(arts, list):
            continue
        changed = False
        new_arts = []
        for a in arts:
            if isinstance(a, dict) and a.get("kind") == "ask_choices" and a.get("status") == "pending":
                a = {**a, "status": "answered"}
                changed = True
            new_arts.append(a)
        if changed:
            msg.artifacts = new_arts
            await db.flush()
            break


async def handle_user_clarify_reply(
    db: AsyncSession,
    user: User,
    session: AgentSession,
    *,
    message: str,
    choice_id: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    """处理 awaiting_user 下的选项/文本；返回调度标志。"""
    act = (action or "submit").strip().lower()
    choice = (choice_id or "").strip().lower() or None
    text = (message or "").strip()

    if act == "ignore":
        await agent_sessions.append_message(
            db, session, role="user", content="（已忽略本次询问）", skill_id=session.skill_id
        )
        await _mark_pending_choices_answered(db, int(session.id))
        session.status = "cancelled"
        from .agent_session_credits import release_orchestration_credits

        await release_orchestration_credits(db, session)
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            agent_role="orchestrator",
            content="已忽略。你可以继续在输入框补充想法，或重新发起创作。",
        )
        return {"_scheduleTeam": False, "_scheduleClarify": False}

    brief = get_session_brief(session)
    idea = str(brief.get("idea") or text or session.title or "短片")
    draft = str(brief.get("draftStory") or "")

    # 记录用户选择
    display = text
    if choice == "confirm" and not text:
        display = "可以，按这个方向写具体分镜"
    elif choice and text:
        display = text
    elif choice:
        # 从 pending options 取 label
        display = choice
    await agent_sessions.append_message(
        db, session, role="user", content=display or "继续", skill_id=session.skill_id
    )
    await _mark_pending_choices_answered(db, int(session.id))

    # 确认 → 宣传片 / 画布操控优先走 followup；其余跑完整 Team
    if choice == "confirm" or (
        not choice
        and text in ("可以", "确认", "好的", "按这个方向", "按这个方案", "同意", "开始")
    ):
        enriched = idea
        if draft:
            enriched = f"{idea}\n\n确认方案：{draft}"
        asked = brief.get("asked") if isinstance(brief.get("asked"), list) else []
        if asked:
            lines = [f"- {x.get('q')}: {x.get('a')}" for x in asked if isinstance(x, dict)]
            enriched += "\n\n已确认需求：\n" + "\n".join(lines)
        skill_ctx = await resolve_session_skill_context(db, session, brief)
        if skill_ctx:
            from .agent_context_budget import BUDGET_SKILL_APPENDIX, clip_budget

            enriched += (
                "\n\n【必须遵守的 Skill 规格】\n"
                + clip_budget(skill_ctx, BUDGET_SKILL_APPENDIX, label="技能")
            )
        brief["confirmed"] = True
        brief["mySkillPhase"] = "building_canvas"

        # 电影级宣传片 / preferCanvasManual：确认后用画布操控，不跑固定 Team 编排
        prefer_canvas = bool(brief.get("preferCanvasManual"))
        if not prefer_canvas and session.skill_id:
            try:
                from ..models.skill import Skill as SkillModel

                sk = (
                    await db.execute(
                        select(SkillModel)
                        .where(SkillModel.id == int(session.skill_id))
                        .limit(1)
                    )
                ).scalar_one_or_none()
                prefer_canvas = agent_sessions._prefers_canvas_manual(sk)
            except Exception:  # noqa: BLE001
                prefer_canvas = False

        if prefer_canvas:
            brief["preferCanvasManual"] = True
            brief["genMode"] = "canvas"
            set_session_brief(session, brief)
            session.status = "active"
            await agent_sessions.append_message(
                db,
                session,
                role="assistant",
                agent_role="orchestrator",
                content=(
                    "好的，开始按确认方案用「画布操控」搭节点与连线（遵循完整技能包，不套固定分镜模板）。"
                    "参考图会落到画布；完成后我会再问你是否开始生成——在那之前不会扣媒体生成算力。"
                ),
            )
            followup_msg = (
                f"{enriched}\n\n"
                "请严格按完整技能包与确认方案，用画布操控 JSON 落节点与连线；"
                "若有 referenceAssetIds / `[附件:…|assetId=…]`，必须先建参考图节点（params.assetId）"
                "并接线到分镜/视频节点；本轮不要 generate，完成后询问是否开始生成。"
            )
            return {
                "_scheduleFollowup": True,
                "_followupMessage": followup_msg,
                "_scheduleTeam": False,
                "_scheduleClarify": False,
            }

        set_session_brief(session, brief)
        session.status = "active"
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            agent_role="orchestrator",
            content=(
                "好的，开始按确认方案编排画布（建节点、连线并填入内容）。"
                "完成后我会再问你是否开始生成——在那之前不会扣媒体生成算力。"
            ),
        )
        return {"_scheduleTeam": True, "_teamIdea": enriched, "_scheduleClarify": False}

    # 改细节 / 重写 / 其他 → 再问一轮
    note = text or display
    asked = list(brief.get("asked") or []) if isinstance(brief.get("asked"), list) else []
    if choice == "tweak":
        asked.append({"q": "修改细节", "a": note or "用户希望调整细节"})
        next_idea = f"{idea}\n用户希望调整：{note}"
    elif choice == "rewrite":
        asked.append({"q": "新剧情", "a": note or "用户要换剧情"})
        next_idea = note or idea
        brief["idea"] = next_idea
    else:
        asked.append({"q": "补充说明", "a": note})
        next_idea = f"{idea}\n用户补充：{note}"
    brief["asked"] = asked
    set_session_brief(session, brief)
    session.status = "active"
    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        agent_role="orchestrator",
        content="收到，我根据你的反馈再对一下方向…",
    )
    return {"_scheduleTeam": False, "_scheduleClarify": True, "_teamIdea": next_idea}
