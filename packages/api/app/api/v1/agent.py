"""Agent Session API（站内；OpenAPI Access Key 留 Phase 3）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.errors import ok
from ...models.database import get_db
from ...models.user import User
from ...services import agent_sessions, project_graphs
from ...services.project_access import require_project_access

router = APIRouter()
logger = logging.getLogger(__name__)


class CreateSessionBody(BaseModel):
    """创建会话：可选绑定 Skill；无 projectId 时自动建项。"""

    message: str = ""
    skill_id: str | None = Field(None, alias="skillId", description="兼容；优先用 skillSlug")
    skill_slug: str | None = Field(None, alias="skillSlug")
    project_id: str | None = Field(None, alias="projectId")
    project_title: str | None = Field(None, alias="projectTitle")
    style_id: str | None = Field(None, alias="styleId", description="视觉风格 visual_style.id")
    controller_model: str | None = Field(
        None, alias="controllerModel", description="编排/续聊控制器 LLM"
    )
    gen_mode: str | None = Field(
        None, alias="genMode", description="smart | canvas | chat"
    )
    # 爆款对话选项（默认由服务端 9:16 / 1080p）
    aspect_ratio: str | None = Field(None, alias="aspectRatio")
    clarity: str | None = None
    # 一键出海：目标市场版本（US / JP / …）
    target_market_id: str | None = Field(None, alias="targetMarketId")
    canvas_snapshot: dict | None = Field(
        None, alias="canvasSnapshot", description="当前画布节点/边精简快照"
    )

    model_config = {"populate_by_name": True}


class BindReferenceAssetsBody(BaseModel):
    """会话参考图：写入 brief + Graph identityLock。"""

    asset_ids: list[str] = Field(default_factory=list, alias="assetIds")

    model_config = {"populate_by_name": True}


class PostMessageBody(BaseModel):
    message: str = ""
    choice_id: str | None = Field(None, alias="choiceId")
    action: str | None = Field(
        None,
        description="submit | ignore | skill_progress | skill_done（爆款拉片进度旁白）",
    )
    controller_model: str | None = Field(None, alias="controllerModel")
    gen_mode: str | None = Field(
        None, alias="genMode", description="smart | canvas | chat"
    )
    canvas_snapshot: dict | None = Field(
        None, alias="canvasSnapshot", description="当前画布节点/边精简快照"
    )
    # 爆款对话：画幅 / 清晰度（写入 brief.viralRemake）
    aspect_ratio: str | None = Field(None, alias="aspectRatio")
    clarity: str | None = None
    # 一键出海：目标市场版本
    target_market_id: str | None = Field(None, alias="targetMarketId")
    # 进度旁白可附带 media_assets 等，回传到聊天框展示生成素材
    artifacts: list[Any] | None = None

    model_config = {"populate_by_name": True}


class AttachSessionBody(BaseModel):
    """弹窗已建项后补绑 Session。"""

    project_id: str = Field(..., alias="projectId")
    skill_slug: str = Field(..., alias="skillSlug")
    message: str | None = None

    model_config = {"populate_by_name": True}


class NodeBindingItem(BaseModel):
    shot_id: str = Field(..., alias="shotId")
    canvas_node_id: str = Field(..., alias="canvasNodeId")

    model_config = {"populate_by_name": True}


class CharacterBindingItem(BaseModel):
    character_id: str = Field(..., alias="characterId")
    canvas_node_id: str = Field(..., alias="canvasNodeId")

    model_config = {"populate_by_name": True}


class ProductBindingItem(BaseModel):
    """单一产品电影级宣传片 Skill：产品多视角定妆图节点绑定。"""

    product_id: str = Field(..., alias="productId")
    canvas_node_id: str = Field(..., alias="canvasNodeId")

    model_config = {"populate_by_name": True}


class AckCanvasOpsBody(BaseModel):
    project_id: str = Field(..., alias="projectId")
    revision: int = Field(..., ge=1)
    node_bindings: list[NodeBindingItem] | None = Field(
        None, alias="nodeBindings", description="shotId → canvasNodeId"
    )
    character_bindings: list[CharacterBindingItem] | None = Field(
        None, alias="characterBindings", description="characterId → canvasNodeId（角色定妆图节点）"
    )
    product_bindings: list[ProductBindingItem] | None = Field(
        None, alias="productBindings", description="productId → canvasNodeId（产品多视角定妆图节点）"
    )

    model_config = {"populate_by_name": True}


class ShotProgressItem(BaseModel):
    """自由创作路径：单镜生成成功后回写 Project Graph 的进度项。"""

    shot_id: str = Field(..., alias="shotId")
    output_asset_id: str | None = Field(None, alias="outputAssetId")
    status: str | None = Field(None, description="planned | generating | ready | failed")

    model_config = {"populate_by_name": True}


class SyncShotProgressBody(BaseModel):
    updates: list[ShotProgressItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class CharacterSheetProgressItem(BaseModel):
    """自由创作路径：角色定妆图生成成功后回写 identityLock 的进度项。"""

    character_id: str = Field(..., alias="characterId")
    sheet_asset_id: str = Field(..., alias="sheetAssetId")

    model_config = {"populate_by_name": True}


class SyncCharacterSheetBody(BaseModel):
    updates: list[CharacterSheetProgressItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ProductSheetProgressItem(BaseModel):
    """单一产品电影级宣传片 Skill：产品定妆图生成成功后回写 identityLock 的进度项。"""

    product_id: str = Field(..., alias="productId")
    sheet_asset_id: str = Field(..., alias="sheetAssetId")

    model_config = {"populate_by_name": True}


class SyncProductSheetBody(BaseModel):
    updates: list[ProductSheetProgressItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


def _public_session_payload(content: dict) -> dict:
    """去掉仅服务端使用的调度字段。"""
    out = dict(content or {})
    out.pop("_scheduleTeam", None)
    out.pop("_scheduleClarify", None)
    out.pop("_scheduleFollowup", None)
    out.pop("_scheduleChatOnly", None)
    out.pop("_scheduleRuntime", None)
    out.pop("_teamIdea", None)
    out.pop("_followupMessage", None)
    out.pop("_canvasSnapshot", None)
    return out


def _schedule_team_if_needed(content: dict) -> None:
    """创建会话后调度新助手 tool-call 循环。"""
    if not content.get("_scheduleRuntime"):
        return
    try:
        sid = int(str(content.get("sessionId") or "0"))
    except (TypeError, ValueError):
        sid = 0
    if sid <= 0:
        return
    msg = str(content.get("_followupMessage") or "")
    snap = content.get("_canvasSnapshot")
    snap_dict = snap if isinstance(snap, dict) else None
    from ...services.agent_runtime import run_session_runtime_job

    asyncio.create_task(run_session_runtime_job(sid, msg, snap_dict))
    logger.info("agent_runtime scheduled session_id=%s", sid)


@router.post("/sessions")
async def create_session(
    body: CreateSessionBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创作框发送：建会话（+ 可选新建项目）；后台跑 tool-call 助手（无快照则等画布 kick）。"""
    slug = (body.skill_slug or body.skill_id or "").strip() or None
    content = await agent_sessions.create_agent_session(
        db,
        current_user,
        message=body.message or "",
        skill_slug=slug,
        project_id=body.project_id,
        project_title=body.project_title,
        style_id=body.style_id,
        controller_model=body.controller_model,
        gen_mode=body.gen_mode,
        aspect_ratio=body.aspect_ratio,
        clarity=body.clarity,
        target_market_id=body.target_market_id,
        canvas_snapshot=body.canvas_snapshot,
    )
    await db.commit()
    _schedule_team_if_needed(content)
    return ok(_public_session_payload(content))


@router.post("/sessions/attach")
async def attach_session(
    body: AttachSessionBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """已有项目上补建 Session（进入画布后由新助手读取快照）。"""
    content = await agent_sessions.attach_session_to_existing_project(
        db,
        current_user,
        project_id=body.project_id,
        skill_slug=body.skill_slug,
        message=body.message,
    )
    await db.commit()
    return ok(_public_session_payload(content))


@router.post("/sessions/{session_id}/reference-assets")
async def bind_reference_assets(
    session_id: str,
    body: BindReferenceAssetsBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """绑定参考图到会话 brief 与 Graph identityLock（首页创作框 / 附件）。"""
    content = await agent_sessions.bind_session_reference_assets(
        db,
        current_user,
        session_id,
        asset_ids=list(body.asset_ids or []),
    )
    await db.commit()
    return ok(content)

@router.get("/controller-models")
async def list_controller_models(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """控制器 LLM 下拉（受后台 AI 操控模型白名单约束）。"""
    from ...services.agent_controller import controller_models_public
    from ...services.agent_skill_pricing import get_agent_skill_pricing

    _ = current_user
    pricing = await get_agent_skill_pricing(db)
    allow = pricing.get("controllerModels") or []
    allowlist = allow if allow else None
    return ok({"items": controller_models_public(allowlist=allowlist)})


@router.get("/projects/{project_id}/sessions")
async def list_project_sessions(
    project_id: str,
    limit: int = Query(30, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前项目的 Agent 会话历史。"""
    content = await agent_sessions.list_project_sessions(
        db, current_user, project_id, limit=limit
    )
    return ok(content)


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    after_seq: int = Query(0, alias="afterSeq", ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询会话消息（支持 afterSeq 增量）+ 当前 Project Graph。"""
    content = await agent_sessions.list_session_messages(
        db, current_user, session_id, after_seq=after_seq
    )
    return ok(content)


@router.post("/sessions/{session_id}/messages")
async def post_message(
    session_id: str,
    body: PostMessageBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """追加用户消息 / 回答反问；可触发澄清、Agent Team 或完成后续聊。"""
    content = await agent_sessions.post_session_message(
        db,
        current_user,
        session_id,
        message=body.message or "",
        choice_id=body.choice_id,
        action=body.action,
        controller_model=body.controller_model,
        gen_mode=body.gen_mode,
        canvas_snapshot=body.canvas_snapshot,
        aspect_ratio=body.aspect_ratio,
        clarity=body.clarity,
        target_market_id=body.target_market_id,
        artifacts=body.artifacts,
    )
    await db.commit()
    _schedule_team_if_needed(content)
    return ok(_public_session_payload(content))


class ToolResultsBody(BaseModel):
    """前端投影完客户端工具后回传结果 + 最新画布快照。"""

    results: list[dict[str, Any]] = Field(default_factory=list)
    canvas_snapshot: dict | None = Field(None, alias="canvasSnapshot")

    model_config = {"populate_by_name": True}


@router.post("/sessions/{session_id}/tool-results")
async def post_tool_results(
    session_id: str,
    body: ToolResultsBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """画布工具落地后继续思考（不再新扣对话算力）。"""
    session = await agent_sessions.get_session_for_user(db, current_user, session_id)
    snap = body.canvas_snapshot if isinstance(body.canvas_snapshot, dict) else None
    await db.commit()
    try:
        sid = int(str(session.id))
    except (TypeError, ValueError):
        sid = 0
    if sid > 0:
        from ...services.agent_runtime import (
            run_session_runtime_job,
            session_runtime_stop_requested,
        )

        if await session_runtime_stop_requested(db, sid):
            logger.info("agent_runtime skip continue (stopped) session_id=%s", sid)
        else:
            asyncio.create_task(
                run_session_runtime_job(
                    sid,
                    "（画布工具已执行，请根据最新快照继续）",
                    snap,
                    list(body.results or []),
                )
            )
            logger.info("agent_runtime continue scheduled session_id=%s", sid)
    content = await agent_sessions.list_session_messages(
        db, current_user, session_id, after_seq=0
    )
    return ok(content)


@router.post("/sessions/{session_id}/stop")
async def stop_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """停止本轮助手思考与画布投影，允许用户继续发话。"""
    content = await agent_sessions.stop_agent_runtime(db, current_user, session_id)
    await db.commit()
    return ok(content)


class SteerBody(BaseModel):
    """飞行中追加指令（不取消当前轮，下一拍思考时并入）。"""

    message: str = Field(..., min_length=1, max_length=2000)

    model_config = {"populate_by_name": True}


@router.post("/sessions/{session_id}/steer")
async def steer_session(
    session_id: str,
    body: SteerBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """turn/steer：先别出视频、改成 9:16 等，写入 brief.runtimeSteer。"""
    content = await agent_sessions.steer_agent_runtime(
        db, current_user, session_id, message=body.message
    )
    await db.commit()
    return ok(content)


@router.get("/sessions/{session_id}/events")
async def session_events_sse(
    session_id: str,
    after_id: int = Query(0, alias="afterId", ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """SSE：推送 runtimeEvents（thinking / tool / plan / ask_user）。最长约 90s。"""
    import asyncio
    import json

    from fastapi.responses import StreamingResponse

    from ...services.agent_runtime_events import list_runtime_events_since

    session = await agent_sessions.get_session_for_user(db, current_user, session_id)
    sid = int(session.id)

    async def event_gen():
        cursor = int(after_id or 0)
        # 立即发一条 comment 保活
        yield ": connected\n\n"
        for _ in range(60):
            row = await agent_sessions.get_session_for_user(
                db, current_user, str(sid)
            )
            brief = row.brief_json if isinstance(row.brief_json, dict) else {}
            events = list_runtime_events_since(brief, after_id=cursor)
            for ev in events:
                cursor = max(cursor, int(ev.get("id") or 0))
                payload = json.dumps(ev, ensure_ascii=False)
                yield f"id: {cursor}\nevent: {ev.get('kind') or 'message'}\ndata: {payload}\n\n"
            status = str(row.status or "")
            if status in ("awaiting_user", "completed", "failed", "cancelled") and not events:
                # 终态且无新事件：再等一轮后结束
                yield f"event: done\ndata: {json.dumps({'status': status}, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1.5)
        yield "event: done\ndata: {\"status\":\"timeout\"}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/projects/{project_id}/graph")
async def get_project_graph(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取项目 Graph（含 canvasOps）。"""
    await require_project_access(db, current_user, project_id)
    content = await project_graphs.get_graph_dict(db, int(project_id))
    return ok(content)


@router.post("/projects/{project_id}/graph/ack-ops")
async def ack_canvas_ops(
    project_id: str,
    body: AckCanvasOpsBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """前端已应用 canvasOps 后清空，避免重复投影；可选回写 shot→node 绑定。"""
    await require_project_access(db, current_user, project_id)
    row = await project_graphs.get_or_create_graph(db, int(project_id))
    if int(row.revision or 0) == int(body.revision):
        if body.node_bindings:
            await project_graphs.bind_shot_canvas_nodes(
                db,
                int(project_id),
                [
                    {"shotId": b.shot_id, "canvasNodeId": b.canvas_node_id}
                    for b in body.node_bindings
                ],
            )
        if body.character_bindings:
            await project_graphs.bind_character_canvas_nodes(
                db,
                int(project_id),
                [
                    {"characterId": b.character_id, "canvasNodeId": b.canvas_node_id}
                    for b in body.character_bindings
                ],
            )
        if body.product_bindings:
            await project_graphs.bind_product_canvas_nodes(
                db,
                int(project_id),
                [
                    {"productId": b.product_id, "canvasNodeId": b.canvas_node_id}
                    for b in body.product_bindings
                ],
            )
        await project_graphs.clear_canvas_ops(db, int(project_id))
        await db.commit()
    return ok({"projectId": str(project_id), "revision": int(row.revision or 0)})


@router.post("/projects/{project_id}/graph/shots/sync")
async def sync_shot_progress(
    project_id: str,
    body: SyncShotProgressBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    自由创作 Agent Team 路径：单镜（image_input / video_input）生成成功后，
    前端把最新 outputAssetId / status 同步回 Project Graph 的 shots[]——
    对齐分镜表路径已有的「改单镜后自动 reflow 整条时间线」，只是载体换成
    Project Graph（timeline.shotIds 排序已在建图时算好，这里只补状态回写）。
    """
    await require_project_access(db, current_user, project_id)
    content = await project_graphs.sync_shot_progress(
        db,
        int(project_id),
        [
            {
                "shotId": u.shot_id,
                "outputAssetId": u.output_asset_id,
                "status": u.status,
            }
            for u in body.updates
        ],
    )
    if content is None:
        content = await project_graphs.get_graph_dict(db, int(project_id))
    else:
        await db.commit()
    return ok(content)


@router.post("/projects/{project_id}/graph/characters/sync")
async def sync_character_sheet_progress(
    project_id: str,
    body: SyncCharacterSheetBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    自由创作 Agent Team 路径：角色「定妆图」节点（characterAssetRole=sheet）生成
    成功后，前端把产物 sheetAssetId 同步回 Project Graph 的
    characters[].identityLock.sheetAssetIds——identityLock 三视图闭环的回写点，
    镜头节点已通过 connect_nodes 连到该定妆图节点上游，后续手动生成镜头时会
    自动带上该形象参考。
    """
    await require_project_access(db, current_user, project_id)
    content = await project_graphs.sync_character_sheet_asset(
        db,
        int(project_id),
        [
            {"characterId": u.character_id, "sheetAssetId": u.sheet_asset_id}
            for u in body.updates
        ],
    )
    if content is None:
        content = await project_graphs.get_graph_dict(db, int(project_id))
    else:
        await db.commit()
    return ok(content)


@router.post("/projects/{project_id}/graph/products/sync")
async def sync_product_sheet_progress(
    project_id: str,
    body: SyncProductSheetBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    单一产品电影级宣传片 Skill（画布操控 followup 路径）：产品「多视角定妆图」
    节点（productAssetRole=sheet）生成成功后，前端把产物 sheetAssetId 同步回
    Project Graph 的 products[].identityLock.sheetAssetIds——对齐角色 identityLock
    闭环，但该 Skill 不经 Team 预建条目，products[] 条目由
    bind_product_canvas_nodes 在绑定时按需自动创建。
    """
    await require_project_access(db, current_user, project_id)
    content = await project_graphs.sync_product_sheet_asset(
        db,
        int(project_id),
        [
            {"productId": u.product_id, "sheetAssetId": u.sheet_asset_id}
            for u in body.updates
        ],
    )
    if content is None:
        content = await project_graphs.get_graph_dict(db, int(project_id))
    else:
        await db.commit()
    return ok(content)
