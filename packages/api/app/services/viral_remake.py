"""爆款复刻 · 从参考视频切镜并注册关键帧 + 分段参考视频素材。"""

from __future__ import annotations

import asyncio
import logging
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
from .shot_detection import MAX_REF_VIDEO_CLIP_SEC, split_video_to_shot_segments
from .storage_quota import assert_storage_quota_for_project
from .storage_urls import normalize_browser_storage_url

logger = logging.getLogger(__name__)

_VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")


def _suffix_from_asset(record: dict[str, Any]) -> str:
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


async def extract_and_register_shot_keyframes(
    db: AsyncSession,
    *,
    project_id: str,
    video_asset_id: str,
    strip_audio: bool = False,
) -> dict[str, Any]:
    """读取项目内参考视频 → 切镜 → 关键帧 + ≤12s 片段写入 OSS 并注册素材。

    strip_audio：出海本地化时去除运镜参考片段音轨，避免原片对白污染成片语音。
    """
    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    asset = await get_project_asset(db, project_id, video_asset_id, sync_oss=False)
    if not asset:
        fail(ErrorCode.ASSET_NOT_FOUND, message="参考视频素材不存在")
    if str(asset.get("category") or "") != "video":
        fail(ErrorCode.BAD_REQUEST, message="请上传视频素材作为爆款复刻参考")

    oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
    if not oss_key:
        fail(ErrorCode.BAD_REQUEST, message="参考视频缺少存储路径")

    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    try:
        video_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
    except Exception as exc:  # noqa: BLE001 — 统一转为业务错误
        logger.warning("viral_remake read video failed: %s", exc)
        fail(ErrorCode.BAD_REQUEST, message="无法读取参考视频，请重新上传")

    if not video_bytes:
        fail(ErrorCode.BAD_REQUEST, message="参考视频内容为空")

    suffix = _suffix_from_asset(asset)
    try:
        segments = await asyncio.to_thread(
            split_video_to_shot_segments,
            video_bytes,
            suffix=suffix,
            strip_audio=bool(strip_audio),
        )
    except ValueError as exc:
        fail(ErrorCode.BAD_REQUEST, message=str(exc))

    shots_out: list[dict[str, Any]] = []
    for seg in segments:
        # 关键帧（拉片 / 草图）
        await assert_storage_quota_for_project(db, project_id, len(seg.jpeg_bytes))
        frame_id = new_asset_id()
        rel_path = f"assets/image/{frame_id}.jpg"
        try:
            stored_frame = await asyncio.to_thread(
                storage.put,
                project_id,
                rel_path,
                seg.jpeg_bytes,
                "image/jpeg",
                storage_folder=folder,
            )
        except StorageWriteError as exc:
            fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"关键帧存储失败: {exc}")

        # 分段参考视频（SD2.0 r2v，单段≤12s）
        await assert_storage_quota_for_project(db, project_id, len(seg.clip_bytes))
        clip_id = new_asset_id()
        clip_rel = f"assets/video/{clip_id}.mp4"
        try:
            stored_clip = await asyncio.to_thread(
                storage.put,
                project_id,
                clip_rel,
                seg.clip_bytes,
                "video/mp4",
                storage_folder=folder,
            )
        except StorageWriteError as exc:
            fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"镜头片段存储失败: {exc}")

        duration_sec = max(0.3, min(seg.end_sec - seg.start_sec, MAX_REF_VIDEO_CLIP_SEC))
        # 分镜表展示/成片报价：最低 4s（物理片段可更短，生成时拉长到 4s）
        label_sec = max(4.0, duration_sec)
        if label_sec >= 10 and abs(label_sec - round(label_sec)) < 0.05:
            duration_label = f"{int(round(label_sec))}s"
        elif label_sec >= 1:
            duration_label = f"{label_sec:.1f}".rstrip("0").rstrip(".") + "s"
        else:
            duration_label = "4s"
        title = f"拉片镜头{seg.index}"
        frame_url = normalize_browser_storage_url(
            stored_frame.file_url, oss_key=stored_frame.oss_key
        )
        clip_url = normalize_browser_storage_url(
            stored_clip.file_url, oss_key=stored_clip.oss_key
        )

        frame_record = {
            "id": frame_id,
            "projectId": project_id,
            "title": title,
            "category": "image",
            "subcategory": "拉片关键帧",
            "fileUrl": frame_url,
            "thumbnailUrl": frame_url,
            "fileType": "image/jpeg",
            "fileSize": len(seg.jpeg_bytes),
            "ossKey": stored_frame.oss_key,
            "source": "viral_remake",
            "createdAt": cst_iso_now(),
        }
        clip_record = {
            "id": clip_id,
            "projectId": project_id,
            "title": f"{title}参考片段",
            "category": "video",
            "subcategory": "拉片参考片段",
            "fileUrl": clip_url,
            "thumbnailUrl": frame_url,
            "fileType": "video/mp4",
            "fileSize": len(seg.clip_bytes),
            "ossKey": stored_clip.oss_key,
            "source": "viral_remake",
            "createdAt": cst_iso_now(),
        }
        saved_frame = await insert_project_asset(db, project_id, frame_record, sync_oss=False)
        saved_clip = await insert_project_asset(db, project_id, clip_record, sync_oss=False)
        shots_out.append(
            {
                "index": seg.index,
                "startSec": round(seg.start_sec, 3),
                "endSec": round(seg.end_sec, 3),
                "durationSec": round(duration_sec, 3),
                # 分镜表时长最低 4s（成片提交用）；物理片段 durationSec 仍可更短
                "durationLabel": duration_label,
                "assetId": str(saved_frame.get("id") or frame_id),
                "fileUrl": str(saved_frame.get("fileUrl") or frame_url),
                "thumbnailUrl": str(saved_frame.get("thumbnailUrl") or frame_url),
                "title": title,
                # 成片 r2v 用本镜分段视频，勿用整段原片（可能 >12s）
                "clipAssetId": str(saved_clip.get("id") or clip_id),
                "clipFileUrl": str(saved_clip.get("fileUrl") or clip_url),
            }
        )

    await invalidate_manifest_cache(project_id)
    return {
        "videoAssetId": video_asset_id,
        "shotCount": len(shots_out),
        "shots": shots_out,
        "maxRefClipSec": MAX_REF_VIDEO_CLIP_SEC,
    }
