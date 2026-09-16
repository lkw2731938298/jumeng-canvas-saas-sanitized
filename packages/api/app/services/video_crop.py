"""画布视频节点「裁剪」：按归一化矩形裁切画面并注册为新素材。"""

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
    crop_video_bytes,
    probe_video_size,
    require_ffmpeg,
)
from .storage_quota import assert_storage_quota_for_project
from .storage_urls import normalize_browser_storage_url
from .video_thumbnail import extract_video_thumbnail_jpeg
from .video_trim import _suffix_from_asset

logger = logging.getLogger(__name__)

MIN_CROP_NORM = 0.05


async def crop_project_video_asset(
    db: AsyncSession,
    *,
    project_id: str,
    video_asset_id: str,
    rect_x: float,
    rect_y: float,
    rect_w: float,
    rect_h: float,
    title: str | None = None,
) -> dict[str, Any]:
    """读取项目内视频 → ffmpeg 空间裁剪 → 写 OSS 并注册新视频素材。"""
    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    asset = await get_project_asset(db, project_id, video_asset_id, sync_oss=False)
    if not asset:
        fail(ErrorCode.ASSET_NOT_FOUND, message="源视频素材不存在")
    if str(asset.get("category") or "") != "video":
        fail(ErrorCode.BAD_REQUEST, message="仅支持对视频素材进行裁剪")

    oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
    if not oss_key:
        fail(ErrorCode.BAD_REQUEST, message="源视频缺少存储路径")

    try:
        nx, ny, nw, nh = float(rect_x), float(rect_y), float(rect_w), float(rect_h)
    except (TypeError, ValueError):
        fail(ErrorCode.VALIDATION_ERROR, message="裁剪区域无效")

    if nw < MIN_CROP_NORM or nh < MIN_CROP_NORM:
        fail(ErrorCode.VALIDATION_ERROR, message="裁剪区域过小")
    if nx < 0 or ny < 0 or nx + nw > 1.001 or ny + nh > 1.001:
        fail(ErrorCode.VALIDATION_ERROR, message="裁剪区域超出画面")

    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    try:
        video_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_crop read video failed: %s", exc)
        fail(ErrorCode.BAD_REQUEST, message="无法读取源视频，请重新上传")

    if not video_bytes:
        fail(ErrorCode.BAD_REQUEST, message="源视频内容为空")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        fail(ErrorCode.BAD_REQUEST, message="源视频超过 200MB，无法裁剪")

    suffix = _suffix_from_asset(asset)
    video_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix or ".mp4", delete=False) as vf:
            vf.write(video_bytes)
            video_path = vf.name

        ffmpeg, ffprobe = require_ffmpeg()
        vw, vh = await asyncio.to_thread(probe_video_size, video_path, ffprobe)
        px = int(round(nx * vw))
        py = int(round(ny * vh))
        pw = int(round(nw * vw))
        ph = int(round(nh * vh))
        # 钳制到画面内且至少 2px
        pw = max(2, min(pw, vw - px))
        ph = max(2, min(ph, vh - py))
        px = max(0, min(px, vw - 2))
        py = max(0, min(py, vh - 2))

        cropped = await asyncio.to_thread(
            crop_video_bytes,
            video_path,
            ffmpeg,
            x=px,
            y=py,
            w=pw,
            h=ph,
        )
    except ValueError as exc:
        fail(ErrorCode.BAD_REQUEST, message=str(exc))
    finally:
        if video_path:
            try:
                Path(video_path).unlink(missing_ok=True)
            except OSError:
                pass

    thumb_data = await asyncio.to_thread(extract_video_thumbnail_jpeg, cropped, "mp4")
    total_size = len(cropped) + (len(thumb_data) if thumb_data else 0)
    await assert_storage_quota_for_project(db, project_id, total_size)

    clip_id = new_asset_id()
    rel_path = f"assets/video/{clip_id}.mp4"
    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel_path,
            cropped,
            "video/mp4",
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"裁剪片段存储失败: {exc}")

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
            logger.warning("video_crop thumbnail upload failed for %s", clip_id)

    source_title = str(asset.get("title") or "").strip()
    default_title = f"{source_title} · 裁剪" if source_title else "裁剪"
    record = {
        "id": clip_id,
        "projectId": project_id,
        "title": (title or "").strip() or default_title,
        "category": "video",
        "subcategory": "裁剪",
        "fileUrl": stored.file_url,
        "thumbnailUrl": thumb_url,
        "fileType": "video/mp4",
        "fileSize": len(cropped),
        "ossKey": stored.oss_key,
        "source": "video_crop",
        "createdAt": cst_iso_now(),
    }
    if thumb_key:
        record["thumbnailOssKey"] = thumb_key

    saved = await insert_project_asset(db, project_id, record, sync_oss=False)
    await invalidate_manifest_cache(project_id)

    return {
        "asset": saved,
        "rect": {"x": round(nx, 4), "y": round(ny, 4), "w": round(nw, 4), "h": round(nh, 4)},
        "sourceAssetId": video_asset_id,
    }
