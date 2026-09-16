"""项目素材接口：按项目隔离的素材列表、批量查询、上传（含模型包）与删除，写入 OSS 并维护索引。"""

from ...core.datetime_util import cst_iso_now, now_cst_naive
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail, ok
from ...integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage, new_asset_id
from ...models.database import get_db
from ...models.user import User
from ...services.asset_store import (
    delete_oss_object,
    delete_project_asset,
    get_project_asset,
    get_project_assets_by_ids,
    insert_project_asset,
    list_project_assets,
)
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder
from ...services.storage_quota import assert_storage_quota_for_project
from ...services.storage_urls import normalize_browser_storage_url
from ...services.video_thumbnail import extract_video_thumbnail_jpeg
from ...services.video_trim import trim_project_video_asset
from ...services.video_compose import compose_project_video_assets
from ...services.video_crop import crop_project_video_asset
from ...services.video_av_split import split_project_video_av
from ...services.video_frame_edit import (
    MAX_VIDEO_FRAME_EDIT_SEC,
    edit_project_video_frames,
)
from ...services.cache import check_rate_limit, get_cached_manifest, invalidate_manifest_cache, set_cached_manifest

router = APIRouter()

CANVAS_IMAGE_MAX_BYTES = 10 * 1024 * 1024
# 单用户上传限流：须覆盖宫格切分上限 5×5=25 次/次操作，并留余量给同窗口内其它上传
UPLOAD_RATE_LIMIT = 60
UPLOAD_RATE_WINDOW_S = 60

ALLOWED: dict[str, list[str]] = {
    "image": ["image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/svg+xml"],
    "video": ["video/mp4", "video/webm", "video/quicktime", "video/x-msvideo", "video/x-matroska"],
    "audio": [
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/x-wav",
        "audio/ogg",
        "audio/flac",
        "audio/mp4",
        "audio/x-m4a",
        "audio/aac",
        "audio/webm",
    ],
    # 文档：Office / PDF / 文本 / Apple iWork，供万相 3.0 file 参考等
    "document": [
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
        "application/vnd.apple.keynote",
        "application/x-iwork-keynote-sffkey",
        "application/vnd.apple.pages",
        "application/x-iwork-pages-sffpages",
        "application/vnd.apple.numbers",
        "application/x-iwork-numbers-sffnumbers",
    ],
    "model": [
        "model/gltf-binary",
        "model/gltf+json",
        "application/octet-stream",
    ],
}

EXT_TO_MIME: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".key": "application/vnd.apple.keynote",
    ".pages": "application/vnd.apple.pages",
    ".numbers": "application/vnd.apple.numbers",
}

# 文档：单文件 ≤100MB；页数 ≤50（见 document_page_count）
CANVAS_DOCUMENT_MAX_BYTES = 100 * 1024 * 1024
DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".md",
    ".txt",
    ".key",
    ".pages",
    ".numbers",
}

# GLTF 模型包常见附属资源（贴图、缓冲等）
MODEL_BUNDLE_EXTENSIONS = {
    ".bin",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".ktx",
    ".ktx2",
    ".basis",
    ".dds",
    ".mtl",
    ".obj",
}


class AssetOut(BaseModel):
    """素材响应模型：素材唯一 ID、所属项目、分类及可访问的文件/缩略图 URL 等。"""

    id: str
    projectId: str
    legacyId: str | None = None
    title: str
    category: str
    subcategory: str | None
    fileUrl: str
    thumbnailUrl: str
    fileType: str
    fileSize: int
    ossKey: str
    createdAt: str


def _to_out(item: dict) -> AssetOut:
    oss_key = item.get("ossKey") or item.get("oss_key")
    normalized = {
        **item,
        "fileUrl": normalize_browser_storage_url(item.get("fileUrl", ""), oss_key=oss_key),
    }
    if "thumbnailUrl" in normalized:
        thumb = normalized["thumbnailUrl"]
        file_url = normalized["fileUrl"]
        if thumb and thumb.startswith("/uploads/"):
            pass
        elif not thumb or thumb == item.get("fileUrl") or thumb == oss_key:
            normalized["thumbnailUrl"] = file_url
        else:
            normalized["thumbnailUrl"] = normalize_browser_storage_url(thumb)
    return AssetOut(**normalized)


def _resolve_content_type(category: str, content_type: str, filename: str | None) -> str:
    ext = Path(filename or "").suffix.lower()
    by_ext = EXT_TO_MIME.get(ext)
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized in ALLOWED.get(category, []):
        return normalized
    if by_ext and by_ext in ALLOWED.get(category, []):
        return by_ext
    if not normalized or normalized == "application/octet-stream":
        # by_ext 已是 MIME；勿误写成 `by_ext in EXT_TO_MIME`（键是扩展名）
        if by_ext:
            return by_ext
    return normalized or "application/octet-stream"


def _is_allowed(category: str, content_type: str, filename: str | None) -> bool:
    resolved = _resolve_content_type(category, content_type, filename)
    allowed = ALLOWED.get(category, [])
    if resolved in allowed:
        return True
    ext = Path(filename or "").suffix.lower()
    by_ext = EXT_TO_MIME.get(ext)
    if category == "model":
        if ext in {".glb", ".gltf"} or ext in MODEL_BUNDLE_EXTENSIONS:
            return True
    # 文档：MIME 不准时仍允许白名单扩展（iWork 等常报 octet-stream）
    if category == "document" and ext in DOCUMENT_EXTENSIONS:
        return True
    return by_ext in allowed if by_ext else False


def _assert_document_upload(data: bytes, filename: str | None) -> None:
    """文档大小与页数硬限制（单文件 ≤100MB，页数 ≤50）。"""
    if len(data) > CANVAS_DOCUMENT_MAX_BYTES:
        fail(ErrorCode.DOWNLOAD_TOO_LARGE, message="文档不能超过 100MB")
    from ...services.document_page_count import assert_document_page_limit

    assert_document_page_limit(data, filename)


@router.get("", response_model=list[AssetOut])
async def list_assets(
    projectId: str,
    category: str | None = None,
    subcategory: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出指定项目的素材，可按分类/子分类过滤，无过滤时走 manifest 缓存。"""
    await require_project_access(db, current_user, projectId)
    if not category and not subcategory:
        cached = await get_cached_manifest(projectId)
        if cached:
            return [_to_out(a) for a in cached]

    items = await list_project_assets(
        db,
        projectId,
        category=category,
        subcategory=subcategory,
    )
    if not category and not subcategory:
        await set_cached_manifest(projectId, items)
    return [_to_out(a) for a in items]


@router.get("/batch", response_model=list[AssetOut])
async def batch_get_assets(
    projectId: str,
    ids: str = Query(..., description="Comma-separated asset ids (numeric or legacy UUID)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按逗号分隔的 id 批量解析素材（单次最多 200 个）。"""
    await require_project_access(db, current_user, projectId)
    asset_ids = [part.strip() for part in ids.split(",") if part.strip()]
    if not asset_ids:
        return []
    if len(asset_ids) > 200:
        fail(ErrorCode.BATCH_TOO_LARGE, content={"max": 200})

    matched = await get_project_assets_by_ids(db, projectId, asset_ids)
    return [_to_out(a) for a in matched]


@router.post("/model-bundle", response_model=AssetOut, status_code=201)
async def upload_model_bundle(
    files: list[UploadFile] = File(...),
    projectId: str = Form(...),
    title: str = Form("未命名"),
    subcategory: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传 GLTF 模型包（如 .gltf + buffer.bin 等附属资源），归入同一素材包 ID。"""
    project = await require_project_access(db, user, projectId)
    folder = project_storage_folder(project)
    allowed = await check_rate_limit(
        f"upload:{user.id}",
        limit=UPLOAD_RATE_LIMIT,
        window_s=UPLOAD_RATE_WINDOW_S,
    )
    if not allowed:
        fail(ErrorCode.UPLOAD_RATE_LIMITED)
    if not files:
        fail(ErrorCode.MODEL_BUNDLE_EMPTY)

    pack_id = new_asset_id()
    storage = get_canvas_storage()
    stored_main = None
    main_name: str | None = None
    main_type: str | None = None
    total_size = 0
    seen_names: set[str] = set()
    pending_files: list[tuple[str, bytes, str]] = []

    for file in files:
        raw_name = Path(file.filename or "model.bin").name
        if ".." in raw_name or not raw_name:
            fail(ErrorCode.INVALID_FILENAME, content={"filename": file.filename})
        if raw_name in seen_names:
            continue
        seen_names.add(raw_name)
        if not _is_allowed("model", file.content_type or "", raw_name):
            fail(
                ErrorCode.INVALID_FILE_TYPE,
                message=f"不允许的文件类型: {file.content_type or 'unknown'} ({raw_name})",
            )

        content_type = _resolve_content_type("model", file.content_type or "", raw_name)
        data = await file.read()
        total_size += len(data)
        pending_files.append((raw_name, data, content_type))

    await assert_storage_quota_for_project(db, projectId, total_size)

    for raw_name, data, content_type in pending_files:
        rel_path = f"assets/model/{pack_id}/{raw_name}"
        try:
            stored = await asyncio.to_thread(
                storage.put,
                projectId,
                rel_path,
                data,
                content_type,
                storage_folder=folder,
            )
        except StorageWriteError as exc:
            fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"存储失败: {exc}")

        lower = raw_name.lower()
        if lower.endswith((".gltf", ".glb")) and stored_main is None:
            stored_main = stored
            main_name = raw_name
            main_type = content_type

    if not stored_main or not main_name:
        fail(ErrorCode.MODEL_BUNDLE_NO_MAIN)

    record = {
        "id": pack_id,
        "projectId": projectId,
        "title": title.strip() or Path(main_name).stem or "未命名",
        "category": "model",
        "subcategory": subcategory,
        "fileUrl": stored_main.file_url,
        "thumbnailUrl": "/uploads/model-default.svg",
        "fileType": main_type or "model/gltf+json",
        "fileSize": total_size,
        "ossKey": stored_main.oss_key,
        "source": "upload",
        "createdAt": cst_iso_now(),
    }
    saved = await insert_project_asset(db, projectId, record, sync_oss=False)
    await invalidate_manifest_cache(projectId)
    return _to_out(saved)


class VideoTrimBody(BaseModel):
    """视频节点剪辑：按入/出点导出新素材。"""

    project_id: str = Field(..., alias="projectId")
    video_asset_id: str = Field(..., alias="videoAssetId")
    in_sec: float = Field(..., alias="inSec", ge=0)
    out_sec: float = Field(..., alias="outSec", gt=0)
    title: str | None = None

    model_config = {"populate_by_name": True}


@router.post("/video-trim")
async def trim_video_asset(
    body: VideoTrimBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按入点/出点切分视频并注册为新素材（原素材不变）。"""
    project_id = (body.project_id or "").strip()
    video_asset_id = (body.video_asset_id or "").strip()
    if not project_id or not video_asset_id:
        fail(ErrorCode.BAD_REQUEST, message="缺少 projectId 或 videoAssetId")

    await require_project_access(db, current_user, project_id)

    allowed = await check_rate_limit(
        f"video_trim:{current_user.id}",
        limit=12,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="剪辑请求过于频繁，请稍后再试")

    result = await trim_project_video_asset(
        db,
        project_id=project_id,
        video_asset_id=video_asset_id,
        in_sec=body.in_sec,
        out_sec=body.out_sec,
        title=body.title,
    )
    asset_out = _to_out(result["asset"])
    return ok(
        {
            "asset": asset_out.model_dump(by_alias=True),
            "inSec": result["inSec"],
            "outSec": result["outSec"],
            "durationSec": result["durationSec"],
            "sourceAssetId": result["sourceAssetId"],
        }
    )


class VideoComposeClipIn(BaseModel):
    """多轨时间线片段：源素材 + 入出点 + 可选绝对时间/轨号/空镜。"""

    video_asset_id: str = Field(..., alias="videoAssetId")
    in_sec: float = Field(..., alias="inSec", ge=0)
    out_sec: float = Field(..., alias="outSec", gt=0)
    start_sec: float | None = Field(None, alias="startSec", ge=0)
    track_index: int | None = Field(None, alias="trackIndex", ge=0, le=3)
    pad_before_sec: float | None = Field(None, alias="padBeforeSec", ge=0)
    pad_after_sec: float | None = Field(None, alias="padAfterSec", ge=0)

    model_config = {"populate_by_name": True}


class VideoComposeAudioIn(BaseModel):
    """独立音轨片段。"""

    audio_asset_id: str = Field(..., alias="audioAssetId")
    in_sec: float = Field(..., alias="inSec", ge=0)
    out_sec: float = Field(..., alias="outSec", gt=0)
    start_sec: float = Field(0, alias="startSec", ge=0)
    pad_before_sec: float = Field(0, alias="padBeforeSec", ge=0)
    pad_after_sec: float = Field(0, alias="padAfterSec", ge=0)
    track_index: int = Field(0, alias="trackIndex", ge=0, le=1)

    model_config = {"populate_by_name": True}


class VideoComposeBody(BaseModel):
    """完整剪辑：多轨压平（高轨优先）后 concat，可选混音轨。"""

    project_id: str = Field(..., alias="projectId")
    clips: list[VideoComposeClipIn] = Field(..., min_length=1, max_length=20)
    audio_clips: list[VideoComposeAudioIn] | None = Field(None, alias="audioClips", max_length=20)
    title: str | None = None

    model_config = {"populate_by_name": True}


@router.post("/video-compose")
async def compose_video_assets(
    body: VideoComposeBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """多轨时间线压平拼接：优先异步（返回 taskId），Redis 不可用时同步降级。"""
    from ...services.video_compose_tasks import (
        new_compose_task_id,
        run_compose_task_background,
        save_compose_task,
    )

    project_id = (body.project_id or "").strip()
    if not project_id:
        fail(ErrorCode.BAD_REQUEST, message="缺少 projectId")

    await require_project_access(db, current_user, project_id)

    allowed = await check_rate_limit(
        f"video_compose:{current_user.id}",
        limit=6,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="拼接请求过于频繁，请稍后再试")

    clips_raw: list[dict] = []
    for item in body.clips:
        row: dict = {
            "videoAssetId": item.video_asset_id,
            "inSec": item.in_sec,
            "outSec": item.out_sec,
        }
        if item.start_sec is not None:
            row["startSec"] = item.start_sec
        if item.track_index is not None:
            row["trackIndex"] = item.track_index
        if item.pad_before_sec is not None:
            row["padBeforeSec"] = item.pad_before_sec
        if item.pad_after_sec is not None:
            row["padAfterSec"] = item.pad_after_sec
        clips_raw.append(row)
    audio_raw: list[dict] | None = None
    if body.audio_clips:
        audio_raw = [
            {
                "audioAssetId": a.audio_asset_id,
                "inSec": a.in_sec,
                "outSec": a.out_sec,
                "startSec": a.start_sec,
                "padBeforeSec": a.pad_before_sec,
                "padAfterSec": a.pad_after_sec,
                "trackIndex": a.track_index,
            }
            for a in body.audio_clips
        ]

    task_id = new_compose_task_id()
    seeded = await save_compose_task(
        task_id,
        {
            "taskId": task_id,
            "projectId": project_id,
            "userId": int(current_user.id),
            "status": "pending",
            "progress": 0,
            "message": "排队中…",
        },
    )
    if not seeded:
        # Redis 不可用：同步降级，保证功能可用
        result = await compose_project_video_assets(
            db,
            project_id=project_id,
            clips=clips_raw,
            audio_clips=audio_raw,
            title=body.title,
        )
        asset_out = _to_out(result["asset"])
        return ok(
            {
                "taskId": "",
                "status": "succeeded",
                "mode": "sync_fallback",
                "progress": 100,
                "asset": asset_out.model_dump(by_alias=True),
                "durationSec": result["durationSec"],
                "clipCount": result["clipCount"],
            }
        )

    asyncio.create_task(
        run_compose_task_background(
            task_id=task_id,
            project_id=project_id,
            clips=clips_raw,
            audio_clips=audio_raw,
            title=body.title,
            user_id=int(current_user.id),
        ),
        name=f"video-compose-{task_id[:8]}",
    )
    return ok(
        {
            "taskId": task_id,
            "status": "pending",
            "mode": "async",
            "progress": 0,
            "message": "拼接任务已启动，请轮询状态",
        }
    )


@router.get("/video-compose/{task_id}")
async def get_video_compose_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """轮询完整剪辑拼接任务状态与进度。"""
    from ...services.video_compose_tasks import load_compose_task

    tid = (task_id or "").strip()
    if not tid:
        fail(ErrorCode.BAD_REQUEST, message="缺少 taskId")

    state = await load_compose_task(tid)
    if not state:
        fail(ErrorCode.NOT_FOUND, message="拼接任务不存在或已过期")

    project_id = str(state.get("projectId") or "").strip()
    if project_id:
        await require_project_access(db, current_user, project_id)

    owner = state.get("userId")
    if owner is not None and int(owner) != int(current_user.id):
        fail(ErrorCode.NOT_FOUND, message="拼接任务不存在或已过期")

    result = state.get("result") if isinstance(state.get("result"), dict) else None
    asset_raw = (result or {}).get("asset") if result else None
    asset_out = None
    if isinstance(asset_raw, dict) and asset_raw.get("id"):
        try:
            asset_out = _to_out(asset_raw).model_dump(by_alias=True)
        except Exception:  # noqa: BLE001
            asset_out = asset_raw

    return ok(
        {
            "taskId": tid,
            "status": str(state.get("status") or "pending"),
            "progress": float(state.get("progress") or 0),
            "message": str(state.get("message") or ""),
            "error": state.get("error"),
            "asset": asset_out,
            "durationSec": (result or {}).get("durationSec") if result else None,
            "clipCount": (result or {}).get("clipCount") if result else None,
        }
    )


class VideoCropRectBody(BaseModel):
    """归一化裁剪框（0–1）。"""

    x: float = Field(..., ge=0, le=1)
    y: float = Field(..., ge=0, le=1)
    w: float = Field(..., gt=0, le=1)
    h: float = Field(..., gt=0, le=1)


class VideoCropBody(BaseModel):
    """视频节点空间裁剪：按画面矩形导出新素材。"""

    project_id: str = Field(..., alias="projectId")
    video_asset_id: str = Field(..., alias="videoAssetId")
    rect: VideoCropRectBody
    title: str | None = None

    model_config = {"populate_by_name": True}


@router.post("/video-crop")
async def crop_video_asset(
    body: VideoCropBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按归一化矩形裁剪视频画面并注册为新素材（原素材不变）。"""
    project_id = (body.project_id or "").strip()
    video_asset_id = (body.video_asset_id or "").strip()
    if not project_id or not video_asset_id:
        fail(ErrorCode.BAD_REQUEST, message="缺少 projectId 或 videoAssetId")

    await require_project_access(db, current_user, project_id)

    allowed = await check_rate_limit(
        f"video_crop:{current_user.id}",
        limit=12,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="裁剪请求过于频繁，请稍后再试")

    result = await crop_project_video_asset(
        db,
        project_id=project_id,
        video_asset_id=video_asset_id,
        rect_x=body.rect.x,
        rect_y=body.rect.y,
        rect_w=body.rect.w,
        rect_h=body.rect.h,
        title=body.title,
    )
    asset_out = _to_out(result["asset"])
    return ok(
        {
            "asset": asset_out.model_dump(by_alias=True),
            "rect": result["rect"],
            "sourceAssetId": result["sourceAssetId"],
        }
    )


class VideoAvSplitBody(BaseModel):
    """视频节点音视频分离：抽出音频 + 无声视频。"""

    project_id: str = Field(..., alias="projectId")
    video_asset_id: str = Field(..., alias="videoAssetId")
    audio_title: str | None = Field(None, alias="audioTitle")
    video_title: str | None = Field(None, alias="videoTitle")

    model_config = {"populate_by_name": True}


@router.post("/video-av-split")
async def split_video_av_asset(
    body: VideoAvSplitBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """音视频分离：注册分离音频与无声视频两份新素材（原素材不变）。"""
    project_id = (body.project_id or "").strip()
    video_asset_id = (body.video_asset_id or "").strip()
    if not project_id or not video_asset_id:
        fail(ErrorCode.BAD_REQUEST, message="缺少 projectId 或 videoAssetId")

    await require_project_access(db, current_user, project_id)

    allowed = await check_rate_limit(
        f"video_av_split:{current_user.id}",
        limit=8,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="音视频分离请求过于频繁，请稍后再试")

    result = await split_project_video_av(
        db,
        project_id=project_id,
        video_asset_id=video_asset_id,
        audio_title=body.audio_title,
        video_title=body.video_title,
    )
    return ok(
        {
            "audioAsset": _to_out(result["audioAsset"]).model_dump(by_alias=True),
            "videoAsset": _to_out(result["videoAsset"]).model_dump(by_alias=True),
            "sourceAssetId": result["sourceAssetId"],
        }
    )


class VideoFrameEditBody(BaseModel):
    """视频画面编辑本地接口（智能抠像；主体消除已改前端上游路径，本接口仍兼容旧 mode）。"""

    project_id: str = Field(..., alias="projectId")
    video_asset_id: str = Field(..., alias="videoAssetId")
    mode: str = Field(..., description="smart_matting | subject_remove")
    title: str | None = None

    model_config = {"populate_by_name": True}


@router.post("/video-frame-edit")
async def edit_video_frames_asset(
    body: VideoFrameEditBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """本地画面编辑：时长须 ≤N 秒，产出 WebM（抠像带透明通道）。"""
    project_id = (body.project_id or "").strip()
    video_asset_id = (body.video_asset_id or "").strip()
    mode = (body.mode or "").strip()
    if not project_id or not video_asset_id:
        fail(ErrorCode.BAD_REQUEST, message="缺少 projectId 或 videoAssetId")

    await require_project_access(db, current_user, project_id)

    allowed = await check_rate_limit(
        f"video_frame_edit:{current_user.id}",
        limit=4,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="画面编辑请求过于频繁，请稍后再试")

    result = await edit_project_video_frames(
        db,
        project_id=project_id,
        video_asset_id=video_asset_id,
        mode=mode,
        title=body.title,
    )
    return ok(
        {
            "asset": _to_out(result["asset"]).model_dump(by_alias=True),
            "mode": result["mode"],
            "canvasTool": result["canvasTool"],
            "maxDurationSec": result.get("maxDurationSec", MAX_VIDEO_FRAME_EDIT_SEC),
            "sourceAssetId": result["sourceAssetId"],
            "outputFormat": result.get("outputFormat"),
            "hasAlpha": result.get("hasAlpha"),
        }
    )


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(
    asset_id: str,
    projectId: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按素材 ID 获取指定项目中的单个素材详情。"""
    await require_project_access(db, current_user, projectId)
    record = await get_project_asset(db, projectId, asset_id)
    if not record:
        fail(ErrorCode.ASSET_NOT_FOUND)
    return _to_out(record)


@router.post("", response_model=AssetOut, status_code=201)
async def upload_asset(
    file: UploadFile = File(...),
    projectId: str = Form(...),
    category: str = Form(...),
    title: str = Form("未命名"),
    subcategory: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传单个素材文件（图片/视频/音频/文档/模型）到 OSS 并注册唯一素材 ID。"""
    project = await require_project_access(db, user, projectId)
    folder = project_storage_folder(project)
    allowed = await check_rate_limit(
        f"upload:{user.id}",
        limit=UPLOAD_RATE_LIMIT,
        window_s=UPLOAD_RATE_WINDOW_S,
    )
    if not allowed:
        fail(ErrorCode.UPLOAD_RATE_LIMITED)
    if category not in ALLOWED:
        fail(ErrorCode.INVALID_ASSET_CATEGORY)
    if not _is_allowed(category, file.content_type or "", file.filename):
        fail(
            ErrorCode.INVALID_FILE_TYPE,
            message=f"不允许的文件类型: {file.content_type or 'unknown'} ({file.filename or 'no name'})",
        )

    content_type = _resolve_content_type(category, file.content_type or "", file.filename)
    if category == "image":
        max_read = CANVAS_IMAGE_MAX_BYTES + 1
    elif category == "document":
        max_read = CANVAS_DOCUMENT_MAX_BYTES + 1
    else:
        max_read = None
    data = await file.read(max_read) if max_read else await file.read()
    if category == "image" and len(data) > CANVAS_IMAGE_MAX_BYTES:
        fail(ErrorCode.DOWNLOAD_TOO_LARGE, message="图片不能超过 10MB")
    if category == "document":
        _assert_document_upload(data, file.filename)

    thumb_data: bytes | None = None
    if category == "video":
        ext = (file.filename or "mp4").split(".")[-1] or "mp4"
        thumb_data = await asyncio.to_thread(extract_video_thumbnail_jpeg, data, ext)

    total_size = len(data) + (len(thumb_data) if thumb_data else 0)
    await assert_storage_quota_for_project(db, projectId, total_size)

    asset_id = new_asset_id()
    ext = (file.filename or "bin").split(".")[-1] or "bin"
    rel_path = f"assets/{category}/{asset_id}.{ext}"

    storage = get_canvas_storage()
    try:
        stored = await asyncio.to_thread(
            storage.put,
            projectId,
            rel_path,
            data,
            content_type,
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"存储失败: {exc}")

    is_audio = category == "audio"
    is_model = category == "model"
    thumbnail_url = "/uploads/audio-default.svg" if is_audio else stored.file_url
    thumbnail_oss_key = ""
    if is_model:
        thumbnail_url = "/uploads/model-default.svg"

    if category == "video":
        if thumb_data:
            thumb_rel = f"assets/video/{asset_id}.thumb.jpg"
            try:
                thumb_stored = await asyncio.to_thread(
                    storage.put,
                    projectId,
                    thumb_rel,
                    thumb_data,
                    "image/jpeg",
                    storage_folder=folder,
                )
            except StorageWriteError as exc:
                fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"缩略图存储失败: {exc}")
            # 落库用 OSS key，响应 URL 由 insert/_to_out 重签
            thumbnail_url = thumb_stored.file_url
            thumbnail_oss_key = thumb_stored.oss_key

    record = {
        "id": asset_id,
        "projectId": projectId,
        "title": title.strip() or "未命名",
        "category": category,
        "subcategory": subcategory,
        "fileUrl": stored.file_url,
        "thumbnailUrl": thumbnail_url,
        "fileType": content_type,
        "fileSize": total_size,
        "ossKey": stored.oss_key,
        "source": "upload",
        "createdAt": cst_iso_now(),
    }
    if thumbnail_oss_key:
        record["thumbnailOssKey"] = thumbnail_oss_key

    saved = await insert_project_asset(db, projectId, record, sync_oss=False)
    await invalidate_manifest_cache(projectId)
    return _to_out(saved)


@router.delete("/{asset_id}")
async def delete_asset(
    asset_id: str,
    projectId: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除指定项目中的素材记录并清理其 OSS 对象。"""
    await require_project_access(db, current_user, projectId)
    asset = await delete_project_asset(db, projectId, asset_id)
    if not asset:
        fail(ErrorCode.ASSET_NOT_FOUND)
    oss_key = asset.get("ossKey")
    if oss_key:
        await asyncio.to_thread(delete_oss_object, oss_key)
    await invalidate_manifest_cache(projectId)
    return {"ok": True}
