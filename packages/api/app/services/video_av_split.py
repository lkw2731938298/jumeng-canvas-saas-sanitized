"""画布视频节点「音视频分离」：抽出音频 + 无声视频，注册为两份新素材。"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import cst_iso_now
from ..core.entity_ids import require_entity_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage, new_asset_id
from ..models.project import Project
from .asset_store import get_project_asset, insert_project_asset
from .cache import invalidate_manifest_cache
from .project_scope import project_storage_folder
from .shot_detection import (
    MAX_VIDEO_BYTES,
    extract_audio_m4a_bytes,
    extract_silent_video_bytes,
    probe_has_audio_stream,
    require_ffmpeg,
)
from .storage_quota import assert_storage_quota_for_project
from .storage_urls import normalize_browser_storage_url
from .video_thumbnail import extract_video_thumbnail_jpeg
from .video_trim import _suffix_from_asset

logger = logging.getLogger(__name__)


async def split_project_video_av(
    db: AsyncSession,
    *,
    project_id: str,
    video_asset_id: str,
    audio_title: str | None = None,
    video_title: str | None = None,
) -> dict[str, Any]:
    """读取项目内视频 → 抽出 m4a 音频 + 无声 mp4 → 写 OSS 并注册两素材。"""
    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    asset = await get_project_asset(db, project_id, video_asset_id, sync_oss=False)
    if not asset:
        fail(ErrorCode.ASSET_NOT_FOUND, message="源视频素材不存在")
    if str(asset.get("category") or "") != "video":
        fail(ErrorCode.BAD_REQUEST, message="仅支持对视频素材进行音视频分离")

    oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
    if not oss_key:
        fail(ErrorCode.BAD_REQUEST, message="源视频缺少存储路径")

    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    try:
        video_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_av_split read video failed: %s", exc)
        fail(ErrorCode.BAD_REQUEST, message="无法读取源视频，请重新上传")

    if not video_bytes:
        fail(ErrorCode.BAD_REQUEST, message="源视频内容为空")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        fail(ErrorCode.BAD_REQUEST, message="源视频超过 200MB，无法分离")

    suffix = _suffix_from_asset(asset)
    video_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix or ".mp4", delete=False) as vf:
            vf.write(video_bytes)
            video_path = vf.name

        ffmpeg, ffprobe = require_ffmpeg()
        has_audio = await asyncio.to_thread(probe_has_audio_stream, video_path, ffprobe)
        if not has_audio:
            fail(ErrorCode.BAD_REQUEST, message="该视频没有音轨，无法分离音频")

        audio_bytes, silent_bytes = await asyncio.gather(
            asyncio.to_thread(extract_audio_m4a_bytes, video_path, ffmpeg),
            asyncio.to_thread(extract_silent_video_bytes, video_path, ffmpeg),
        )
    except ValueError as exc:
        fail(ErrorCode.BAD_REQUEST, message=str(exc))
    finally:
        if video_path:
            try:
                Path(video_path).unlink(missing_ok=True)
            except OSError:
                pass

    thumb_data = await asyncio.to_thread(extract_video_thumbnail_jpeg, silent_bytes, "mp4")
    total_size = len(audio_bytes) + len(silent_bytes) + (len(thumb_data) if thumb_data else 0)
    await assert_storage_quota_for_project(db, project_id, total_size)

    source_title = str(asset.get("title") or "").strip()
    audio_id = new_asset_id()
    silent_id = new_asset_id()

    audio_rel = f"assets/audio/{audio_id}.m4a"
    try:
        stored_audio = await asyncio.to_thread(
            storage.put,
            project_id,
            audio_rel,
            audio_bytes,
            "audio/mp4",
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"分离音频存储失败: {exc}")

    silent_rel = f"assets/video/{silent_id}.mp4"
    try:
        stored_silent = await asyncio.to_thread(
            storage.put,
            project_id,
            silent_rel,
            silent_bytes,
            "video/mp4",
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"无声视频存储失败: {exc}")

    thumb_url = normalize_browser_storage_url(
        stored_silent.file_url, oss_key=stored_silent.oss_key
    )
    thumb_key = ""
    if thumb_data:
        thumb_rel = f"assets/image/{silent_id}_thumb.jpg"
        try:
            stored_thumb = await asyncio.to_thread(
                storage.put,
                project_id,
                thumb_rel,
                thumb_data,
                "image/jpeg",
                storage_folder=folder,
            )
            thumb_url = normalize_browser_storage_url(
                stored_thumb.file_url, oss_key=stored_thumb.oss_key
            )
            thumb_key = stored_thumb.oss_key
        except StorageWriteError:
            logger.warning("video_av_split thumbnail upload failed for %s", silent_id)

    default_audio_title = f"{source_title} · 分离音频" if source_title else "分离音频"
    default_video_title = f"{source_title} · 无声视频" if source_title else "无声视频"

    audio_record = {
        "id": audio_id,
        "projectId": project_id,
        "title": (audio_title or "").strip() or default_audio_title,
        "category": "audio",
        "subcategory": "音视频分离",
        "fileUrl": stored_audio.file_url,
        "thumbnailUrl": normalize_browser_storage_url(
            stored_audio.file_url, oss_key=stored_audio.oss_key
        ),
        "fileType": "audio/mp4",
        "fileSize": len(audio_bytes),
        "ossKey": stored_audio.oss_key,
        "source": "video_av_split",
        "createdAt": cst_iso_now(),
    }
    silent_record = {
        "id": silent_id,
        "projectId": project_id,
        "title": (video_title or "").strip() or default_video_title,
        "category": "video",
        "subcategory": "无声视频",
        "fileUrl": stored_silent.file_url,
        "thumbnailUrl": thumb_url,
        "fileType": "video/mp4",
        "fileSize": len(silent_bytes),
        "ossKey": stored_silent.oss_key,
        "source": "video_av_split",
        "createdAt": cst_iso_now(),
    }
    if thumb_key:
        silent_record["thumbnailOssKey"] = thumb_key

    saved_audio = await insert_project_asset(db, project_id, audio_record, sync_oss=False)
    saved_silent = await insert_project_asset(db, project_id, silent_record, sync_oss=False)
    await invalidate_manifest_cache(project_id)

    return {
        "audioAsset": saved_audio,
        "videoAsset": saved_silent,
        "sourceAssetId": video_asset_id,
    }
