"""画布视频节点「剪辑」：按入/出点切段并注册为新素材。"""

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
    MAX_USER_TRIM_DURATION_SEC,
    MAX_VIDEO_BYTES,
    MIN_USER_TRIM_DURATION_SEC,
    extract_clip_bytes,
    probe_duration_sec,
    require_ffmpeg,
)
from .storage_quota import assert_storage_quota_for_project
from .storage_urls import normalize_browser_storage_url
from .video_thumbnail import extract_video_thumbnail_jpeg

logger = logging.getLogger(__name__)

_VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")


def _suffix_from_asset(record: dict[str, Any]) -> str:
    """从素材记录推断临时文件后缀。"""
    oss_key = str(record.get("ossKey") or record.get("oss_key") or "").lower()
    for ext in _VIDEO_EXT:
        if oss_key.endswith(ext):
            return ext
    file_type = str(record.get("fileType") or record.get("file_type") or "").lower()
    if "webm" in file_type:
        return ".webm"
    if "quicktime" in file_type or "mov" in file_type:
        return ".mov"
    return ".mp4"


async def trim_project_video_asset(
    db: AsyncSession,
    *,
    project_id: str,
    video_asset_id: str,
    in_sec: float,
    out_sec: float,
    title: str | None = None,
) -> dict[str, Any]:
    """读取项目内视频 → ffmpeg 切 [in, out) → 写 OSS 并注册新视频素材。"""
    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    asset = await get_project_asset(db, project_id, video_asset_id, sync_oss=False)
    if not asset:
        fail(ErrorCode.ASSET_NOT_FOUND, message="源视频素材不存在")
    if str(asset.get("category") or "") != "video":
        fail(ErrorCode.BAD_REQUEST, message="仅支持对视频素材进行剪辑")

    oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
    if not oss_key:
        fail(ErrorCode.BAD_REQUEST, message="源视频缺少存储路径")

    try:
        in_v = float(in_sec)
        out_v = float(out_sec)
    except (TypeError, ValueError):
        fail(ErrorCode.VALIDATION_ERROR, message="入点/出点无效")

    if in_v < 0 or out_v <= in_v:
        fail(ErrorCode.VALIDATION_ERROR, message="出点必须大于入点")
    clip_dur = out_v - in_v
    if clip_dur < MIN_USER_TRIM_DURATION_SEC:
        fail(
            ErrorCode.VALIDATION_ERROR,
            message=f"片段至少 {MIN_USER_TRIM_DURATION_SEC:g} 秒",
        )
    if clip_dur > MAX_USER_TRIM_DURATION_SEC + 0.05:
        fail(
            ErrorCode.VALIDATION_ERROR,
            message=f"片段最长 {MAX_USER_TRIM_DURATION_SEC:g} 秒",
        )

    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    try:
        video_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
    except Exception as exc:  # noqa: BLE001 — 统一转为业务错误
        logger.warning("video_trim read video failed: %s", exc)
        fail(ErrorCode.BAD_REQUEST, message="无法读取源视频，请重新上传")

    if not video_bytes:
        fail(ErrorCode.BAD_REQUEST, message="源视频内容为空")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        fail(ErrorCode.BAD_REQUEST, message="源视频超过 200MB，无法剪辑")

    suffix = _suffix_from_asset(asset)
    video_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix or ".mp4", delete=False) as vf:
            vf.write(video_bytes)
            video_path = vf.name

        ffmpeg, ffprobe = require_ffmpeg()
        duration = await asyncio.to_thread(probe_duration_sec, video_path, ffprobe)
        # 允许极小浮点误差；出点略超时长则钳到片尾
        if in_v >= duration - 0.05:
            fail(ErrorCode.VALIDATION_ERROR, message="入点超出视频时长")
        end_clamped = min(out_v, duration)
        if end_clamped - in_v < MIN_USER_TRIM_DURATION_SEC:
            fail(ErrorCode.VALIDATION_ERROR, message="有效片段过短，请调整入出点")

        clip_bytes = await asyncio.to_thread(
            extract_clip_bytes,
            video_path,
            ffmpeg,
            start_sec=in_v,
            end_sec=end_clamped,
            max_duration_sec=MAX_USER_TRIM_DURATION_SEC,
            min_duration_sec=MIN_USER_TRIM_DURATION_SEC,
        )
    except ValueError as exc:
        fail(ErrorCode.BAD_REQUEST, message=str(exc))
    finally:
        if video_path:
            try:
                Path(video_path).unlink(missing_ok=True)
            except OSError:
                pass

    thumb_data = await asyncio.to_thread(extract_video_thumbnail_jpeg, clip_bytes, "mp4")
    total_size = len(clip_bytes) + (len(thumb_data) if thumb_data else 0)
    await assert_storage_quota_for_project(db, project_id, total_size)

    clip_id = new_asset_id()
    rel_path = f"assets/video/{clip_id}.mp4"
    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel_path,
            clip_bytes,
            "video/mp4",
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"剪辑片段存储失败: {exc}")

    thumb_url = normalize_browser_storage_url(stored.file_url, oss_key=stored.oss_key)
    thumb_key = ""
    if thumb_data:
        thumb_rel = f"assets/image/{clip_id}_thumb.jpg"
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
            logger.warning("video_trim thumbnail upload failed for %s", clip_id)

    source_title = str(asset.get("title") or "").strip()
    default_title = f"{source_title} · 剪辑" if source_title else "剪辑"
    record = {
        "id": clip_id,
        "projectId": project_id,
        "title": (title or "").strip() or default_title,
        "category": "video",
        "subcategory": "剪辑",
        "fileUrl": stored.file_url,
        "thumbnailUrl": thumb_url,
        "fileType": "video/mp4",
        "fileSize": len(clip_bytes),
        "ossKey": stored.oss_key,
        "source": "video_trim",
        "createdAt": cst_iso_now(),
    }
    if thumb_key:
        record["thumbnailOssKey"] = thumb_key

    saved = await insert_project_asset(db, project_id, record, sync_oss=False)
    await invalidate_manifest_cache(project_id)

    out_actual = round(end_clamped, 3)
    in_actual = round(in_v, 3)
    return {
        "asset": saved,
        "inSec": in_actual,
        "outSec": out_actual,
        "durationSec": round(out_actual - in_actual, 3),
        "sourceAssetId": video_asset_id,
    }
