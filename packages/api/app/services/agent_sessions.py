"""Agent Session 服务：建项、发消息、增量查询。"""

from __future__ import annotations

import re
import random
import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.agent_session import AgentSession, AgentSessionMessage
from ..models.project import Project
from ..models.skill import Skill
from ..models.user import User
from ..services.project_access import require_project_access
from ..services.project_create_limits import assert_project_create_allowed
from ..services.project_scope import new_storage_folder
from ..services.skills_catalog import require_skill_by_slug

# 画布向导类平台 Skill（爆款复刻 / 一键出海）：对话驱动，不跑自由创作 Team
_WIZARD_ENTRY_KINDS = frozenset({"viral_remake", "overseas_localize"})
# 故事板→确认→成片类平台配方（电影级宣传片 / 第一视角催泪短片等）：走画布操控
_CANVAS_RECIPE_PLATFORM_SLUGS = frozenset(
    {
        "product_cinematic_commercial",
        "pov_tearjerker_short",
    }
)
# 消息附件行：`[附件:name|assetId=xxx]`（MySQL 自增 id 可能仅 1～5 位，勿要求 ≥6）
_ASSET_ID_RE = re.compile(r"assetId=([0-9a-zA-Z_-]{1,128})", re.IGNORECASE)
# 对话确认成片（与 agent_generate_intent._CONFIRM_VIDEO_GENERATE_RE 对齐；短出片「生成视频」走 is_short_video_shot）
_VIRAL_CONFIRM_RE = re.compile(
    r"确认生成|生成同款|确认并生成|开始成片|批量成片|"
    r"开始吧|开始生成|需要生成|生成吧|可以生成|现在开始|开始出片|"
    r"同意生成|确认出片|按方案生成|就这样生成|出片吧|"
    r"好的[，,]?开始|可以[，,]?开始|那就生成|就生成吧",
    re.IGNORECASE,
)
# 对话生成主体图（勿与再次拉片混淆）
_VIRAL_SUBJECTS_RE = re.compile(
    r"一键生成主体图|生成主体图|全部主体图|主体生图|出主体图",
    re.IGNORECASE,
)

# 爆款对话默认画幅/清晰度（对齐 ViralRemakeDialog / LibTV）
_DEFAULT_ASPECT_RATIO = "9:16"
_DEFAULT_CLARITY = "1080p"
_VALID_ASPECT_RATIOS = frozenset({"9:16", "16:9", "1:1"})
_VALID_CLARITIES = frozenset({"1080p", "720p"})
# 一键出海目标市场（与前端 overseasMarkets 对齐）
_DEFAULT_TARGET_MARKET = "US"
_VALID_TARGET_MARKETS = frozenset({"US", "JP", "KR", "SEA", "EU", "MENA", "BR", "IN"})
_TARGET_MARKET_LABELS: dict[str, str] = {
    "US": "美国",
    "JP": "日本",
    "KR": "韩国",
    "SEA": "东南亚",
    "EU": "欧洲",
    "MENA": "中东",
    "BR": "巴西",
    "IN": "印度",
}


def _normalize_aspect_ratio(raw: Any) -> str:
    v = str(raw or "").strip()
    return v if v in _VALID_ASPECT_RATIOS else _DEFAULT_ASPECT_RATIO


def _normalize_clarity(raw: Any) -> str:
    v = str(raw or "").strip().lower()
    return v if v in _VALID_CLARITIES else _DEFAULT_CLARITY


def _normalize_target_market(raw: Any) -> str:
    v = str(raw or "").strip().upper()
    return v if v in _VALID_TARGET_MARKETS else _DEFAULT_TARGET_MARKET


def _viral_asked_summary_items(
    *,
    aspect_ratio: str | None = None,
    clarity: str | None = None,
    replace_notes: str | None = None,
    target_market_id: str | None = None,
    entry_kind: str | None = None,
) -> list[dict[str, str]]:
    """LibTV 式「已询问」摘要条目。"""
    notes = (replace_notes or "").strip()
    kind = str(entry_kind or "").strip()
    items: list[dict[str, str]] = []
    if kind == "overseas_localize":
        mid = _normalize_target_market(target_market_id)
        items.append(
            {
                "q": "目标市场",
                "a": f"{_TARGET_MARKET_LABELS.get(mid, mid)}（{mid}）",
            }
        )
        items.append(
            {
                "q": "本地化说明",
                "a": notes or "待填写（可选：补充服饰/场景偏好）",
            }
        )
    else:
        items.append(
            {
                "q": "替换说明",
                "a": notes
                or "待填写（上传时说明想把什么换成什么；台词旁白不变则保留）",
            }
        )
    items.append({"q": "视频比例", "a": _normalize_aspect_ratio(aspect_ratio)})
    items.append({"q": "清晰度", "a": _normalize_clarity(clarity)})
    return items


def _merge_viral_remake_brief(
    existing: Any,
    *,
    entry_kind: str = "viral_remake",
    aspect_ratio: str | None = None,
    clarity: str | None = None,
    replace_notes: str | None = None,
    target_market_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """合并 brief.viralRemake；画幅/清晰度缺省 9:16 / 1080p；出海含目标市场。"""
    prev = dict(existing) if isinstance(existing, dict) else {}
    out: dict[str, Any] = {**prev}
    if extra:
        out.update(extra)
    # 显式传入优先，否则沿用 brief，再兜底默认
    ar = aspect_ratio if aspect_ratio is not None else out.get("aspectRatio")
    cl = clarity if clarity is not None else out.get("clarity")
    out["aspectRatio"] = _normalize_aspect_ratio(ar)
    out["clarity"] = _normalize_clarity(cl)
    if replace_notes is not None:
        out["replaceNotes"] = str(replace_notes).strip()
    elif "replaceNotes" not in out:
        out["replaceNotes"] = ""
    out["entryKind"] = (
        str(out.get("entryKind") or entry_kind or "viral_remake").strip() or "viral_remake"
    )
    # 一键出海：目标市场写入 brief，供前端拉片桥使用
    if out["entryKind"] == "overseas_localize" or target_market_id is not None:
        tm = (
            target_market_id
            if target_market_id is not None
            else out.get("targetMarketId")
        )
        mid = _normalize_target_market(tm)
        out["targetMarketId"] = mid
        out["targetMarketLabel"] = _TARGET_MARKET_LABELS.get(mid, mid)
    return out


def _is_wizard_skill(skill: Skill | None) -> bool:
    return bool(skill and (skill.entry_kind or "") in _WIZARD_ENTRY_KINDS)


def _is_product_cinematic_skill(skill: Skill | None) -> bool:
    """平台「故事板→确认→成片」类配方（含电影级宣传片、第一视角催泪短片）。

    整包技能 + 画布操控，禁止固定 Team 七步；读板建镜后进入 awaiting_generate_confirm。
    """
    if skill is None:
        return False
    slug = (skill.slug or "").strip()
    kind = (skill.entry_kind or "").strip()
    return slug in _CANVAS_RECIPE_PLATFORM_SLUGS or kind in _CANVAS_RECIPE_PLATFORM_SLUGS


def _prefers_canvas_manual(skill: Skill | None) -> bool:
    """是否走精细画布操控（agent_followup 一次性 JSON plan）而非 Team 模板投影。

    平台故事板成片类配方硬编码走此路径；「我的 Skill」可通过 execution_mode=
    canvas_manual 显式声明，追平同等能力（技能包能力对齐，见技能包升级方案）。
    """
    if _is_product_cinematic_skill(skill):
        return True
    return bool(
        skill is not None
        and str(getattr(skill, "execution_mode", None) or "") == "canvas_manual"
    )


def _product_cinematic_load_message(skill: Skill) -> str:
    """LibTV 式黑箱引导：按技能类型提示上传/说明 → 故事板 →「确认生成」出视频。"""
    slug = (skill.slug or skill.entry_kind or "").strip()
    if slug == "pov_tearjerker_short":
        return (
            f"已加载「{skill.title}」。"
            "请告诉我**人物关系**与**情感主题**（可选上传角色/场景参考图；"
            "画幅/时长可省略，默认 15s 竖版）。"
            "例如：父亲从小送我上学到送我远行，主题是父爱与传承。"
            "我会按 POV 情绪工程设计故事板并分配视频镜头；"
            "完成后你只需回复「**确认生成**」开始出视频（出视频前不扣视频算力）。"
        )
    return (
        f"已加载「{skill.title}」。"
        "请上传**产品参考图**，并说明产品名、核心卖点与画幅/时长（可省略，默认 15s 竖版）。"
        "我会自动写镜头提示词、生成故事板并按格分配视频镜头；"
        "完成后你只需回复「**确认生成**」开始出视频（出视频前不扣视频算力）。"
    )


def _wizard_skill_load_message(skill: Skill, doc: dict[str, Any] | None = None) -> str:
    """对齐 LibTV：加载 Skill 后引导上传 + 替换说明；附带 SKILL.md 摘要。"""
    kind = (skill.entry_kind or "").strip()
    if kind == "viral_remake":
        base = (
            "我已经加载了「爆款视频复刻」Skill。"
            "接下来请上传你想复刻的视频文件，我来帮你分析！"
            "\n\n"
            "同时为了不用来回打断，也可以顺便一起告诉我："
            "你想把视频里的什么替换成什么？"
            "（例如把某个道具、人物、Logo 换成你的产品/角色/品牌；台词旁白不变则保留）"
            "\n\n"
            "下方可选视频比例与清晰度（默认 9:16 / 1080p）。"
            "拉片完成后回复「确认生成」，成片会写入独立「成片表」节点。"
        )
    elif kind == "overseas_localize":
        base = (
            f"已加载「{skill.title}」Skill。"
            "请在输入框旁选择「出海国家版本」，上传参考视频后发送，"
            "我将按该市场本地化主体/场景/对白并拉片。"
            "\n\n"
            "拉片完成后可回复「一键生成主体图」或「确认生成」批量出海成片。"
        )
    else:
        base = f"已加载「{skill.title}」Skill。请按提示补充素材后继续。"
    summary = (doc or {}).get("summary") or ""
    # 去掉历史文案中的外部产品对齐说明，避免对用户展示
    summary = re.sub(
        r"（对齐\s*\[[^\]]+\]\([^)]+\)\s*Skill\s*卡）",
        "",
        summary,
    )
    summary = re.sub(r"对齐\s*\[[^\]]+\]\([^)]+\)\s*Skill\s*卡", "", summary)
    if summary and len(summary) < 220:
        return f"{base}\n\n【Skill 说明】\n{summary}"
    return base


async def _resolve_skill_doc_for_session(db: AsyncSession, skill: Skill | None) -> dict[str, Any] | None:
    """会话路径读 Skill MD（含库覆盖）。"""
    if skill is None:
        return None
    from .skill_docs import load_skill_doc_resolved

    return await load_skill_doc_resolved(db, skill.slug)


def _extract_asset_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for m in _ASSET_ID_RE.finditer(text or ""):
        aid = (m.group(1) or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        ids.append(aid)
    return ids


async def _resolve_wizard_media(
    db: AsyncSession,
    project_id: int | str,
    asset_ids: list[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """从附件 id 中拆出首个视频与图片（替换参考）。"""
    from .asset_store import get_project_assets_by_ids

    if not asset_ids:
        return None, []
    rows = await get_project_assets_by_ids(db, str(project_id), asset_ids)
    by_id = {str(r.get("id") or ""): r for r in rows if r.get("id") is not None}
    # legacyId 也可能出现在消息里
    for r in rows:
        leg = str(r.get("legacyId") or "").strip()
        if leg:
            by_id[leg] = r

    def _looks_like_video(row: dict[str, Any]) -> bool:
        """兼容：前端误标 category=image、或仅靠扩展名/MIME 识别视频。"""
        cat = str(row.get("category") or "").lower()
        if cat == "video":
            return True
        ft = str(row.get("fileType") or row.get("file_type") or "").lower()
        if ft.startswith("video/"):
            return True
        name = str(row.get("title") or row.get("fileUrl") or row.get("file_url") or "").lower()
        return bool(re.search(r"\.(mp4|webm|mov|mkv|avi|m4v)(?:\?|$)", name))

    video: dict[str, Any] | None = None
    images: list[dict[str, Any]] = []
    for aid in asset_ids:
        row = by_id.get(str(aid))
        if not row:
            continue
        cat = str(row.get("category") or "").lower()
        if _looks_like_video(row) and video is None:
            video = row
        elif cat == "image" and not _looks_like_video(row) and len(images) < 4:
            images.append(row)
    return video, images


async def _skill_row_for_session(db: AsyncSession, session: AgentSession) -> Skill | None:
    if not session.skill_id:
        return None
    return (
        await db.execute(select(Skill).where(Skill.id == int(session.skill_id)).limit(1))
    ).scalar_one_or_none()


def _gen_project_no() -> str:
    ts = int(time.time() * 1000) % 100000000
    suffix = random.randint(0, 99)
    return f"PRJ-{ts:08d}{suffix:02d}"


def _project_url(project_id: int | str, session_id: int | str, *, skill_slug: str | None = None) -> str:
    """画布链接：有 CANVAS_PUBLIC_ORIGIN 时返回绝对 URL，否则相对路径。"""
    from ..core.config import get_settings

    qs = f"session={session_id}"
    if skill_slug:
        qs += f"&skill={skill_slug}"
    path = f"/{project_id}?{qs}"
    settings = get_settings()
    origin = (settings.canvas_public_origin or "").rstrip("/")
    # 允许 CANVAS_WEB_BASE_PATH=""（c 域无 /canvas）；勿用 `or "/canvas"` 把空串盖掉
    base = str(settings.canvas_web_base_path or "").rstrip("/")
    if origin:
        return f"{origin}{base}{path}"
    # 前端站内路由仍用相对路径（不含 basePath，由 Next 处理）
    return path


def message_to_dict(msg: AgentSessionMessage) -> dict[str, Any]:
    return {
        "id": str(msg.id),
        "seq": int(msg.seq),
        "role": msg.role,
        "agentRole": msg.agent_role,
        "content": msg.content or "",
        "skillId": str(msg.skill_id) if msg.skill_id else None,
        "graphPatch": msg.graph_patch,
        "jobIds": msg.job_ids or [],
        "artifacts": msg.artifacts or [],
        "createdAt": to_cst_iso(msg.created_at) if msg.created_at else None,
    }


async def session_to_dict(
    db: AsyncSession,
    session: AgentSession,
    *,
    skill_slug: str | None = None,
    messages: list[AgentSessionMessage] | None = None,
) -> dict[str, Any]:
    from .agent_controller import (
        controller_models_public,
        get_session_controller_model,
        list_controller_model_ids,
    )
    from .agent_skill_pricing import get_agent_skill_pricing

    pricing = await get_agent_skill_pricing(db)
    allow = pricing.get("controllerModels") or []
    allowlist = allow if allow else None
    ids = list_controller_model_ids(allowlist)
    ctrl = get_session_controller_model(session) or (ids[0] if ids else None)
    brief = session.brief_json if isinstance(session.brief_json, dict) else {}
    # 创作框/对话附件：供前端 Composer 回显芯片
    ref_ids = [
        str(x).strip()
        for x in (brief.get("referenceAssetIds") or [])
        if str(x).strip()
    ][:20]
    my_defaults = (
        brief.get("mySkillDefaults")
        if isinstance(brief.get("mySkillDefaults"), dict)
        else None
    )
    return {
        "sessionId": str(session.id),
        "projectId": str(session.project_id),
        "skillId": str(session.skill_id) if session.skill_id else None,
        "skillSlug": skill_slug,
        "status": session.status,
        "title": session.title,
        "controllerModel": ctrl,
        "controllerModels": controller_models_public(allowlist=allowlist),
        "projectUrl": _project_url(session.project_id, session.id, skill_slug=skill_slug),
        "createdAt": to_cst_iso(session.created_at) if session.created_at else None,
        "updatedAt": to_cst_iso(session.updated_at) if session.updated_at else None,
        "messages": [message_to_dict(m) for m in (messages or [])],
        "conversationTurnCredit": int(pricing.get("conversationTurn") or 0),
        "agentPricingVersion": int(pricing.get("version") or 1),
        "referenceAssetIds": ref_ids,
        # 我的 Skill 配方确认条
        "mySkillDefaults": my_defaults,
        "recipePending": bool(brief.get("recipePending"))
        and not bool(brief.get("recipeApplied")),
        "recipeApplied": bool(brief.get("recipeApplied")),
        "needsCanvasKick": bool(brief.get("pendingRuntime")),
        # 可见计划（update_plan）；仅进度展示
        "runtimePlan": brief.get("runtimePlan")
        if isinstance(brief.get("runtimePlan"), dict)
        else None,
        "runtimeEvents": [
            e
            for e in (brief.get("runtimeEvents") or [])
            if isinstance(e, dict)
        ][-20:]
        if isinstance(brief.get("runtimeEvents"), list)
        else [],
        # 含 canvasOps，供站内投影与 OpenAPI 轮询
        "graph": await _graph_payload(db, int(session.project_id)),
    }


async def _next_seq(db: AsyncSession, session_id: int) -> int:
    result = await db.execute(
        select(func.coalesce(func.max(AgentSessionMessage.seq), 0)).where(
            AgentSessionMessage.session_id == session_id
        )
    )
    return int(result.scalar_one() or 0) + 1


async def append_message(
    db: AsyncSession,
    session: AgentSession,
    *,
    role: str,
    content: str,
    agent_role: str | None = None,
    skill_id: int | None = None,
    graph_patch: dict[str, Any] | None = None,
    job_ids: list[Any] | None = None,
    artifacts: list[Any] | None = None,
) -> AgentSessionMessage:
    seq = await _next_seq(db, int(session.id))
    msg = AgentSessionMessage(
        session_id=int(session.id),
        seq=seq,
        role=role,
        agent_role=agent_role,
        content=content or "",
        skill_id=skill_id,
        graph_patch=graph_patch,
        job_ids=job_ids,
        artifacts=artifacts,
    )
    db.add(msg)
    session.updated_at = now_cst_naive()
    await db.flush()
    return msg


async def get_session_for_user(
    db: AsyncSession, user: User, session_id: str
) -> AgentSession:
    try:
        sid = int(str(session_id).strip())
    except (TypeError, ValueError):
        fail(ErrorCode.AGENT_SESSION_NOT_FOUND, message="会话不存在")
    session = (
        await db.execute(select(AgentSession).where(AgentSession.id == sid).limit(1))
    ).scalar_one_or_none()
    if session is None:
        fail(ErrorCode.AGENT_SESSION_NOT_FOUND, message="会话不存在")
    if int(session.user_id) != int(user.id):
        fail(ErrorCode.FORBIDDEN, message="无权访问该会话")
    await require_project_access(db, user, str(session.project_id))
    return session


async def create_agent_session(
    db: AsyncSession,
    user: User,
    *,
    message: str,
    skill_slug: str | None = None,
    project_id: str | None = None,
    project_title: str | None = None,
    style_id: str | None = None,
    controller_model: str | None = None,
    gen_mode: str | None = None,
    aspect_ratio: str | None = None,
    clarity: str | None = None,
    target_market_id: str | None = None,
    canvas_snapshot: dict | None = None,
    allow_workflow_snapshot: bool = False,
) -> dict[str, Any]:
    """
    创建 Agent 会话：
    - 无 projectId → 新建项目
    - 有 skillSlug → 绑定 Skill，并写入引导消息（具体流水线由前端 Wizard / 后续 Runner 执行）
    - 无 Skill → 自由创作占位消息（Agent Team 全流程 Phase 2）
    - styleId → 写入 session，编排时落到 Project Graph.style
    - controllerModel → 编排/续聊用的控制器 LLM
    - genMode → smart 澄清编排 / canvas 直接画布操控 / chat 仅对话
    - aspectRatio / clarity → 爆款对话选项（默认 9:16 / 1080p）
    - targetMarketId → 一键出海目标市场版本
    """
    from .agent_controller import set_session_controller_model

    text = (message or "").strip()
    mode = (gen_mode or "").strip().lower() or "smart"
    if mode not in ("smart", "canvas", "chat"):
        mode = "smart"
    skill: Skill | None = None
    if skill_slug and skill_slug.strip():
        skill = await require_skill_by_slug(db, skill_slug.strip(), user=user)

    # 我的 Skill：另存时的 defaults（想法/风格/模型/画幅）作建会话兜底
    from .skills_catalog import get_skill_defaults

    skill_defaults = get_skill_defaults(skill) if skill is not None else {}
    is_my_skill = bool(skill is not None and skill.owner_user_id is not None)

    style = (style_id or "").strip() or None
    if style in ("none", ""):
        style = None
    if style is None and skill_defaults.get("styleId"):
        style = str(skill_defaults.get("styleId") or "").strip() or None
    # Skill 默认风格可作兜底
    if style is None and skill is not None and skill.default_style_id:
        style = str(skill.default_style_id).strip() or None
        if style in ("none", ""):
            style = None

    if not (controller_model or "").strip() and skill_defaults.get("controllerModel"):
        controller_model = str(skill_defaults.get("controllerModel") or "").strip() or None
    if not (aspect_ratio or "").strip() and skill_defaults.get("aspectRatio"):
        aspect_ratio = str(skill_defaults.get("aspectRatio") or "").strip() or None
    if not (clarity or "").strip() and skill_defaults.get("clarity"):
        clarity = str(skill_defaults.get("clarity") or "").strip() or None
    if mode == "smart" and skill_defaults.get("genMode"):
        dm = str(skill_defaults.get("genMode") or "").strip().lower()
        if dm in ("smart", "canvas", "chat"):
            mode = dm

    # 我的 Skill：空消息或「启动 Skill：」占位 → 用另存想法自动开跑
    if is_my_skill and not _is_wizard_skill(skill):
        seed_idea = str(skill_defaults.get("idea") or "").strip()
        if not text or text.startswith("启动 Skill：") or text.startswith("启动 Skill:"):
            text = seed_idea or (skill.title if skill else text)

    project: Project | None = None
    if project_id and str(project_id).strip():
        await require_project_access(db, user, str(project_id).strip())
        try:
            pid = int(str(project_id).strip())
        except (TypeError, ValueError):
            fail(ErrorCode.INVALID_PROJECT_ID)
        project = (
            await db.execute(select(Project).where(Project.id == pid).limit(1))
        ).scalar_one_or_none()
        if project is None:
            fail(ErrorCode.PROJECT_NOT_FOUND)

    if project is None:
        await assert_project_create_allowed(db, int(user.id))
        title = (project_title or "").strip()
        if not title:
            if skill is not None:
                title = skill.title
            elif text:
                title = text[:40] + ("…" if len(text) > 40 else "")
            else:
                title = "未命名项目"
        project = Project(
            owner_id=user.id,
            project_no=_gen_project_no(),
            title=title,
            storage_folder=new_storage_folder(),
        )
        db.add(project)
        await db.flush()

    # 爆款/出海：建会话即 seed brief.viralRemake（默认 9:16 / 1080p）
    initial_brief: dict[str, Any] = {"genMode": mode}
    # 我的 Skill：把生图/生视频模型与画幅时长写入 brief，供 Team / 续聊读取
    if is_my_skill and skill_defaults:
        media_kind = str(skill_defaults.get("mediaKind") or "").strip().lower()
        if media_kind not in ("image", "video", "text", "audio"):
            media_kind = ""
        if media_kind != "video":
            # 非短视频：去掉时长，避免按成片排版
            skill_defaults = {
                k: v for k, v in skill_defaults.items() if k != "durationSec"
            }
        if skill_defaults.get("imageModel"):
            initial_brief["preferredImageModel"] = str(skill_defaults["imageModel"])
        if media_kind == "video" and skill_defaults.get("videoModel"):
            initial_brief["preferredVideoModel"] = str(skill_defaults["videoModel"])
        if skill_defaults.get("aspectRatio"):
            initial_brief["aspectRatio"] = str(skill_defaults["aspectRatio"])
        if media_kind in ("image", "video") and skill_defaults.get("clarity"):
            initial_brief["clarity"] = str(skill_defaults["clarity"])
        if media_kind == "video" and skill_defaults.get("durationSec"):
            initial_brief["durationSec"] = str(skill_defaults["durationSec"])
        if media_kind:
            skill_defaults = {**skill_defaults, "mediaKind": media_kind}
        # 保证带上节点配方，供前端确认条与一键铺节点
        from .skill_node_recipe import normalize_node_recipe

        skill_defaults["nodeRecipe"] = normalize_node_recipe(
            skill_defaults.get("nodeRecipe"),
            media_kind=media_kind or "video",
        )
        initial_brief["mySkillDefaults"] = {
            k: skill_defaults[k]
            for k in (
                "idea",
                "mediaKind",
                "imageModel",
                "videoModel",
                "aspectRatio",
                "clarity",
                "durationSec",
                "nodeRecipe",
            )
            if skill_defaults.get(k)
        }
        # 新流程走澄清→Team→确认生成，不再用「按配方铺节点」确认条
        initial_brief["recipePending"] = False
        initial_brief["mySkillPhase"] = "awaiting_plan_confirm"
        # 技能包能力对齐：execution_mode=canvas_manual 时提前标记，方案确认后
        # 走 agent_followup 精细画布操控而非 Team 模板投影（两段人工确认流程不变）
        if _prefers_canvas_manual(skill):
            initial_brief["preferCanvasManual"] = True
            initial_brief["genMode"] = "canvas"
    if _is_wizard_skill(skill):
        initial_brief["viralRemake"] = _merge_viral_remake_brief(
            None,
            entry_kind=str(skill.entry_kind or skill.slug or "viral_remake"),
            aspect_ratio=aspect_ratio,
            clarity=clarity,
            target_market_id=target_market_id,
            replace_notes="",
        )
    # 故事板→确认→成片类平台配方：软默认 + 强制画布操控标记
    if _is_product_cinematic_skill(skill):
        if (aspect_ratio or "").strip():
            initial_brief.setdefault("aspectRatio", aspect_ratio.strip())
        initial_brief["mediaKind"] = "video"
        initial_brief["preferCanvasManual"] = True
        initial_brief["genMode"] = "canvas"

    session = AgentSession(
        user_id=int(user.id),
        project_id=int(project.id),
        skill_id=int(skill.id) if skill else None,
        style_id=style,
        status="active",
        title=project.title,
        brief_json=initial_brief,
    )
    db.add(session)
    await db.flush()
    set_session_controller_model(session, controller_model)

    user_content = text or (f"启动 Skill：{skill.title}" if skill else "开始创作")
    await append_message(
        db,
        session,
        role="user",
        content=user_content,
        skill_id=int(skill.id) if skill else None,
    )

    # 新助手：创建后统一走 tool-call runtime；站内无快照则等画布页 kick
    from .agent_tools import client_snapshot_usable, snapshot_from_latest_workflow

    snap = canvas_snapshot if client_snapshot_usable(canvas_snapshot) else None
    if snap is None and allow_workflow_snapshot:
        snap = await snapshot_from_latest_workflow(db, project.id)
        if isinstance(canvas_snapshot, dict):
            for key in ("preferredImageModel", "preferredVideoModel"):
                val = str(canvas_snapshot.get(key) or "").strip()
                if val:
                    snap[key] = val
    brief_now = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    brief_now["genMode"] = mode
    brief_now["pendingRuntime"] = snap is None
    brief_now.pop("cinematicPhase", None)
    brief_now.pop("mySkillPhase", None)
    session.brief_json = brief_now
    session.status = "active"

    slug = skill.slug if skill else None
    msgs = (
        await db.execute(
            select(AgentSessionMessage)
            .where(AgentSessionMessage.session_id == int(session.id))
            .order_by(AgentSessionMessage.seq.asc())
        )
    ).scalars().all()
    result = await session_to_dict(db, session, skill_slug=slug, messages=list(msgs))
    result["styleId"] = style
    result["_scheduleTeam"] = False
    result["_scheduleClarify"] = False
    result["_scheduleChatOnly"] = False
    result["_scheduleFollowup"] = False
    result["_scheduleRuntime"] = bool(snap)
    result["_followupMessage"] = user_content
    result["_canvasSnapshot"] = snap
    result["needsCanvasKick"] = snap is None
    if snap:
        from .agent_session_credits import reserve_orchestration_credits

        user_msgs = [m for m in msgs if getattr(m, "role", None) == "user"]
        turn_seq = int(user_msgs[-1].seq) if user_msgs else 1
        orch = await reserve_orchestration_credits(
            db,
            user=user,
            session=session,
            skill_slug=slug,
            suffix=f"turn-{turn_seq}",
        )
        result["orchestrationCredit"] = orch
    return result


async def bind_session_reference_assets(
    db: AsyncSession,
    user: User,
    session_id: str,
    *,
    asset_ids: list[str],
) -> dict[str, Any]:
    """把参考图 assetId 写入 session.brief 与 Graph identityLock，并投影到画布参考图节点。"""
    from ..core.datetime_util import now_cst_naive
    from . import project_graphs

    session = await get_session_for_user(db, user, session_id)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in asset_ids or []:
        aid = str(raw or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        cleaned.append(aid)
        if len(cleaned) >= 20:
            break

    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    prev = [
        str(x).strip()
        for x in (brief.get("referenceAssetIds") or [])
        if str(x).strip()
    ]
    merged: list[str] = []
    seen2: set[str] = set()
    for aid in [*prev, *cleaned]:
        if aid in seen2:
            continue
        seen2.add(aid)
        merged.append(aid)
        if len(merged) >= 20:
            break
    brief["referenceAssetIds"] = merged
    # 参考图已写入，清除「等待 bind」标记
    brief.pop("awaitingReferenceBind", None)
    brief.pop("cinematicPhase", None)
    session.brief_json = brief

    # Graph 已有角色时立刻写入 identityLock；否则 Team/续聊从 brief 读取
    row = await project_graphs.get_or_create_graph(db, int(session.project_id))
    graph = dict(row.graph_json or {})
    graph["referenceAssetIds"] = merged
    chars = list(graph.get("characters") or []) if isinstance(graph.get("characters"), list) else []
    if chars:
        for i, c in enumerate(chars):
            if not isinstance(c, dict):
                continue
            lock = c.get("identityLock") if isinstance(c.get("identityLock"), dict) else {}
            refs = [str(x).strip() for x in (lock.get("refAssetIds") or []) if str(x).strip()]
            # 首个角色吃全部参考图；其余仅保留已有
            if i == 0:
                for aid in merged:
                    if aid not in refs:
                        refs.append(aid)
                lock["refAssetIds"] = refs[:12]
                if "sheetAssetIds" not in lock:
                    lock["sheetAssetIds"] = []
                c["identityLock"] = lock
        graph["characters"] = chars
    row.graph_json = graph

    # 新绑定的参考素材 → 追加 canvasOps（图→image，视频→video），不覆盖已有待投影 ops
    new_only = [a for a in cleaned if a not in prev]
    ref_ops: list[dict[str, Any]] = []
    cat_by_id: dict[str, str] = {}
    if new_only:
        try:
            from .asset_store import get_project_assets_by_ids

            rows = await get_project_assets_by_ids(
                db, str(session.project_id), new_only[:8]
            )
            for r in rows or []:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("id") or "").strip()
                if rid:
                    cat_by_id[rid] = str(r.get("category") or "").lower()
        except Exception:  # noqa: BLE001
            cat_by_id = {}
    for i, aid in enumerate(new_only[:8]):
        cat = cat_by_id.get(aid) or "image"
        if cat == "video":
            ref_ops.append(
                {
                    "op": "add_video_node",
                    "tempId": f"vid_ref_{aid[:8]}_{i}",
                    "label": f"参考视频{i + 1}" if i else "参考视频",
                    "prompt": "",
                    "x": 80.0 + float(i) * 320.0,
                    "y": 40.0,
                    "params": {"assetId": aid},
                }
            )
        elif cat == "audio":
            ref_ops.append(
                {
                    "op": "add_audio_node",
                    "tempId": f"aud_ref_{aid[:8]}_{i}",
                    "label": f"参考音频{i + 1}" if i else "参考音频",
                    "prompt": "",
                    "x": 80.0 + float(i) * 280.0,
                    "y": 40.0,
                    "params": {"assetId": aid},
                }
            )
        else:
            ref_ops.append(
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
    if ref_ops:
        pending = list(row.canvas_ops or []) if isinstance(row.canvas_ops, list) else []
        row.canvas_ops = [*pending, *ref_ops]
        next_rev = int(row.revision or 1) + 1
        row.revision = next_rev
        graph["revision"] = next_rev
        graph["projectId"] = str(session.project_id)
        row.graph_json = graph

    row.updated_at = now_cst_naive()
    await db.flush()

    return {
        "sessionId": str(session.id),
        "projectId": str(session.project_id),
        "referenceAssetIds": merged,
        "canvasOpsAdded": len(ref_ops),
    }


async def list_project_sessions(
    db: AsyncSession,
    user: User,
    project_id: str,
    *,
    limit: int = 30,
) -> dict[str, Any]:
    """当前项目下的 Agent 会话列表（历史对话）。"""
    await require_project_access(db, user, project_id)
    try:
        pid = int(str(project_id).strip())
    except (TypeError, ValueError):
        fail(ErrorCode.INVALID_PROJECT_ID)

    lim = max(1, min(int(limit or 30), 50))
    rows = list(
        (
            await db.execute(
                select(AgentSession)
                .where(
                    AgentSession.project_id == pid,
                    AgentSession.user_id == int(user.id),
                )
                .order_by(AgentSession.updated_at.desc())
                .limit(lim)
            )
        ).scalars().all()
    )

    # 批量取 skill slug
    skill_ids = {int(r.skill_id) for r in rows if r.skill_id}
    skill_map: dict[int, str] = {}
    if skill_ids:
        skills = list(
            (await db.execute(select(Skill).where(Skill.id.in_(list(skill_ids))))).scalars().all()
        )
        skill_map = {int(s.id): str(s.slug) for s in skills}

    items = []
    for s in rows:
        slug = skill_map.get(int(s.skill_id)) if s.skill_id else None
        items.append(
            {
                "sessionId": str(s.id),
                "projectId": str(s.project_id),
                "title": s.title or "未命名对话",
                "status": s.status,
                "skillSlug": slug,
                "updatedAt": to_cst_iso(s.updated_at) if s.updated_at else None,
                "createdAt": to_cst_iso(s.created_at) if s.created_at else None,
            }
        )
    return {"projectId": str(pid), "items": items}


async def list_session_messages(
    db: AsyncSession,
    user: User,
    session_id: str,
    *,
    after_seq: int = 0,
) -> dict[str, Any]:
    session = await get_session_for_user(db, user, session_id)
    q = (
        select(AgentSessionMessage)
        .where(AgentSessionMessage.session_id == int(session.id))
        .order_by(AgentSessionMessage.seq.asc())
    )
    if after_seq > 0:
        q = q.where(AgentSessionMessage.seq > after_seq)
    msgs = list((await db.execute(q)).scalars().all())

    skill_slug = None
    if session.skill_id:
        skill = (
            await db.execute(select(Skill).where(Skill.id == int(session.skill_id)).limit(1))
        ).scalar_one_or_none()
        skill_slug = skill.slug if skill else None

    from .agent_controller import (
        controller_models_public,
        get_session_controller_model,
        list_controller_model_ids,
    )
    from .agent_skill_pricing import get_agent_skill_pricing

    pricing = await get_agent_skill_pricing(db)
    allow = pricing.get("controllerModels") or []
    allowlist = allow if allow else None
    ids = list_controller_model_ids(allowlist)
    ctrl = get_session_controller_model(session) or (ids[0] if ids else None)
    brief = session.brief_json if isinstance(session.brief_json, dict) else {}
    ref_ids = [
        str(x).strip()
        for x in (brief.get("referenceAssetIds") or [])
        if str(x).strip()
    ][:20]
    return {
        "sessionId": str(session.id),
        "projectId": str(session.project_id),
        "skillSlug": skill_slug,
        "status": session.status,
        "title": session.title,
        "controllerModel": ctrl,
        "controllerModels": controller_models_public(allowlist=allowlist),
        "projectUrl": _project_url(session.project_id, session.id, skill_slug=skill_slug),
        "messages": [message_to_dict(m) for m in msgs],
        "graph": await _graph_payload(db, int(session.project_id)),
        "conversationTurnCredit": int(pricing.get("conversationTurn") or 0),
        "agentPricingVersion": int(pricing.get("version") or 1),
        "referenceAssetIds": ref_ids,
        "needsCanvasKick": bool(brief.get("pendingRuntime")),
        "runtimePlan": brief.get("runtimePlan")
        if isinstance(brief.get("runtimePlan"), dict)
        else None,
        "runtimeEvents": [
            e
            for e in (brief.get("runtimeEvents") or [])
            if isinstance(e, dict)
        ][-20:]
        if isinstance(brief.get("runtimeEvents"), list)
        else [],
        "mySkillDefaults": brief.get("mySkillDefaults")
        if isinstance(brief.get("mySkillDefaults"), dict)
        else None,
        "recipePending": bool(brief.get("recipePending"))
        and not bool(brief.get("recipeApplied")),
        "recipeApplied": bool(brief.get("recipeApplied")),
    }


async def _graph_payload(db: AsyncSession, project_id: int) -> dict[str, Any] | None:
    try:
        from ..services import project_graphs

        return await project_graphs.get_graph_dict(db, project_id)
    except Exception:
        return None


async def post_session_message(
    db: AsyncSession,
    user: User,
    session_id: str,
    *,
    message: str = "",
    choice_id: str | None = None,
    action: str | None = None,
    controller_model: str | None = None,
    gen_mode: str | None = None,
    canvas_snapshot: dict | None = None,
    allow_workflow_snapshot: bool = False,
    aspect_ratio: str | None = None,
    clarity: str | None = None,
    target_market_id: str | None = None,
    artifacts: list[Any] | None = None,
) -> dict[str, Any]:
    """追加用户消息 / 回答反问选项 / 编排完成后续聊操控。"""
    from .agent_controller import set_session_controller_model

    text = (message or "").strip()
    choice = (choice_id or "").strip() or None
    act = (action or "").strip().lower() or None
    mode = (gen_mode or "").strip().lower() or "smart"
    if mode not in ("smart", "canvas", "chat"):
        mode = "smart"
    snap = canvas_snapshot if isinstance(canvas_snapshot, dict) else None
    # 前端旁白可附带 media_assets 等
    progress_artifacts = artifacts if isinstance(artifacts, list) and artifacts else None
    session = await get_session_for_user(db, user, session_id)
    if controller_model:
        set_session_controller_model(session, controller_model)
    # 记住生成模式；爆款选项写入 brief.viralRemake
    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    brief["genMode"] = mode
    # 画布操控：用户选定的生图/生视频模型写入 brief，供续聊默认 params
    if isinstance(snap, dict):
        pref_img = str(snap.get("preferredImageModel") or "").strip()
        pref_vid = str(snap.get("preferredVideoModel") or "").strip()
        if pref_img:
            brief["preferredImageModel"] = pref_img
        if pref_vid:
            brief["preferredVideoModel"] = pref_vid
    session.brief_json = brief

    skill_row = await _skill_row_for_session(db, session)

    # 前端拉片进度旁白（不对用户暴露为「续聊」）；可带生成素材 artifact
    if act in ("skill_progress", "viral_progress", "skill_done"):
        if not text and not progress_artifacts:
            fail(ErrorCode.BAD_REQUEST, message="进度消息不能为空")
        role_tag = "scriptwriter" if (skill_row and skill_row.entry_kind == "viral_remake") else "orchestrator"
        await append_message(
            db,
            session,
            role="assistant",
            content=text or "已生成素材",
            agent_role=role_tag,
            skill_id=session.skill_id,
            # 标记旁白，避免注入 LLM 历史当作用户意图
            graph_patch={"_narration": act},
            artifacts=progress_artifacts,
        )
        if act == "skill_done":
            # 拉片完成：回到可确认/可继续上传
            session.status = "awaiting_user"
        result = await list_session_messages(db, user, session_id, after_seq=0)
        result["_scheduleTeam"] = False
        result["_scheduleClarify"] = False
        return result

    # 新助手：统一 tool-call runtime（向导 / Team / 澄清 / gates 不再从本入口调度）
    from .agent_runtime import enqueue_session_runtime_turn

    return await enqueue_session_runtime_turn(
        db,
        user,
        session,
        message=text,
        choice_id=choice,
        action=act,
        canvas_snapshot=snap,
        allow_workflow_snapshot=allow_workflow_snapshot,
    )


async def attach_session_to_existing_project(
    db: AsyncSession,
    user: User,
    *,
    project_id: str,
    skill_slug: str,
    message: str | None = None,
) -> dict[str, Any]:
    """已有项目上补建 Agent Session；进入画布后由新助手读取最新快照。"""
    skill = await require_skill_by_slug(db, skill_slug, user=user)
    await require_project_access(db, user, project_id)
    try:
        pid = int(str(project_id).strip())
    except (TypeError, ValueError):
        fail(ErrorCode.INVALID_PROJECT_ID)

    session = AgentSession(
        user_id=int(user.id),
        project_id=pid,
        skill_id=int(skill.id),
        status="active",
        title=skill.title,
        brief_json={
            "viralRemake": _merge_viral_remake_brief(
                None,
                entry_kind=str(skill.entry_kind or skill.slug or "viral_remake"),
            )
        }
        if _is_wizard_skill(skill)
        else {},
    )
    db.add(session)
    await db.flush()
    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    brief["pendingRuntime"] = True
    session.brief_json = brief

    await append_message(
        db,
        session,
        role="user",
        content=(message or "").strip() or f"启动 Skill：{skill.title}",
        skill_id=int(skill.id),
    )

    session.status = "active"

    await append_message(
        db,
        session,
        role="assistant",
        content="已绑定到当前项目。进入画布后助手会读取最新画布并开始工作。",
        agent_role="orchestrator",
        skill_id=int(skill.id),
    )
    msgs = (
        await db.execute(
            select(AgentSessionMessage)
            .where(AgentSessionMessage.session_id == int(session.id))
            .order_by(AgentSessionMessage.seq.asc())
        )
    ).scalars().all()
    result = await session_to_dict(db, session, skill_slug=skill.slug, messages=list(msgs))
    result["needsCanvasKick"] = True
    return result


async def stop_agent_runtime(
    db: AsyncSession, user: User, session_id: str
) -> dict[str, Any]:
    """用户点停止：结束本轮思考与投影，会话回到可继续对话。"""
    session = await get_session_for_user(db, user, session_id)
    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    already = bool(brief.get("runtimeStopRequested")) and session.status == "awaiting_user"
    brief["runtimeStopRequested"] = True
    brief["pendingRuntime"] = False
    brief.pop("runtimeInflight", None)
    session.brief_json = brief
    flag_modified(session, "brief_json")
    session.status = "awaiting_user"
    from . import project_graphs

    await project_graphs.clear_canvas_ops(db, int(session.project_id))
    if not already:
        await append_message(
            db,
            session,
            role="assistant",
            content="已停止。画布上已落下的内容还在，你可以直接再说。",
            agent_role="orchestrator",
            skill_id=session.skill_id,
        )
    await db.flush()
    return await list_session_messages(db, user, str(session.id), after_seq=0)


async def steer_agent_runtime(
    db: AsyncSession,
    user: User,
    session_id: str,
    *,
    message: str,
) -> dict[str, Any]:
    """飞行中追加用户指令到 brief.runtimeSteer，供下一拍思考并入。"""
    from ..core.error_codes import ErrorCode
    from ..core.errors import fail
    from .agent_runtime_events import append_runtime_event

    text = (message or "").strip()
    if not text:
        fail(ErrorCode.VALIDATION_ERROR, message="请填写飞行指令")
    session = await get_session_for_user(db, user, session_id)
    brief = dict(session.brief_json) if isinstance(session.brief_json, dict) else {}
    prev = brief.get("runtimeSteer")
    if isinstance(prev, str) and prev.strip():
        brief["runtimeSteer"] = f"{prev.strip()}\n{text}"[:2000]
    elif isinstance(prev, list):
        prev.append({"message": text})
        brief["runtimeSteer"] = prev[-5:]
    else:
        brief["runtimeSteer"] = text[:2000]
    append_runtime_event(brief, kind="thinking", message=f"已收到飞行指令：{text[:80]}")
    session.brief_json = brief
    flag_modified(session, "brief_json")
    await db.flush()
    return await list_session_messages(db, user, str(session.id), after_seq=0)
