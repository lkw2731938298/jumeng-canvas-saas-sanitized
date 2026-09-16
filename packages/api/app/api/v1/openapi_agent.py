"""外部 Agent OpenAPI（对齐 LibTV IM Session 形态）。

鉴权：Authorization: Bearer <Access Key>
复用站内 Agent Session / 资产上传能力。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.datetime_util import cst_iso_now
from ...core.deps import get_openapi_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail, ok
from ...integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage, new_asset_id
from ...models.database import get_db
from ...models.user import User
from ...services import agent_sessions
from ...services.asset_store import get_project_asset, insert_project_asset
from ...services.cache import check_rate_limit, invalidate_manifest_cache
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder
from ...services.storage_quota import assert_storage_quota_for_project
from ...services.storage_urls import public_url_for_key
from ...services.video_thumbnail import extract_video_thumbnail_jpeg
from .assets import (
    ALLOWED,
    CANVAS_IMAGE_MAX_BYTES,
    _is_allowed,
    _resolve_content_type,
    _to_out,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class OpenApiSessionBody(BaseModel):
    """创建会话 / 追加消息（LibTV create_session 兼容）。"""

    session_id: str | None = Field(None, alias="sessionId")
    message: str | None = None
    skill_slug: str | None = Field(None, alias="skillSlug")
    skill_id: str | None = Field(None, alias="skillId")
    project_title: str | None = Field(None, alias="projectTitle")
    style_id: str | None = Field(None, alias="styleId")
    canvas_snapshot: dict | None = Field(
        None,
        alias="canvasSnapshot",
        description="当前画布目录快照（与站内同结构）。不传则用项目最新工作流合成。",
    )

    model_config = {"populate_by_name": True}


class ChangeProjectBody(BaseModel):
    """切换到新项目（隔离任务）。"""

    message: str | None = None
    skill_slug: str | None = Field(None, alias="skillSlug")
    canvas_snapshot: dict | None = Field(None, alias="canvasSnapshot")

    model_config = {"populate_by_name": True}


def _public_payload(content: dict) -> dict:
    """去掉仅服务端使用的调度字段；并标注待投影 canvasOps（OpenAPI 无 UI 时须自投影）。"""
    out = dict(content or {})
    out.pop("_scheduleTeam", None)
    out.pop("_scheduleClarify", None)
    out.pop("_scheduleFollowup", None)
    out.pop("_scheduleChatOnly", None)
    out.pop("_scheduleRuntime", None)
    out.pop("_teamIdea", None)
    out.pop("_followupMessage", None)
    out.pop("_canvasSnapshot", None)
    graph = out.get("graph") if isinstance(out.get("graph"), dict) else {}
    ops = graph.get("canvasOps") if isinstance(graph.get("canvasOps"), list) else []
    pending = [x for x in ops if isinstance(x, dict)]
    out["pendingCanvasOpCount"] = len(pending)
    out["projectionRequired"] = len(pending) > 0
    if pending:
        out["projectionHint"] = (
            "graph.canvasOps 待投影：请由画布客户端应用后 "
            "POST /api/v1/openapi/session/{sessionId}/tool-results；"
            "服务端不直接改 React Flow（算力/生成仍走站内链路）。"
            "也可打开站内画布页自动投影。"
        )
    return out


def _schedule_team_if_needed(content: dict) -> None:
    """创建会话后调度新助手 tool-call 循环（与站内 agent.py 对齐）。"""
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
    logger.info("openapi agent_runtime scheduled session_id=%s", sid)


@router.post("/session")
async def openapi_create_session(
    body: OpenApiSessionBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_openapi_user),
):
    """创建会话并发消息，或向已有会话追加 / 查询。"""
    sid = (body.session_id or "").strip()
    msg = (body.message or "").strip()
    slug = (body.skill_slug or body.skill_id or "").strip() or None

    if sid:
        if msg:
            content = await agent_sessions.post_session_message(
                db,
                current_user,
                sid,
                message=msg,
                canvas_snapshot=body.canvas_snapshot if isinstance(body.canvas_snapshot, dict) else None,
                allow_workflow_snapshot=True,
            )
            await db.commit()
            _schedule_team_if_needed(content)
            return ok(_public_payload(content))
        content = await agent_sessions.list_session_messages(
            db, current_user, sid, after_seq=0
        )
        return ok(content)

    content = await agent_sessions.create_agent_session(
        db,
        current_user,
        message=msg,
        skill_slug=slug,
        project_title=body.project_title,
        style_id=body.style_id,
        canvas_snapshot=body.canvas_snapshot if isinstance(body.canvas_snapshot, dict) else None,
        allow_workflow_snapshot=True,
    )
    await db.commit()
    _schedule_team_if_needed(content)
    return ok(_public_payload(content))


@router.get("/session/{session_id}")
async def openapi_get_session(
    session_id: str,
    after_seq: int = Query(0, alias="afterSeq", ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_openapi_user),
):
    """增量查询会话消息（afterSeq）。含 pending canvasOps 时带 projectionRequired。"""
    content = await agent_sessions.list_session_messages(
        db, current_user, session_id, after_seq=after_seq
    )
    return ok(_public_payload(content if isinstance(content, dict) else {}))


@router.post("/session/change-project")
async def openapi_change_project(
    body: ChangeProjectBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_openapi_user),
):
    """新开项目并创建会话（任务隔离）。"""
    content = await agent_sessions.create_agent_session(
        db,
        current_user,
        message=(body.message or "").strip() or "新项目",
        skill_slug=(body.skill_slug or "").strip() or None,
        project_title=None,
        canvas_snapshot=body.canvas_snapshot if isinstance(body.canvas_snapshot, dict) else None,
        allow_workflow_snapshot=True,
    )
    await db.commit()
    _schedule_team_if_needed(content)
    return ok(_public_payload(content))


class OpenApiToolResultsBody(BaseModel):
    """外部 Agent 投影完客户端工具后回传结果 + 最新画布快照。"""

    results: list[dict[str, Any]] = Field(default_factory=list)
    canvas_snapshot: dict | None = Field(None, alias="canvasSnapshot")

    model_config = {"populate_by_name": True}


@router.post("/session/{session_id}/tool-results")
async def openapi_tool_results(
    session_id: str,
    body: OpenApiToolResultsBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_openapi_user),
):
    """画布工具落地后继续思考（与站内 tool-results 同一循环）。无快照则用工作流合成。"""
    session = await agent_sessions.get_session_for_user(db, current_user, session_id)
    snap = body.canvas_snapshot if isinstance(body.canvas_snapshot, dict) else None
    await db.commit()
    try:
        sid = int(str(session.id))
    except (TypeError, ValueError):
        sid = 0
    if sid > 0:
        from ...services.agent_runtime import run_session_runtime_job

        asyncio.create_task(
            run_session_runtime_job(
                sid,
                "（画布工具已执行，请根据最新快照继续）",
                snap,
                list(body.results or []),
            )
        )
        logger.info("openapi agent_runtime continue scheduled session_id=%s", sid)
    content = await agent_sessions.list_session_messages(
        db, current_user, session_id, after_seq=0
    )
    return ok(_public_payload(content))


@router.post("/file/upload")
async def openapi_upload_file(
    file: UploadFile = File(...),
    project_id: str = Form(..., alias="projectId"),
    category: str | None = Form(None),
    title: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_openapi_user),
):
    """上传图片/视频/音频到项目 OSS。"""
    project = await require_project_access(db, current_user, project_id)
    folder = project_storage_folder(project)

    allowed = await check_rate_limit(f"openapi_upload:{current_user.id}", limit=20, window_s=60)
    if not allowed:
        fail(ErrorCode.UPLOAD_RATE_LIMITED)

    filename = file.filename or "upload.bin"
    ctype = file.content_type or ""
    cat = (category or "").strip().lower()
    if cat not in ALLOWED:
        if ctype.startswith("image/") or _is_allowed("image", ctype, filename):
            cat = "image"
        elif ctype.startswith("video/") or _is_allowed("video", ctype, filename):
            cat = "video"
        elif ctype.startswith("audio/") or _is_allowed("audio", ctype, filename):
            cat = "audio"
        else:
            fail(ErrorCode.INVALID_FILE_TYPE, message="仅支持图片/视频/音频")

    if not _is_allowed(cat, ctype, filename):
        fail(ErrorCode.INVALID_FILE_TYPE)

    content_type = _resolve_content_type(cat, ctype, filename)
    max_read = CANVAS_IMAGE_MAX_BYTES + 1 if cat == "image" else None
    data = await file.read(max_read) if max_read else await file.read()
    if cat == "image" and len(data) > CANVAS_IMAGE_MAX_BYTES:
        fail(ErrorCode.DOWNLOAD_TOO_LARGE, message="图片不能超过 10MB")

    thumb_data: bytes | None = None
    if cat == "video":
        ext = Path(filename).suffix.lstrip(".") or "mp4"
        thumb_data = await asyncio.to_thread(extract_video_thumbnail_jpeg, data, ext)

    total_size = len(data) + (len(thumb_data) if thumb_data else 0)
    await assert_storage_quota_for_project(db, project_id, total_size)

    asset_id = new_asset_id()
    ext = (filename or "bin").split(".")[-1] or "bin"
    rel_path = f"assets/{cat}/{asset_id}.{ext}"
    storage = get_canvas_storage()
    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel_path,
            data,
            content_type,
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"存储失败: {exc}")

    thumbnail_url = stored.file_url
    thumbnail_oss_key = ""
    if cat == "audio":
        thumbnail_url = "/uploads/audio-default.svg"
    if cat == "video" and thumb_data:
        thumb_rel = f"assets/video/{asset_id}.thumb.jpg"
        try:
            thumb_stored = await asyncio.to_thread(
                storage.put,
                project_id,
                thumb_rel,
                thumb_data,
                "image/jpeg",
                storage_folder=folder,
            )
            thumbnail_url = thumb_stored.file_url
            thumbnail_oss_key = thumb_stored.oss_key
        except StorageWriteError as exc:
            fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"缩略图存储失败: {exc}")

    record = {
        "id": asset_id,
        "projectId": project_id,
        "title": (title or Path(filename).stem or "upload").strip() or "upload",
        "category": cat,
        "subcategory": "openapi",
        "fileUrl": stored.file_url,
        "thumbnailUrl": thumbnail_url,
        "fileType": content_type,
        "fileSize": total_size,
        "ossKey": stored.oss_key,
        "source": "openapi_upload",
        "createdAt": cst_iso_now(),
    }
    if thumbnail_oss_key:
        record["thumbnailOssKey"] = thumbnail_oss_key

    saved = await insert_project_asset(db, project_id, record, sync_oss=False)
    await invalidate_manifest_cache(project_id)
    await db.commit()
    out = _to_out(saved)
    return ok(
        {
            "url": out.fileUrl,
            "assetId": out.id,
            "category": cat,
            "projectId": project_id,
        }
    )


@router.get("/file/download")
async def openapi_download_file(
    asset_id: str = Query(..., alias="assetId"),
    project_id: str = Query(..., alias="projectId"),
    session_id: str | None = Query(None, alias="sessionId"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_openapi_user),
):
    """外部 Agent 下载项目内某个素材（图/视频/音频）的结果 URL。

    按 projectId 强制隔离：只信 require_project_access，不允许仅凭全局 assetId
    跨项目取资产。可选 sessionId 仅作交叉核验（session 须属于当前 Access Key
    对应用户且 projectId 一致），不能替代项目权限校验。
    """
    if session_id:
        session = await agent_sessions.get_session_for_user(db, current_user, session_id)
        if str(session.project_id) != str(project_id):
            fail(ErrorCode.FORBIDDEN, message="sessionId 与 projectId 不匹配")
    else:
        await require_project_access(db, current_user, project_id)

    record = await get_project_asset(db, project_id, asset_id)
    if not record:
        fail(ErrorCode.ASSET_NOT_FOUND)

    oss_key = str(record.get("ossKey") or "").strip()
    url = public_url_for_key(oss_key) if oss_key else str(record.get("fileUrl") or "")
    return ok(
        {
            "url": url,
            "assetId": asset_id,
            "projectId": project_id,
            "category": record.get("category"),
            "title": record.get("title"),
        }
    )
