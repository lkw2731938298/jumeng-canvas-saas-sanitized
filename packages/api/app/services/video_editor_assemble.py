"""Agent assemble_timeline：从 Project Graph 镜头骨架写入剪辑台 OSS 草稿。

仅纳入已有 outputAssetId 的视频镜头（按 timeline.shotIds 排序）；
可选挂上 audio.bgm 的 outputAssetId 作为音轨。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import cst_iso_now
from ..core.entity_ids import require_entity_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.oss.canvas_storage import get_canvas_storage
from ..models.project import Project
from .asset_store import get_project_asset
from .project_graphs import get_graph_dict
from .project_scope import project_storage_folder
from .shot_detection import probe_duration_sec, require_ffmpeg
from .video_trim import _suffix_from_asset

logger = logging.getLogger(__name__)

MAX_ASSEMBLE_CLIPS = 20
MAX_CLIP_DURATION_SEC = 180.0
DEFAULT_SHOT_DURATION_SEC = 5.0
_DRAFT_REL = "editor/timeline.json"


def _new_clip_id() -> str:
    return uuid.uuid4().hex


async def _probe_asset_duration(
    storage: Any,
    asset: dict[str, Any],
    *,
    ffmpeg_ok: bool,
) -> float:
    """尽量探测真实片长；失败则用默认时长。"""
    meta_dur = asset.get("durationSec") or asset.get("duration_sec")
    try:
        if meta_dur is not None and float(meta_dur) > 0.05:
            return min(MAX_CLIP_DURATION_SEC, float(meta_dur))
    except (TypeError, ValueError):
        pass
    if not ffmpeg_ok:
        return DEFAULT_SHOT_DURATION_SEC
    oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
    if not oss_key:
        return DEFAULT_SHOT_DURATION_SEC
    try:
        data = await asyncio.to_thread(storage.get_bytes, oss_key)
        if not data:
            return DEFAULT_SHOT_DURATION_SEC
        suffix = _suffix_from_asset(asset) or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(data)
            path = tf.name
        try:
            _ffmpeg, ffprobe = require_ffmpeg()
            dur = await asyncio.to_thread(probe_duration_sec, path, ffprobe)
            if dur and dur > 0.05:
                return min(MAX_CLIP_DURATION_SEC, float(dur))
        finally:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("assemble probe duration failed: %s", exc)
    return DEFAULT_SHOT_DURATION_SEC


async def read_editor_draft_raw(project_id: str, storage_folder: str | None) -> dict[str, Any]:
    storage = get_canvas_storage()
    data = await asyncio.to_thread(
        storage.read_json, project_id, _DRAFT_REL, storage_folder=storage_folder
    )
    return data if isinstance(data, dict) else {}


async def write_editor_draft(
    project_id: str,
    *,
    storage_folder: str | None,
    clips: list[dict[str, Any]],
    track_count: int | None = None,
    audio_track_count: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """写入剪辑台草稿（与 video_editor_drafts API 同路径）。"""
    storage = get_canvas_storage()
    oss_key = storage.project_key(project_id, _DRAFT_REL, storage_folder=storage_folder)
    updated_at = cst_iso_now()
    record: dict[str, Any] = {
        "projectId": project_id,
        "clips": clips[:MAX_ASSEMBLE_CLIPS],
        "trackCount": track_count,
        "audioTrackCount": audio_track_count,
        "ossKey": oss_key,
        "updatedAt": updated_at,
    }
    if extra:
        record.update(extra)
    await asyncio.to_thread(
        storage.write_json,
        project_id,
        _DRAFT_REL,
        record,
        storage_folder=storage_folder,
    )
    return record


async def assemble_timeline_from_graph(
    db: AsyncSession,
    project_id: str | int,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """
    从 Project Graph 组装剪辑台草稿。

    replace=False：若已有非空草稿则跳过写入（避免覆盖用户手工剪辑）。
    replace=True：覆盖写入（用户/Agent 显式「组装剪辑台」）。
    """
    pid_int = require_entity_id(project_id)
    pid = str(pid_int)

    result = await db.execute(select(Project).filter(Project.id == pid_int))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    folder = project_storage_folder(project)
    existing = await read_editor_draft_raw(pid, folder)
    existing_clips = existing.get("clips") if isinstance(existing.get("clips"), list) else []
    if existing_clips and not replace:
        return {
            "projectId": pid,
            "skipped": True,
            "reason": "draft_not_empty",
            "clipCount": len(existing_clips),
            "videoClipCount": sum(
                1 for c in existing_clips if isinstance(c, dict) and c.get("kind") != "audio"
            ),
            "audioClipCount": sum(
                1 for c in existing_clips if isinstance(c, dict) and c.get("kind") == "audio"
            ),
            "message": "剪辑台已有草稿，未覆盖。可说「强制组装剪辑台」覆盖。",
            "draft": existing,
            "openEditor": True,
        }

    payload = await get_graph_dict(db, pid_int)
    graph = dict(payload.get("graph") or {}) if isinstance(payload, dict) else {}

    shots_raw = graph.get("shots") if isinstance(graph.get("shots"), list) else []
    shots_by_id = {
        str(s.get("id")): s for s in shots_raw if isinstance(s, dict) and s.get("id")
    }
    timeline = graph.get("timeline") if isinstance(graph.get("timeline"), dict) else {}
    order = [str(x).strip() for x in (timeline.get("shotIds") or []) if str(x).strip()]
    if not order:
        order = [str(s.get("id")) for s in shots_raw if isinstance(s, dict) and s.get("id")]

    storage = get_canvas_storage()
    ffmpeg_ok = True
    try:
        require_ffmpeg()
    except Exception:  # noqa: BLE001
        ffmpeg_ok = False

    clips: list[dict[str, Any]] = []
    cursor = 0.0
    missed = 0
    for shot_id in order:
        if len(clips) >= MAX_ASSEMBLE_CLIPS:
            break
        shot = shots_by_id.get(shot_id)
        if not shot:
            continue
        asset_id = str(shot.get("outputAssetId") or "").strip()
        if not asset_id:
            missed += 1
            continue
        asset = await get_project_asset(db, pid, asset_id, sync_oss=False)
        if not asset:
            missed += 1
            continue
        if str(asset.get("category") or "") != "video":
            missed += 1
            continue
        dur = await _probe_asset_duration(storage, asset, ffmpeg_ok=ffmpeg_ok)
        out_sec = max(0.3, min(MAX_CLIP_DURATION_SEC, dur))
        title = str(shot.get("action") or asset.get("title") or f"镜头 {len(clips) + 1}")[:200]
        clips.append(
            {
                "id": _new_clip_id(),
                "kind": "video",
                "assetId": asset_id,
                "title": title,
                "fileUrl": str(asset.get("fileUrl") or asset.get("file_url") or ""),
                "sourceDuration": out_sec,
                "inSec": 0.0,
                "outSec": out_sec,
                "startSec": round(cursor, 3),
                "trackIndex": 0,
                "padBeforeSec": 0.0,
                "padAfterSec": 0.0,
                "shotId": shot_id,
            }
        )
        cursor += out_sec

    audio = graph.get("audio") if isinstance(graph.get("audio"), dict) else {}
    bgm = audio.get("bgm") if isinstance(audio.get("bgm"), dict) else {}
    bgm_asset_id = str(bgm.get("outputAssetId") or "").strip()
    audio_count = 0
    if bgm_asset_id and len(clips) < MAX_ASSEMBLE_CLIPS:
        a_asset = await get_project_asset(db, pid, bgm_asset_id, sync_oss=False)
        if a_asset and str(a_asset.get("category") or "") == "audio":
            a_dur = await _probe_asset_duration(storage, a_asset, ffmpeg_ok=ffmpeg_ok)
            media_len = min(a_dur, max(cursor, 0.3), MAX_CLIP_DURATION_SEC)
            clips.append(
                {
                    "id": _new_clip_id(),
                    "kind": "audio",
                    "assetId": bgm_asset_id,
                    "title": str(bgm.get("mood") or a_asset.get("title") or "BGM")[:200],
                    "fileUrl": str(a_asset.get("fileUrl") or a_asset.get("file_url") or ""),
                    "sourceDuration": a_dur,
                    "inSec": 0.0,
                    "outSec": media_len,
                    "startSec": 0.0,
                    "trackIndex": 0,
                    "padBeforeSec": 0.0,
                    "padAfterSec": 0.0,
                }
            )
            audio_count = 1

    video_count = sum(1 for c in clips if c.get("kind") == "video")
    if video_count <= 0:
        return {
            "projectId": pid,
            "skipped": False,
            "clipCount": 0,
            "videoClipCount": 0,
            "audioClipCount": 0,
            "missedShots": missed,
            "message": "还没有带成片的镜头。请先生成各镜视频，再说「组装剪辑台」。",
            "draft": None,
            "openEditor": False,
        }

    draft = await write_editor_draft(
        pid,
        storage_folder=folder,
        clips=clips,
        track_count=2,
        audio_track_count=1,
        extra={
            "source": "agent_assemble",
            "assembledAt": cst_iso_now(),
            "shotIds": order,
        },
    )
    return {
        "projectId": pid,
        "skipped": False,
        "clipCount": len(clips),
        "videoClipCount": video_count,
        "audioClipCount": audio_count,
        "missedShots": missed,
        "message": (
            f"已将 {video_count} 段镜头写入剪辑台"
            + (f"（另有 {missed} 镜尚未生成，已跳过）" if missed else "")
            + "。正在打开剪辑台…"
        ),
        "draft": draft,
        "openEditor": True,
    }
