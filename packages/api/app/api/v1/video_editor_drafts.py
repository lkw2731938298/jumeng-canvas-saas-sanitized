"""项目级剪辑台时间线草稿：JSON 存 OSS，按 projectId 隔离（跨浏览器可恢复）。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.datetime_util import cst_iso_now
from ...core.deps import get_current_user
from ...integrations.oss.canvas_storage import get_canvas_storage
from ...models.database import get_db
from ...models.user import User
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder

router = APIRouter()

# 与前端 videoEditorTimeline 上限对齐
MAX_DRAFT_CLIPS = 20
MAX_VIDEO_TRACKS = 4
MAX_AUDIO_TRACKS = 2


def _draft_rel() -> str:
    return "editor/timeline.json"


class VideoEditorDraftPayload(BaseModel):
    """前端时间线快照（clips + 轨数）。"""

    clips: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_DRAFT_CLIPS)
    track_count: int | None = Field(None, alias="trackCount", ge=1, le=MAX_VIDEO_TRACKS)
    audio_track_count: int | None = Field(
        None, alias="audioTrackCount", ge=1, le=MAX_AUDIO_TRACKS
    )

    model_config = {"populate_by_name": True}


class VideoEditorDraftResponse(BaseModel):
    projectId: str
    clips: list[dict[str, Any]] = Field(default_factory=list)
    trackCount: int | None = None
    audioTrackCount: int | None = None
    ossKey: str = ""
    updatedAt: str = ""


def _sanitize_clip(raw: dict[str, Any]) -> dict[str, Any] | None:
    """只保留时间线必要字段，丢弃异常片段。"""
    if not isinstance(raw, dict):
        return None
    clip_id = str(raw.get("id") or "").strip()
    asset_id = str(raw.get("assetId") or raw.get("asset_id") or "").strip()
    if not clip_id or not asset_id:
        return None
    try:
        in_sec = float(raw.get("inSec", raw.get("in_sec", 0)))
        out_sec = float(raw.get("outSec", raw.get("out_sec", 0)))
    except (TypeError, ValueError):
        return None
    if not (out_sec > in_sec):
        return None
    kind = "audio" if raw.get("kind") == "audio" else "video"
    try:
        start_sec = float(raw.get("startSec", raw.get("start_sec", 0)) or 0)
        track_index = int(raw.get("trackIndex", raw.get("track_index", 0)) or 0)
        source_duration = float(raw.get("sourceDuration", raw.get("source_duration", out_sec)) or out_sec)
        pad_before = max(0.0, float(raw.get("padBeforeSec", raw.get("pad_before_sec", 0)) or 0))
        pad_after = max(0.0, float(raw.get("padAfterSec", raw.get("pad_after_sec", 0)) or 0))
    except (TypeError, ValueError):
        return None
    max_track = MAX_AUDIO_TRACKS - 1 if kind == "audio" else MAX_VIDEO_TRACKS - 1
    track_index = max(0, min(max_track, track_index))
    return {
        "id": clip_id,
        "kind": kind,
        "assetId": asset_id,
        "title": str(raw.get("title") or ("音频" if kind == "audio" else "视频"))[:200],
        # fileUrl 仅作 hydrate 兜底；权威以素材库为准
        "fileUrl": str(raw.get("fileUrl") or raw.get("file_url") or "")[:2000],
        "sourceDuration": max(out_sec, source_duration),
        "inSec": round(in_sec, 3),
        "outSec": round(out_sec, 3),
        "startSec": round(max(0.0, start_sec), 3),
        "trackIndex": track_index,
        "padBeforeSec": round(pad_before, 3),
        "padAfterSec": round(pad_after, 3),
    }


def _sanitize_clips(raw_clips: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw_clips[:MAX_DRAFT_CLIPS]:
        if not isinstance(item, dict):
            continue
        cleaned = _sanitize_clip(item)
        if cleaned:
            out.append(cleaned)
    return out


def _empty_response(project_id: str) -> VideoEditorDraftResponse:
    return VideoEditorDraftResponse(
        projectId=project_id,
        clips=[],
        trackCount=None,
        audioTrackCount=None,
        ossKey="",
        updatedAt="",
    )


@router.get("/projects/{project_id}", response_model=VideoEditorDraftResponse)
async def get_video_editor_draft(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取项目剪辑台草稿；无草稿时返回空 clips。"""
    project = await require_project_access(db, current_user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    data = await asyncio.to_thread(
        storage.read_json, project_id, _draft_rel(), storage_folder=folder
    )
    if not isinstance(data, dict):
        return _empty_response(project_id)

    clips_raw = data.get("clips")
    clips = _sanitize_clips(clips_raw) if isinstance(clips_raw, list) else []
    oss_key = str(data.get("ossKey") or "") or storage.project_key(
        project_id, _draft_rel(), storage_folder=folder
    )
    track_count = data.get("trackCount", data.get("track_count"))
    audio_track_count = data.get("audioTrackCount", data.get("audio_track_count"))
    try:
        track_count_i = int(track_count) if track_count is not None else None
    except (TypeError, ValueError):
        track_count_i = None
    try:
        audio_track_count_i = int(audio_track_count) if audio_track_count is not None else None
    except (TypeError, ValueError):
        audio_track_count_i = None

    return VideoEditorDraftResponse(
        projectId=project_id,
        clips=clips,
        trackCount=track_count_i,
        audioTrackCount=audio_track_count_i,
        ossKey=oss_key,
        updatedAt=str(data.get("updatedAt") or ""),
    )


@router.put("/projects/{project_id}", response_model=VideoEditorDraftResponse)
async def save_video_editor_draft(
    project_id: str,
    payload: VideoEditorDraftPayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """覆盖写入项目剪辑台草稿（防抖由前端控制）。"""
    project = await require_project_access(db, user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    clips = _sanitize_clips(payload.clips)
    oss_key = storage.project_key(project_id, _draft_rel(), storage_folder=folder)
    updated_at = cst_iso_now()
    track_count = payload.track_count
    audio_track_count = payload.audio_track_count
    record = VideoEditorDraftResponse(
        projectId=project_id,
        clips=clips,
        trackCount=track_count,
        audioTrackCount=audio_track_count,
        ossKey=oss_key,
        updatedAt=updated_at,
    )
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _draft_rel(),
        record.model_dump(),
        storage_folder=folder,
    )
    return record


@router.delete("/projects/{project_id}")
async def delete_video_editor_draft(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """清空项目剪辑台草稿（导出并返回后调用）。"""
    project = await require_project_access(db, user, project_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    empty = _empty_response(project_id)
    empty.updatedAt = cst_iso_now()
    empty.ossKey = storage.project_key(project_id, _draft_rel(), storage_folder=folder)
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _draft_rel(),
        empty.model_dump(),
        storage_folder=folder,
    )
    return {"ok": True}


class AssembleFromGraphBody(BaseModel):
    """从 Project Graph 镜头骨架写入剪辑台草稿。"""

    # True：覆盖已有手工草稿；False：草稿非空则跳过
    replace: bool = False


@router.post("/projects/{project_id}/assemble-from-graph")
async def assemble_video_editor_from_graph(
    project_id: str,
    body: AssembleFromGraphBody | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Agent / 用户：按 Graph timeline.shotIds 把已成片镜头写入剪辑台 OSS 草稿。"""
    await require_project_access(db, user, project_id)
    from ...services.video_editor_assemble import assemble_timeline_from_graph

    replace = bool(body.replace) if body else False
    result = await assemble_timeline_from_graph(db, project_id, replace=replace)
    return {
        "projectId": result.get("projectId") or project_id,
        "skipped": bool(result.get("skipped")),
        "reason": result.get("reason"),
        "clipCount": int(result.get("clipCount") or 0),
        "videoClipCount": int(result.get("videoClipCount") or 0),
        "audioClipCount": int(result.get("audioClipCount") or 0),
        "missedShots": int(result.get("missedShots") or 0),
        "message": str(result.get("message") or ""),
        "openEditor": bool(result.get("openEditor")),
        "updatedAt": (
            (result.get("draft") or {}).get("updatedAt")
            if isinstance(result.get("draft"), dict)
            else ""
        ),
    }
