"""AI 助手 tool-call 循环：每轮先读最新画布，再让模型选工具落地。

无前端快照时用最新工作流合成目录。客户端工具写成 canvasOps，等前端投影后回传 tool-results。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..integrations.llm.chat import (
    chat_completion_tools,
    evolving_tools_call_kwargs,
    is_evolving_controller,
)
from ..models.agent_session import AgentSession, AgentSessionMessage
from ..models.user import User
from . import agent_sessions, project_graphs
from .agent_controller import get_session_controller_model
from .agent_followup import (
    _focused_content_block,
    _snapshot_catalog,
)
from .agent_tools import (
    CLIENT_TOOL_NAMES,
    SERVER_TOOL_NAMES,
    TERMINAL_TOOL_NAMES,
    client_snapshot_usable,
    client_tool_to_canvas_ops,
    execute_server_tool,
    openai_tool_schemas,
    snapshot_from_latest_workflow,
    tool_call_name_args,
)
from .agent_canvas_tool_names import format_named_tools_hint
from .agent_context_budget import (
    BUDGET_HISTORY_MEMO,
    BUDGET_HISTORY_OLDER,
    BUDGET_HISTORY_RECENT,
    BUDGET_SKILL_APPENDIX,
    BUDGET_SYSTEM_RULES,
    BUDGET_TOOL_RESULT,
    BUDGET_USER_TURN_EXTRA,
    apply_observe_budgets,
    clip_budget,
)
from .agent_generate_intent import (
    GENERATE_DONE_TEXT,
    STORYBOARD_DONE_TEXT,
    VIDEO_CONFIRM_ASK_OPTIONS,
    VIDEO_CONFIRM_ASK_PROMPT,
    apply_client_intent_filter,
    assistant_message_for_client_calls,
    assistant_message_keeping_tool_calls,
    evolving_use_deep_thinking,
    last_user_intent_text,
    should_stop_after_tool_results,
    specified_nodes,
    successful_generate_in_tool_results,
)
from .agent_context_compact import compact_role_contents
from .agent_vision_refs import (
    build_controller_user_content,
    collect_vision_image_urls,
    collect_vision_video_urls,
    pick_vision_controller_model,
)

logger = logging.getLogger(__name__)

_RULES_PATH = Path(__file__).resolve().parent / "agent_rules.md"


async def _mark_pending_ask_choices(
    db: AsyncSession, session_id: int, *, status: str = "answered"
) -> None:
    """把最近一条 pending 的 ask_choices 标为已答/已忽略，前端询问条才会收起。"""
    if status not in ("answered", "ignored"):
        status = "answered"
    rows = (
        (
            await db.execute(
                select(AgentSessionMessage)
                .where(AgentSessionMessage.session_id == int(session_id))
                .order_by(AgentSessionMessage.seq.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    for msg in rows:
        arts = msg.artifacts
        if not isinstance(arts, list):
            continue
        changed = False
        new_arts: list[Any] = []
        for item in arts:
            if (
                isinstance(item, dict)
                and item.get("kind") == "ask_choices"
                and item.get("status") == "pending"
            ):
                new_arts.append({**item, "status": status})
                changed = True
            else:
                new_arts.append(item)
        if changed:
            msg.artifacts = new_arts
            flag_modified(msg, "artifacts")
            await db.flush()
            break


_MAX_SERVER_ROUNDS = 6
# Evolving 同轮多读、多步编排：略提高服务端轮次；打满文案不变
_MAX_SERVER_ROUNDS_EVOLVING = 10
_TOOL_RESULT_CHARS = BUDGET_TOOL_RESULT
# 本会话约 20 轮；较早消息截短，最近 4 条保留更长
_HISTORY_CAP = 40
_HISTORY_RECENT_FULL = 4
_HISTORY_RECENT_CHARS = BUDGET_HISTORY_RECENT
_HISTORY_OLDER_CHARS = BUDGET_HISTORY_OLDER
_NARRATION_KINDS = frozenset({"skill_progress", "viral_progress", "skill_done"})


def max_server_rounds_for_model(model_id: str) -> int:
    """Evolving 略提高服务端读轮次；其它控制器保持 6。"""
    if is_evolving_controller(model_id):
        return _MAX_SERVER_ROUNDS_EVOLVING
    return _MAX_SERVER_ROUNDS


def load_agent_rules() -> str:
    """读取注入 LLM 的规则手册。"""
    try:
        text = _RULES_PATH.read_text(encoding="utf-8")
    except OSError:
        text = "你是聚梦画布创作助手。每轮先读画布，再用工具布点。不要写死流程。"
    return text.strip()


def _system_prompt(*, skill_appendix: str = "", chat_only: bool = False) -> str:
    rules = clip_budget(load_agent_rules(), BUDGET_SYSTEM_RULES, label="规则手册")
    parts = [
        rules,
        "",
        "工具用法见函数 schema。开始思考时必须已经读过本轮画布目录（get_canvas_state）。",
        "目录不含非焦点节点正文；改那些节点的文案/提示词前必须 inspect_node（可一次传 nodeIds 最多 20，少来回）。",
        "同一轮应并行多个读工具（inspect_node / list_models / list_canvas_tools / list_skills / get_canvas_state），读完再写画布。",
        "本轮已注入【画布工具速查】（官方名/id/功能特点/主模型）和画布已有/首选模型的输入上限。"
        "选工具先对照速查；连参考、写 generationOptions、换未列出的模型前对照能力块或再调 list_models。params 细节再 list_canvas_tools。",
        "本轮已注入【技能目录】（name+description）。匹配任务或用户点名技能时用 load_skill；references 用 load_skill_file。"
        "消息里的 $product-cinematic-commercial 等为显式点名，必须 load（已预载则可跳过）。禁止执行 scripts/，只用画布工具落地。",
        "长配方可用 update_plan 更新可见进度（pending/in_progress/completed，同时仅一步 in_progress）；勿用 plan 当状态机。",
        "update_node_params 只传要改的字段（对齐 apply_patch）；禁止为改一句 prompt 重发整份 generationOptions。",
        "写画布工具由前端执行；你调用后会收到落地结果与更新后的目录。",
        "用户点名的画布工具必须用官方全名对应的 tool id；本轮若有【本轮点名工具】或【本轮推断工具】必须按其 id 调用。高置信口语（看背面/补光等）勿再 ask_user。",
        "消息里的 [附件:…|assetId=…] 与 [节点:…|nodeId=…] 是用户本轮指定的参考，必须用这些 id 落到画布，禁止视而不见。",
        "用户准星点名了节点并说「生成视频」：对已有节点短出片（图生视频用已连到该图的 video_input）；没有点名才 add_node。准星 +「确认生成/做宣传片」仍走技能配方，不要短出片硬拦。本轮 generate 已成功后禁止再生成。",
        "本轮用户已说「确认生成 / 确认并生成 / 生成同款 / 开始成片 / 批量成片」时：禁止再 ask_user 问是否出片，须同轮 generate_node。",
        "用户只要「一张故事板 / 生成故事板」：只出一张合成图；禁止 add_node video_input、禁止再出第二张板；故事板工具成功后必须收束。「5s」「电影级」「出海风」「出片感」等风格/口癖单独出现不算要视频或整段配方。",
        "多角度/打光/九宫格/抠图/扩图/高清等单次编辑工具成功后必须收束，禁止再铺点或搭空视频。未说要视频时不要 add_node video_input；未「确认生成」不要对视频 generate。",
    ]
    if chat_only:
        parts.append("本轮用户只要对话：不要调用写画布 / 生成 / 跑工具，只用文字或 ask_user。")
    if skill_appendix.strip():
        parts.append("")
        parts.append("【可选配方 · 非强制流程】")
        parts.append(
            clip_budget(
                skill_appendix.strip(),
                BUDGET_SKILL_APPENDIX,
                label="技能附录",
            )
        )
    return "\n".join(parts)


def _is_narration_message(msg: AgentSessionMessage) -> bool:
    """拉片/生成进度旁白不进 LLM 历史；ask_user 选项必须保留。"""
    patch = msg.graph_patch if isinstance(msg.graph_patch, dict) else {}
    kind = str(patch.get("_narration") or "").strip()
    if kind in _NARRATION_KINDS:
        return True
    if str(msg.role or "") != "assistant" or not msg.artifacts:
        return False
    arts = msg.artifacts if isinstance(msg.artifacts, list) else []
    # 只滤素材旁白，勿把 ask_choices 问句从历史里丢掉
    return any(
        isinstance(a, dict) and str(a.get("kind") or "") == "media_assets" for a in arts
    )


def _history_messages(
    rows: list[AgentSessionMessage],
    *,
    skip_user_content: str | None = None,
    context_memo: str = "",
    skill_hint: str = "",
) -> tuple[list[dict[str, Any]], str]:
    """本会话 user/assistant 历史：去掉本轮用户原文与旁白。

    超阈值时用【会话备忘】替换早期原文（compaction），返回 (messages, 刷新后的 memo)。
    """
    skip = (skip_user_content or "").strip()
    picked: list[AgentSessionMessage] = []
    skipped_current_user = False
    for msg in reversed(rows):
        role = str(msg.role or "")
        if role not in ("user", "assistant"):
            continue
        if _is_narration_message(msg):
            continue
        content = str(msg.content or "").strip()
        if not content:
            continue
        if (
            not skipped_current_user
            and skip
            and role == "user"
            and content == skip
        ):
            skipped_current_user = True
            continue
        picked.append(msg)
        if len(picked) >= _HISTORY_CAP:
            break
    picked.reverse()

    role_contents: list[tuple[str, str]] = [
        (str(msg.role), str(msg.content or "").strip()) for msg in picked
    ]
    compacted, new_memo = compact_role_contents(
        role_contents,
        previous_memo=context_memo,
        skill_hint=skill_hint,
    )

    out: list[dict[str, Any]] = []
    total = len(compacted)
    for i, (role, content) in enumerate(compacted):
        # 备忘本身不截太狠；最近几条保留更长
        is_memo = content.startswith("【会话备忘】")
        recent = i >= total - _HISTORY_RECENT_FULL
        if is_memo:
            cap = BUDGET_HISTORY_MEMO
        elif recent:
            cap = _HISTORY_RECENT_CHARS
        else:
            cap = _HISTORY_OLDER_CHARS
        out.append({"role": role, "content": content[:cap]})
    return out, new_memo


def _observe_snapshot_blocks(
    snapshot: dict[str, Any],
    *,
    heading: str = "",
    tool_primary_models: dict[str, str] | None = None,
) -> str:
    """目录 + 焦点 + 工具速查 + 本轮模型能力（续跑与首轮同一套观察）。"""
    from .agent_model_caps import model_caps_block_for_snapshot
    from .agent_tool_catalog import format_canvas_tools_quickref

    catalog = _snapshot_catalog(snapshot)
    focused = _focused_content_block(snapshot).strip()
    if not focused:
        focused = "（焦点节点无短正文或未选中；改其它节点前请 inspect_node）"
    tools = format_canvas_tools_quickref(tool_primary_models)
    caps = model_caps_block_for_snapshot(snapshot)
    return apply_observe_budgets(
        catalog=catalog,
        focused=focused,
        tools=tools,
        caps=caps,
        heading=heading,
    )


_ATTACH_LINE_RE = re.compile(
    r"\[附件:([^\]|]*)\|assetId=([0-9a-zA-Z_-]{1,128})\]", re.IGNORECASE
)
_NODE_LINE_RE = re.compile(
    r"\[节点:([^\]|]*)\|nodeId=([0-9a-zA-Z_-]{1,128})"
    r"(?:\|type=([0-9a-zA-Z_-]+))?(?:\|assetId=([0-9a-zA-Z_-]{1,128}))?\]"
)


def _format_message_refs_hint(message: str) -> str:
    """把输入框附件与准星引用节点写成强制使用块。"""
    text = message or ""
    attaches = list(_ATTACH_LINE_RE.finditer(text))
    nodes = list(_NODE_LINE_RE.finditer(text))
    if not attaches and not nodes:
        return ""
    lines = ["【本轮用户指定的参考 · 必须使用，禁止视而不见】"]
    for m in attaches:
        name = (m.group(1) or "").strip() or "未命名"
        lines.append(
            f"- 附件「{name}」assetId={m.group(2)} → 落到对应图片/视频节点 params.assetId"
        )
    for m in nodes:
        extra = f" assetId={m.group(4)}" if m.group(4) else ""
        ntype = m.group(3) or "?"
        lines.append(
            f"- 准星引用节点 id={m.group(2)} 名称={(m.group(1) or '').strip() or '节点'} "
            f"type={ntype}{extra} → 本轮优先操作（focused）"
        )
    if nodes:
        lines.append(
            "用户要生成/出片时：对上述已有节点 generate_node，禁止再 add_node。"
            "点名的是图片且要出视频：对已连接到该图的 video_input 生成；"
            "没有这样的视频节点才允许新建一个并连上该图。本轮已提交生成后不要再生成。"
        )
    return "\n".join(lines)


def _turn_user_content(
    snapshot: dict[str, Any],
    message: str,
    *,
    tool_primary_models: dict[str, str] | None = None,
) -> str:
    """本轮 user 块：目录 + 焦点 + 点名工具 + 附件/引用节点 + $技能 + 本轮用户。"""
    from .agent_skill_mentions import (
        format_skill_mention_hint,
        parse_skill_dollar_mentions,
    )
    from .agent_generate_intent import allows_video_generate

    user_text = (message or "").strip() or "（无新文本，根据最新画布继续）"
    named = format_named_tools_hint(user_text)
    refs = _format_message_refs_hint(user_text)
    skill_hint = format_skill_mention_hint(parse_skill_dollar_mentions(user_text))
    # 用户已口头确认出片：禁止模型再 ask_user 追问确认
    confirm_hint = ""
    if allows_video_generate(user_text):
        confirm_hint = (
            "【本轮已确认出片】用户已说「确认生成 / 提交生成 / 搭线生成 / 开始吧 / 需要生成」"
            "或点了「确认生成」选项。"
            "禁止再 ask_user 或复述搭镜计划；须同轮对已就绪视频镜头 generate_node。"
            "若镜头节点未齐：先搭齐再 generate；节点已齐则立刻 generate_node。"
        )
    extra = ""
    if named:
        extra += f"\n\n{named}"
    if refs:
        extra += f"\n\n{refs}"
    if skill_hint:
        extra += f"\n\n{skill_hint}"
    if confirm_hint:
        extra += f"\n\n{confirm_hint}"
    if extra:
        extra = clip_budget(extra, BUDGET_USER_TURN_EXTRA, label="本轮提示")
    return (
        f"{_observe_snapshot_blocks(snapshot, tool_primary_models=tool_primary_models)}"
        f"{extra}\n\n【本轮用户】\n{user_text}"
    )


async def _skill_appendix(
    db: AsyncSession,
    session: AgentSession,
    *,
    message: str = "",
) -> str:
    """技能渐进披露：目录常驻；绑定或 $点名时预载 SKILL.md（不再整包 28KB）。"""
    from .agent_skill_mentions import parse_skill_dollar_mentions
    from .skill_docs import format_runtime_skill_context, load_skill_body_text

    skill = await agent_sessions._skill_row_for_session(db, session)
    base = (await format_runtime_skill_context(db, bound_skill=skill)).strip()
    mentions = parse_skill_dollar_mentions(message)
    if not mentions:
        return base
    bound_slug = str(getattr(skill, "slug", "") or "").strip().lower()
    extras: list[str] = []
    for name in mentions[:2]:
        # 已绑定同技能则 format_runtime_skill_context 已预载
        if bound_slug and name.replace("-", "_") in (
            bound_slug,
            bound_slug.replace("-", "_"),
        ):
            continue
        if bound_slug.replace("_", "-") == name.replace("_", "-"):
            continue
        body = await load_skill_body_text(db, name, skill_row=None)
        if body.startswith("{") and '"error"' in body[:80]:
            extras.append(f"【点名技能未找到】${name}：{body}")
        else:
            extras.append(f"【本轮 $ 点名预载 · {name}】\n{body}")
    if not extras:
        return base
    return (base + "\n\n" + "\n\n".join(extras)).strip()[:20000]


def runtime_stop_requested_from_brief(brief: dict[str, Any] | None) -> bool:
    """brief.runtimeStopRequested：用户点停止后禁止再写 canvasOps / 续思考。"""
    if not isinstance(brief, dict):
        return False
    return bool(brief.get("runtimeStopRequested"))


async def session_runtime_stop_requested(db: AsyncSession, session_id: int) -> bool:
    """从库重读会话（跨连接可见 stop 提交），避免后台任务用脏对象盖掉停止。"""
    row = (
        await db.execute(
            select(AgentSession)
            .where(AgentSession.id == int(session_id))
            .execution_options(populate_existing=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return True
    brief = row.brief_json if isinstance(row.brief_json, dict) else {}
    return runtime_stop_requested_from_brief(brief)


async def run_session_runtime(
    db: AsyncSession,
    *,
    session: AgentSession,
    user: User,
    message: str,
    canvas_snapshot: dict[str, Any] | None,
    tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """跑一轮思考。tool_results 为前端回传时续跑。"""
    raw_snap = canvas_snapshot if isinstance(canvas_snapshot, dict) else None
    snap = raw_snap if client_snapshot_usable(raw_snap) else None
    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    chat_only = str(brief.get("genMode") or "").strip().lower() == "chat"

    from .agent_runtime_events import append_runtime_event, consume_runtime_steer

    # 飞行中 steer：并入本轮意图（不整轮 interrupt）
    steer_msg = consume_runtime_steer(brief)
    effective_message = (message or "").strip()
    if steer_msg:
        append_runtime_event(
            brief, kind="steer_applied", message=steer_msg[:120]
        )
        if effective_message:
            effective_message = f"{effective_message}\n\n【飞行指令】{steer_msg}"
        else:
            effective_message = steer_msg
        message = effective_message
        session.brief_json = brief
        flag_modified(session, "brief_json")

    append_runtime_event(brief, kind="thinking", message="开始思考")
    session.brief_json = brief
    flag_modified(session, "brief_json")

    # 本轮注入后台实配的画布工具主模型（速查表「主模型=」）
    from .agent_canvas_tool_models import load_agent_tool_primary_models

    try:
        tool_primary_models = await load_agent_tool_primary_models(db)
    except Exception:  # noqa: BLE001
        tool_primary_models = None

    # 用户已点停止：本轮不再思考、不再投影
    if runtime_stop_requested_from_brief(brief):
        brief.pop("runtimeInflight", None)
        brief["pendingRuntime"] = False
        session.brief_json = brief
        flag_modified(session, "brief_json")
        session.status = "awaiting_user"
        await db.flush()
        return await agent_sessions.list_session_messages(
            db, user, str(session.id), after_seq=0
        )

    # 无前端快照：用最新工作流合成目录（OpenAPI / 外部 Agent），不要拒绝思考
    if snap is None:
        snap = await snapshot_from_latest_workflow(db, session.project_id)
        if isinstance(raw_snap, dict):
            for key in ("preferredImageModel", "preferredVideoModel"):
                val = str(raw_snap.get(key) or "").strip()
                if val:
                    snap[key] = val
        logger.info(
            "agent_runtime: synthesized snapshot from workflow session=%s source=%s nodes=%s",
            session.id,
            snap.get("source"),
            snap.get("nodeCount"),
        )

    brief["pendingRuntime"] = False
    if isinstance(snap.get("preferredImageModel"), str) and snap["preferredImageModel"].strip():
        brief["preferredImageModel"] = snap["preferredImageModel"].strip()
    if isinstance(snap.get("preferredVideoModel"), str) and snap["preferredVideoModel"].strip():
        brief["preferredVideoModel"] = snap["preferredVideoModel"].strip()
    session.brief_json = brief
    session.status = "active"

    rows = list(
        (
            await db.execute(
                select(AgentSessionMessage)
                .where(AgentSessionMessage.session_id == int(session.id))
                .order_by(AgentSessionMessage.seq.asc())
            )
        ).scalars().all()
    )
    appendix = await _skill_appendix(db, session, message=message)
    ref_ids = [
        str(x).strip()
        for x in (brief.get("referenceAssetIds") or [])
        if str(x).strip()
    ]
    image_urls = await collect_vision_image_urls(
        db,
        session.project_id,
        message_text=message,
        reference_asset_ids=ref_ids,
        canvas_snapshot=snap,
        max_images=6,
    )
    video_urls = await collect_vision_video_urls(
        db,
        session.project_id,
        message_text=message,
        reference_asset_ids=ref_ids,
        canvas_snapshot=snap,
        max_videos=3,
    )
    # 多 worker / 启动 DDL 失败时 L1 可能为空；思考前从 Redis/DB 回填密钥快照
    try:
        from .credential_service import refresh_runtime_if_credential_version_changed

        await refresh_runtime_if_credential_version_changed(db)
    except Exception as cred_exc:  # noqa: BLE001
        logger.warning("agent_runtime: credential refresh skipped: %s", cred_exc)
    has_media = bool(image_urls or video_urls)
    model_id = pick_vision_controller_model(
        get_session_controller_model(session),
        has_images=has_media,
    )
    if has_media:
        logger.info(
            "agent_runtime: vision refs session=%s images=%s videos=%s model=%s",
            session.id,
            len(image_urls),
            len(video_urls),
            model_id,
        )
    if not model_id:
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            content="控制器模型未配置，无法思考。请在后台配置文本模型密钥后再试。",
            agent_role="orchestrator",
            skill_id=session.skill_id,
        )
        session.status = "awaiting_user"
        await db.flush()
        return await agent_sessions.list_session_messages(
            db, user, str(session.id), after_seq=0
        )
    tools = openai_tool_schemas()

    prev_memo = str(brief.get("contextMemo") or "").strip()
    skill_hint = ""
    try:
        bound_for_memo = await agent_sessions._skill_row_for_session(db, session)
        skill_hint = str(
            getattr(bound_for_memo, "name", None)
            or getattr(bound_for_memo, "slug", None)
            or ""
        ).strip()
    except Exception:  # noqa: BLE001
        skill_hint = ""
    hist_msgs, refreshed_memo = _history_messages(
        rows,
        skip_user_content=message if not tool_results else None,
        context_memo=prev_memo,
        skill_hint=skill_hint,
    )
    if refreshed_memo and refreshed_memo != prev_memo:
        brief["contextMemo"] = refreshed_memo
        session.brief_json = brief
        flag_modified(session, "brief_json")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(skill_appendix=appendix, chat_only=chat_only)},
        *hist_msgs,
    ]
    inflight = brief.get("runtimeInflight") if isinstance(brief.get("runtimeInflight"), dict) else None
    resume_pending_ask: dict[str, Any] | None = None
    tool_fail_notes: list[str] = []
    if tool_results and inflight and isinstance(inflight.get("assistant"), dict):
        messages.append(inflight["assistant"])
        pending_ids = [
            str(x).strip()
            for x in (inflight.get("pendingIds") or [])
            if str(x).strip()
        ]
        got: dict[str, str] = {}
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("toolCallId") or item.get("id") or "").strip()
            if not cid:
                continue
            ok = bool(item.get("ok", True))
            result = str(item.get("result") or "").strip() or ("ok" if ok else "failed")
            mapped = item.get("nodeId") or item.get("mappedNodeId")
            if mapped:
                result = f"{result}；落地 nodeId={mapped}"
            got[cid] = result if ok else f"失败：{result}"
        for cid in pending_ids:
            content = got.get(cid, "前端未回传该工具结果，请根据最新快照判断是否已落地。")
            if content.startswith("失败"):
                tool_fail_notes.append(content)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": cid,
                    "content": content,
                }
            )
        messages.append(
            {
                "role": "user",
                "content": _observe_snapshot_blocks(
                    snap,
                    heading="【画布已更新】",
                    tool_primary_models=tool_primary_models,
                ),
            }
        )
        raw_ask = inflight.get("pendingAsk")
        if isinstance(raw_ask, dict) and str(raw_ask.get("prompt") or "").strip():
            resume_pending_ask = raw_ask
        brief.pop("runtimeInflight", None)
        session.brief_json = brief
    else:
        user_text = _turn_user_content(
            snap, message, tool_primary_models=tool_primary_models
        )
        messages.append(
            {
                "role": "user",
                "content": build_controller_user_content(
                    user_text,
                    model_id=model_id,
                    image_urls=image_urls,
                    video_urls=video_urls,
                ),
            }
        )

    # 点名节点 / 本轮是否已提交生成或故事板：续跑时禁止再出片、禁止再铺点
    intent_text = last_user_intent_text(rows, message)
    named_nodes = specified_nodes(intent_text, snap)
    # 续跑检测：即使 brief 已 pop，仍用本轮入参里的 inflight 快照
    stop_continue, done_text = should_stop_after_tool_results(
        user_text=intent_text,
        inflight=inflight if isinstance(inflight, dict) else None,
        tool_results=tool_results,
        snapshot=snap if isinstance(snap, dict) else None,
    )
    # 只要板 + 快照已有板时，即使本轮 tool_results 已空也要收束
    already_generated = bool(stop_continue)

    client_ops: list[dict[str, Any]] = []
    ask_prompt = ""
    ask_options: list[dict[str, str]] = []
    assistant_text = ""
    last_raw_assistant: dict[str, Any] | None = None
    pending_ids: list[str] = []

    # 同轮写画布时暂存的 ask_user：画布落地后再发出，避免问句丢失
    # 已成功提交生成 / 只要板已出板时优先收束，丢掉「再铺镜头」类续跑
    if already_generated:
        assistant_text = done_text or GENERATE_DONE_TEXT
        ask_prompt = ""
        ask_options = []
        resume_pending_ask = None
        logger.info(
            "agent_runtime: skip continue after done session=%s text=%s named=%s",
            session.id,
            (assistant_text or "")[:40],
            [n.get("id") for n in named_nodes],
        )
    elif resume_pending_ask:
        ask_prompt = str(resume_pending_ask.get("prompt") or "").strip()
        raw_opts = resume_pending_ask.get("options")
        ask_options = []
        if isinstance(raw_opts, list):
            for opt in raw_opts:
                if isinstance(opt, dict) and opt.get("label"):
                    ask_options.append(
                        {
                            "id": str(opt.get("id") or opt.get("label")),
                            "label": str(opt.get("label")),
                        }
                    )
    else:
        deep = evolving_use_deep_thinking(
            intent_text,
            chat_only=chat_only,
            has_skill=bool((appendix or "").strip()),
        )
        llm_kwargs: dict[str, Any] = {"timeout_s": 180.0}
        if is_evolving_controller(model_id):
            llm_kwargs = evolving_tools_call_kwargs(deep=deep)
            logger.info(
                "agent_runtime: evolving thinking=%s max_tokens=%s timeout=%s session=%s",
                llm_kwargs.get("thinking_enabled"),
                llm_kwargs.get("max_tokens"),
                llm_kwargs.get("timeout_s"),
                session.id,
            )
        max_rounds = max_server_rounds_for_model(model_id)
        for _round in range(max_rounds):
            try:
                result = await chat_completion_tools(
                    model_id,
                    messages,
                    tools=tools,
                    **llm_kwargs,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent_runtime llm failed session=%s: %s", session.id, exc)
                assistant_text = f"思考失败：{exc}"
                break

            # 用户只看 content；reasoning 不写入会话文案
            assistant_text = result.content
            last_raw_assistant = result.raw_message
            if not result.tool_calls:
                break

            server_jobs: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
            client_calls: list[tuple[str, str, dict[str, Any]]] = []
            ask_this = False
            for tc in result.tool_calls:
                cid, name, args = tool_call_name_args(tc)
                if not cid or not name:
                    continue
                if name in TERMINAL_TOOL_NAMES:
                    # 用户已确认出片：禁止再 ask_user 循环，改由服务端补 generate_node
                    from .agent_generate_intent import allows_video_generate

                    if allows_video_generate(intent_text):
                        continue
                    ask_this = True
                    ask_prompt = str(args.get("prompt") or ask_prompt or assistant_text or "请确认一下。")
                    raw_opts = args.get("options") if isinstance(args.get("options"), list) else []
                    ask_options = []
                    for opt in raw_opts:
                        if isinstance(opt, dict) and opt.get("label"):
                            ask_options.append(
                                {
                                    "id": str(opt.get("id") or opt.get("label")),
                                    "label": str(opt.get("label")),
                                }
                            )
                    continue
                if name in SERVER_TOOL_NAMES:
                    server_jobs.append((tc, name, args))
                    continue
                if name in CLIENT_TOOL_NAMES:
                    client_calls.append((cid, name, args))
                    continue
                server_jobs.append((tc, name, args))

            # 读工具全部执行完再交给模型；同会话 AsyncSession 不并发
            bound_skill = await agent_sessions._skill_row_for_session(db, session)
            bound_slug = str(getattr(bound_skill, "slug", "") or "").strip() or None
            server_pairs: list[tuple[dict[str, Any], str]] = []
            for tc, name, args in server_jobs:
                if name in SERVER_TOOL_NAMES:
                    append_runtime_event(
                        brief,
                        kind="tool_started",
                        message=name,
                        data={"tool": name},
                    )
                    out = await execute_server_tool(
                        name,
                        args,
                        snapshot=snap,
                        project_id=str(session.project_id),
                        db=db,
                        skill_slug=bound_slug,
                        skill_row=bound_skill,
                        session=session,
                    )
                    # update_plan 可能改了 brief；重新合并事件队列
                    if isinstance(session.brief_json, dict):
                        brief = dict(session.brief_json)
                    append_runtime_event(
                        brief,
                        kind="tool_finished",
                        message=name,
                        data={"tool": name},
                    )
                    if name == "update_plan":
                        append_runtime_event(
                            brief,
                            kind="plan_updated",
                            message="计划已更新",
                        )
                    session.brief_json = brief
                    flag_modified(session, "brief_json")
                else:
                    out = json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
                server_pairs.append((tc, out))

            # 用户已确认出片时补 generate_node；再跑意图护栏
            from .agent_generate_intent import inject_confirmed_video_generate_calls

            client_calls = inject_confirmed_video_generate_calls(
                client_calls,
                snapshot=snap,
                user_text=intent_text,
            )
            video_gate_blocks: list[tuple[str, str]] = []
            if client_calls:
                filtered = apply_client_intent_filter(
                    client_calls,
                    user_text=intent_text,
                    snapshot=snap,
                    named=named_nodes,
                    already_generated=already_generated,
                )
                client_calls = filtered.calls
                video_gate_blocks = list(filtered.video_gate_blocks)

            # 硬闸拦掉全部写画布调用：不投影；有读结果先交回再让模型 ask_user
            if video_gate_blocks and not client_calls:
                gate_note = video_gate_blocks[0][1]
                if server_pairs:
                    read_calls = [
                        tc
                        for tc, _ in server_pairs
                        if isinstance(tc, dict) and tc.get("id")
                    ]
                    messages.append(
                        assistant_message_keeping_tool_calls(
                            result.raw_message
                            if isinstance(result.raw_message, dict)
                            else None,
                            read_calls,
                            assistant_text,
                        )
                    )
                    for tc, out in server_pairs:
                        cid = str(tc.get("id") or "")
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": cid,
                                "content": out[:_TOOL_RESULT_CHARS],
                            }
                        )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"{gate_note}\n"
                                "请用 ask_user 请用户确认出片，不要再次调用 "
                                "generate_node / storyboard_batch_videos。"
                            ),
                        }
                    )
                    logger.info(
                        "agent_runtime: video hard-gate after reads session=%s blocks=%s",
                        session.id,
                        len(video_gate_blocks),
                    )
                    continue
                ask_prompt = VIDEO_CONFIRM_ASK_PROMPT
                ask_options = list(VIDEO_CONFIRM_ASK_OPTIONS)
                assistant_text = (
                    f"{gate_note}\n\n{VIDEO_CONFIRM_ASK_PROMPT}"
                ).strip()
                last_raw_assistant = None
                append_runtime_event(
                    brief,
                    kind="hard_gate",
                    message="未确认出片，已拦截视频生成",
                )
                session.brief_json = brief
                flag_modified(session, "brief_json")
                logger.info(
                    "agent_runtime: video generate hard-gate session=%s blocks=%s",
                    session.id,
                    len(video_gate_blocks),
                )
                break

            # 同轮既读又写：先把读结果交回（tool_calls 只保留读），写留到下一轮
            if server_pairs and client_calls:
                read_calls = [tc for tc, _ in server_pairs if isinstance(tc, dict) and tc.get("id")]
                messages.append(
                    assistant_message_keeping_tool_calls(
                        result.raw_message if isinstance(result.raw_message, dict) else None,
                        read_calls,
                        assistant_text,
                    )
                )
                for tc, out in server_pairs:
                    cid = str(tc.get("id") or "")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": cid,
                            "content": out[:_TOOL_RESULT_CHARS],
                        }
                    )
                continue

            # 同轮既有写画布又有 ask_user 时，先落地画布，问句写入 inflight.pendingAsk
            if client_calls:
                kept_calls: list[tuple[str, str, dict[str, Any]]] = []
                for cid, name, args in client_calls:
                    ops = client_tool_to_canvas_ops(
                        cid, name, args, user_text=intent_text
                    )
                    # update_node_params 经 slim 后无变更：不投影、不占 pending
                    if not ops:
                        continue
                    pending_ids.append(cid)
                    client_ops.extend(ops)
                    kept_calls.append((cid, name, args))
                if not kept_calls:
                    if video_gate_blocks and not ask_prompt:
                        ask_prompt = VIDEO_CONFIRM_ASK_PROMPT
                        ask_options = list(VIDEO_CONFIRM_ASK_OPTIONS)
                    if ask_prompt:
                        break
                    if not assistant_text:
                        assistant_text = "参数无需变更，已跳过空更新。"
                    last_raw_assistant = None
                    break
                # 护栏可能丢掉/改写工具，inflight 必须与实际 ops 对齐
                last_raw_assistant = assistant_message_for_client_calls(
                    result.raw_message if isinstance(result.raw_message, dict) else None,
                    kept_calls,
                    assistant_text,
                )
                # 搭板/挪点可投影，但出片被硬闸：落地后追问确认
                if video_gate_blocks and not ask_prompt:
                    ask_prompt = VIDEO_CONFIRM_ASK_PROMPT
                    ask_options = list(VIDEO_CONFIRM_ASK_OPTIONS)
                break

            if ask_this:
                break

            messages.append(result.raw_message)
            for tc, out in server_pairs:
                cid = str(tc.get("id") or "")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": cid,
                        "content": out[:_TOOL_RESULT_CHARS],
                    }
                )
            if not server_pairs:
                break
        else:
            if not assistant_text:
                assistant_text = "本轮步骤较多，已先停在当前画布。请看节点或再发一句继续。"

    artifacts: list[Any] | None = None
    if ask_prompt and not client_ops:
        fail_head = ""
        if resume_pending_ask and tool_fail_notes:
            fail_head = (
                "部分步骤未成功：\n"
                + "\n".join(f"- {x}" for x in tool_fail_notes[:8])
                + "\n\n"
            )
        assistant_text = fail_head + ask_prompt
        artifacts = [
            {
                "kind": "ask_choices",
                "prompt": ask_prompt,
                "options": ask_options,
                "status": "pending",
            }
        ]
        append_runtime_event(
            brief,
            kind="ask_user",
            message=(ask_prompt or "")[:120],
        )
        session.brief_json = brief
        flag_modified(session, "brief_json")

    if client_ops:
        # 思考过程中用户点了停止：丢弃本轮 canvasOps，不要再投影
        if await session_runtime_stop_requested(db, int(session.id)):
            brief.pop("runtimeInflight", None)
            brief["pendingRuntime"] = False
            session.brief_json = brief
            flag_modified(session, "brief_json")
            session.status = "awaiting_user"
            await db.flush()
            return await agent_sessions.list_session_messages(
                db, user, str(session.id), after_seq=0
            )
        inflight_payload: dict[str, Any] = {
            "assistant": last_raw_assistant or {
                "role": "assistant",
                "content": assistant_text,
                "tool_calls": [],
            },
            "pendingIds": pending_ids,
        }
        if ask_prompt:
            inflight_payload["pendingAsk"] = {
                "prompt": ask_prompt,
                "options": ask_options,
            }
        brief["runtimeInflight"] = inflight_payload
        append_runtime_event(
            brief,
            kind="canvas_op_applied",
            message=f"投影 {len(client_ops)} 步",
            data={"opCount": len(client_ops)},
        )
        session.brief_json = brief
        flag_modified(session, "brief_json")
        graph_row = await project_graphs.get_or_create_graph(db, int(session.project_id))
        graph = dict(graph_row.graph_json or {})
        await project_graphs.save_graph(
            db,
            int(session.project_id),
            graph,
            canvas_ops=client_ops,
            bump_revision=True,
        )
        think = assistant_text.strip() or "正在把步骤落到画布…"
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            content=think,
            agent_role="orchestrator",
            skill_id=session.skill_id,
        )
        session.status = "active"
        await db.flush()
        return await agent_sessions.list_session_messages(
            db, user, str(session.id), after_seq=0
        )

    brief.pop("runtimeInflight", None)
    session.brief_json = brief
    final = (assistant_text or "").strip() or "已看过当前画布。你想先改哪一块？"
    await agent_sessions.append_message(
        db,
        session,
        role="assistant",
        content=final,
        agent_role="orchestrator",
        skill_id=session.skill_id,
        artifacts=artifacts,
    )
    session.status = "awaiting_user"
    await db.flush()
    return await agent_sessions.list_session_messages(
        db, user, str(session.id), after_seq=0
    )


async def enqueue_session_runtime_turn(
    db: AsyncSession,
    user: User,
    session: AgentSession,
    *,
    message: str,
    choice_id: str | None = None,
    action: str | None = None,
    canvas_snapshot: dict[str, Any] | None = None,
    allow_workflow_snapshot: bool = False,
) -> dict[str, Any]:
    """写入用户消息（resume 除外），调度后台思考。

    allow_workflow_snapshot：OpenAPI 无前端快照时用最新工作流合成目录并立刻调度。
    站内首页建会话仍等画布页 kick，避免用过期工作流抢跑。
    """
    act = (action or "").strip().lower() or None
    text = (message or "").strip()
    if choice_id:
        text = (text + f"\n（选项 {choice_id}）").strip()
    if act == "ignore":
        # 忽略也要收起询问条，否则前端会一直停在「询问中」
        await _mark_pending_ask_choices(db, int(session.id), status="ignored")
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            content="好的，需要时直接说就行。",
            agent_role="orchestrator",
            skill_id=session.skill_id,
        )
        session.status = "awaiting_user"
        await db.flush()
        result = await agent_sessions.list_session_messages(
            db, user, str(session.id), after_seq=0
        )
        result["_scheduleRuntime"] = False
        return result

    if act != "resume":
        if not text:
            from ..core.error_codes import ErrorCode
            from ..core.errors import fail

            fail(ErrorCode.BAD_REQUEST, message="请输入内容")
        # 用户作答后立刻把旧询问标为已答，避免刷新后询问框还在
        await _mark_pending_ask_choices(db, int(session.id), status="answered")
        await agent_sessions.append_message(
            db,
            session,
            role="user",
            content=text,
            skill_id=session.skill_id,
        )

    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    brief.pop("runtimeStopRequested", None)
    snap = canvas_snapshot if client_snapshot_usable(canvas_snapshot) else None
    if snap is None and allow_workflow_snapshot:
        snap = await snapshot_from_latest_workflow(db, session.project_id)
        if isinstance(canvas_snapshot, dict):
            for key in ("preferredImageModel", "preferredVideoModel"):
                val = str(canvas_snapshot.get(key) or "").strip()
                if val:
                    snap[key] = val
    if snap:
        brief["pendingRuntime"] = False
        pref_img = str(snap.get("preferredImageModel") or "").strip()
        pref_vid = str(snap.get("preferredVideoModel") or "").strip()
        if pref_img:
            brief["preferredImageModel"] = pref_img
        if pref_vid:
            brief["preferredVideoModel"] = pref_vid
    else:
        brief["pendingRuntime"] = True
    session.brief_json = brief
    flag_modified(session, "brief_json")
    session.status = "active"

    from .agent_session_credits import reserve_orchestration_credits

    slug = None
    skill = await agent_sessions._skill_row_for_session(db, session)
    if skill is not None:
        slug = skill.slug
    msgs = list(
        (
            await db.execute(
                select(AgentSessionMessage)
                .where(AgentSessionMessage.session_id == int(session.id))
                .order_by(AgentSessionMessage.seq.asc())
            )
        ).scalars().all()
    )
    user_msgs = [m for m in msgs if getattr(m, "role", None) == "user"]
    turn_seq = int(user_msgs[-1].seq) if user_msgs else 1
    orch = await reserve_orchestration_credits(
        db,
        user=user,
        session=session,
        skill_slug=slug,
        suffix=f"turn-{turn_seq}",
    )
    result = await agent_sessions.session_to_dict(
        db, session, skill_slug=slug, messages=msgs
    )
    result["graph"] = await agent_sessions._graph_payload(db, int(session.project_id))
    result["orchestrationCredit"] = orch
    result["_scheduleRuntime"] = bool(snap)
    result["_followupMessage"] = text
    result["_canvasSnapshot"] = snap
    result["needsCanvasKick"] = snap is None
    return result


async def apply_tool_results(
    db: AsyncSession,
    user: User,
    session: AgentSession,
    *,
    results: list[dict[str, Any]],
    canvas_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """前端投影完成后回传工具结果 + 新快照，继续思考。"""
    if await session_runtime_stop_requested(db, int(session.id)):
        session.status = "awaiting_user"
        await db.flush()
        return await agent_sessions.list_session_messages(
            db, user, str(session.id), after_seq=0
        )
    out = await run_session_runtime(
        db,
        session=session,
        user=user,
        message="（画布工具已执行，请根据最新快照继续）",
        canvas_snapshot=canvas_snapshot,
        tool_results=results,
    )
    return out


async def run_session_runtime_job(
    session_id: int,
    message: str,
    canvas_snapshot: dict[str, Any] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> None:
    """后台任务：独立 DB session。"""
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
            user = (
                await db.execute(
                    select(User).where(User.id == int(session.user_id)).limit(1)
                )
            ).scalar_one_or_none()
            if user is None:
                return
            if await session_runtime_stop_requested(db, int(session_id)):
                return
            await run_session_runtime(
                db,
                session=session,
                user=user,
                message=message,
                canvas_snapshot=canvas_snapshot,
                tool_results=tool_results,
            )
            from .agent_session_credits import commit_orchestration_credits

            await commit_orchestration_credits(db, session)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            logger.exception("agent_runtime session %s failed: %s", session_id, exc)
            async with async_session() as db2:
                try:
                    session2 = (
                        await db2.execute(
                            select(AgentSession)
                            .where(AgentSession.id == int(session_id))
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if session2 is None:
                        return
                    await agent_sessions.append_message(
                        db2,
                        session2,
                        role="assistant",
                        content="思考过程出错，请再发一句。画布内容还在。",
                        agent_role="orchestrator",
                        skill_id=session2.skill_id,
                    )
                    session2.status = "awaiting_user"
                    from .agent_session_credits import release_orchestration_credits

                    await release_orchestration_credits(db2, session2)
                    await db2.commit()
                except Exception:
                    await db2.rollback()
                    logger.exception("agent_runtime recovery failed session=%s", session_id)
