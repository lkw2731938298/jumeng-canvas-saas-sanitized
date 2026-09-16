"""工作流发布业务：发布、列表、点赞、只读预览、全量复制。"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from typing import Any, Literal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..core.entity_ids import require_entity_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.oss.canvas_storage import (
    StorageWriteError,
    get_canvas_storage,
    new_asset_id,
    resolve_flow_json,
    store_flow_json,
)
from ..models.project import Project, Workflow
from ..models.user import User
from ..models.workflow_publication import (
    WorkflowPublication,
    WorkflowPublicationLike,
    WorkflowPublicationUse,
)
from .asset_copy import (
    collect_asset_ids_from_json,
    copy_assets_batch,
    remap_asset_ids_in_json,
)
from .asset_store import get_project_asset, insert_project_asset
from .cache import invalidate_manifest_cache
from .project_create_limits import assert_project_create_allowed
from .project_scope import new_storage_folder, project_storage_folder
from .storage_quota import assert_storage_quota_for_project
from .storage_urls import normalize_browser_storage_url, public_url_for_key

logger = logging.getLogger(__name__)

PUBLICATION_CATEGORIES: tuple[str, ...] = (
    "短剧漫剧",
    "电商带货",
    "IP口播",
    "AI获客",
    "达人探店",
    "AI信息流",
    "商业广告",
    "动漫游戏",
    "教育生活",
)


async def _allowed_publication_categories(db: AsyncSession) -> list[str]:
    """发布/列表分类权威：发现页 galleryFilters；失败时回退常量。"""
    try:
        from .discover_page import get_publication_category_options

        opts = await get_publication_category_options(db)
        if opts:
            return opts
    except Exception:
        logger.exception("load publication categories from discover settings failed")
    return list(PUBLICATION_CATEGORIES)

ScopeKey = Literal["public", "mine", "used"]


def _gen_project_no() -> str:
    import random
    import time

    ts = int(time.time() * 1000) % 100000000
    suffix = random.randint(0, 99)
    return f"PRJ-{ts:08d}{suffix:02d}"


def _video_mime(ext: str) -> str:
    mapping = {
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mov": "video/quicktime",
        "m4v": "video/x-m4v",
    }
    return mapping.get(ext.lower(), "video/mp4")


async def _load_owned_project(
    db: AsyncSession, user: User, project_id: str
) -> Project:
    pid = require_entity_id(project_id, message="项目 ID 无效")
    result = await db.execute(
        select(Project).filter(
            Project.id == pid,
            Project.owner_id == user.id,
            Project.isdel.is_(False),
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)
    return project


async def _get_publication_or_fail(
    db: AsyncSession, publication_id: str
) -> WorkflowPublication:
    pub_id = require_entity_id(publication_id, message="发布 ID 无效")
    result = await db.execute(
        select(WorkflowPublication).filter(WorkflowPublication.id == pub_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        fail(ErrorCode.PUBLICATION_NOT_FOUND)
    return row


def _assert_can_view(
    pub: WorkflowPublication, viewer: User | None
) -> None:
    """审核通过的公开条目，或作者本人可查看；否则 403。"""
    if viewer is not None and int(viewer.id) == int(pub.author_id):
        return
    rs = str(getattr(pub, "review_status", None) or "approved")
    if pub.is_public and rs == "approved":
        return
    fail(ErrorCode.PUBLICATION_NOT_PUBLIC)


async def _copy_preview_video_to_platform(
    *,
    publication_id: int,
    source_oss_key: str,
    content_type: str,
) -> str:
    """把源视频拷到平台路径，避免源资产删除后广场失效。"""
    storage = get_canvas_storage()
    try:
        data = await asyncio.to_thread(storage.get_bytes, source_oss_key)
    except Exception as exc:
        fail(ErrorCode.INTERNAL_ERROR, message=f"读取发布视频失败：{exc}")
    if not data:
        fail(ErrorCode.PUBLICATION_VIDEO_REQUIRED, message="发布视频文件为空")

    _, ext = os.path.splitext(source_oss_key)
    ext = (ext or ".mp4").lstrip(".") or "mp4"
    rel = f"workflow-publications/{publication_id}/preview.{ext}"
    try:
        stored = await asyncio.to_thread(
            storage.put_platform,
            rel,
            data,
            content_type or _video_mime(ext),
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"发布视频存储失败：{exc}")
    return stored.oss_key


def _image_mime(ext: str) -> str:
    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }
    return mapping.get(ext.lower(), "image/jpeg")


# 作品广场封面：长边上限与目标体积（落平台 OSS，列表/详情共用）
_PUBLICATION_COVER_MAX_EDGE = 1280
_PUBLICATION_COVER_MAX_BYTES = 300 * 1024


def _compress_publication_cover(data: bytes) -> tuple[bytes, str, str]:
    """将发布封面压成 WebP（长边≤1280，尽量 <300KB）；失败则抛错由调用方回退原图。"""
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        if getattr(img, "is_animated", False):
            img.seek(0)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in (img.mode or "") else "RGB")
        w, h = img.size
        max_edge = max(w, h)
        if max_edge > _PUBLICATION_COVER_MAX_EDGE:
            scale = _PUBLICATION_COVER_MAX_EDGE / float(max_edge)
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        # 广场封面按不透明图处理：透明铺深色底后压 WebP
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (14, 11, 18))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        compressed: bytes | None = None
        for quality in (78, 68, 58, 48):
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=quality, method=4)
            candidate = out.getvalue()
            if not candidate:
                continue
            compressed = candidate
            if len(candidate) <= _PUBLICATION_COVER_MAX_BYTES:
                break
        if not compressed:
            raise ValueError("压缩结果为空")
    return compressed, "image/webp", "webp"


async def _copy_preview_cover_to_platform(
    *,
    publication_id: int,
    source_oss_key: str,
    content_type: str,
) -> str:
    """把封面图拷到平台路径并压缩，供作品广场展示。"""
    storage = get_canvas_storage()
    try:
        data = await asyncio.to_thread(storage.get_bytes, source_oss_key)
    except Exception as exc:
        fail(ErrorCode.INTERNAL_ERROR, message=f"读取发布封面失败：{exc}")
    if not data:
        fail(ErrorCode.PUBLICATION_COVER_REQUIRED, message="发布封面文件为空")

    store_bytes = data
    store_mime = content_type or "image/jpeg"
    _, ext = os.path.splitext(source_oss_key)
    store_ext = (ext or ".jpg").lstrip(".") or "jpg"
    # 已很小则跳过重编码；否则压 WebP，失败回退原图
    if len(data) > 80 * 1024:
        try:
            store_bytes, store_mime, store_ext = await asyncio.to_thread(
                _compress_publication_cover, data
            )
            logger.info(
                "publication cover compress ok pub=%s in=%s out=%s",
                publication_id,
                len(data),
                len(store_bytes),
            )
        except Exception as exc:
            logger.warning(
                "publication cover compress failed pub=%s, store original: %s",
                publication_id,
                exc,
            )

    rel = f"workflow-publications/{publication_id}/cover.{store_ext}"
    try:
        stored = await asyncio.to_thread(
            storage.put_platform,
            rel,
            store_bytes,
            store_mime or _image_mime(store_ext),
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"发布封面存储失败：{exc}")
    return stored.oss_key


def _pub_snapshot_rel(publication_id: int, *parts: str) -> str:
    """发布快照相对路径：workflow-publications/{id}/..."""
    return "/".join(["workflow-publications", str(publication_id), *parts])


async def _freeze_publication_snapshot(
    db: AsyncSession,
    *,
    pub: WorkflowPublication,
    source: Project,
) -> str:
    """冻结源项目当前工作流到平台 OSS（flow + 素材 + 节点文本）。

    他人预览/复制只读此快照；作者后续改源项目不会影响已发布内容，
    须再次「发布/更新」才会刷新快照。
    """
    publication_id = int(pub.id)
    source_pid = str(source.id)
    source_folder = project_storage_folder(source)
    storage = get_canvas_storage()

    wf = await _latest_workflow(db, int(source.id))
    flow_data: dict[str, Any] = {"nodes": [], "edges": []}
    wf_title = pub.title or "未命名工作流"
    wf_description = pub.description or ""
    wf_version = "1"
    if wf:
        raw = await asyncio.to_thread(resolve_flow_json, wf.flow_json, storage)
        try:
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                flow_data = parsed
        except json.JSONDecodeError:
            flow_data = {"nodes": [], "edges": []}
        wf_title = wf.title or wf_title
        wf_description = wf.description or wf_description
        wf_version = wf.version or "1"

    # 素材：按发布时 assetId 写入平台路径，flow 内引用保持不变
    asset_ids = collect_asset_ids_from_json(flow_data)
    assets_manifest: list[dict[str, Any]] = []
    for aid in asset_ids:
        src = await get_project_asset(db, source_pid, aid, sync_oss=False)
        if not src:
            src = await get_project_asset(db, source_pid, aid, sync_oss=True)
        if not src:
            logger.warning(
                "publication snapshot skip missing asset pub=%s asset=%s",
                publication_id,
                aid,
            )
            continue
        oss_key = str(src.get("ossKey") or "").strip()
        if not oss_key:
            continue
        try:
            data = await asyncio.to_thread(storage.get_bytes, oss_key)
        except Exception as exc:
            fail(
                ErrorCode.INTERNAL_ERROR,
                message=f"冻结发布素材失败（assetId={aid}）：{exc}",
            )
        if not data:
            continue
        category = str(src.get("category") or "image")
        _, ext = os.path.splitext(oss_key)
        ext = (ext or ".bin").lstrip(".") or "bin"
        rel = _pub_snapshot_rel(publication_id, "assets", category, f"{aid}.{ext}")
        try:
            stored = await asyncio.to_thread(
                storage.put_platform,
                rel,
                data,
                str(src.get("fileType") or "application/octet-stream"),
            )
        except StorageWriteError as exc:
            fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"冻结发布素材失败：{exc}")

        thumb_oss_key = ""
        thumb_guess = oss_key.rsplit(".", 1)[0] + ".thumb.jpg"
        try:
            if await asyncio.to_thread(storage.object_exists, thumb_guess):
                thumb_bytes = await asyncio.to_thread(storage.get_bytes, thumb_guess)
                if thumb_bytes:
                    thumb_rel = _pub_snapshot_rel(
                        publication_id, "assets", category, f"{aid}.thumb.jpg"
                    )
                    thumb_obj = await asyncio.to_thread(
                        storage.put_platform, thumb_rel, thumb_bytes, "image/jpeg"
                    )
                    thumb_oss_key = thumb_obj.oss_key
        except Exception:
            thumb_oss_key = ""

        assets_manifest.append(
            {
                "id": aid,
                "category": category,
                "subcategory": src.get("subcategory"),
                "title": src.get("title") or "未命名",
                "fileType": src.get("fileType") or "application/octet-stream",
                "fileSize": len(data),
                "ossKey": stored.oss_key,
                "thumbOssKey": thumb_oss_key,
            }
        )

    index_rel = _pub_snapshot_rel(publication_id, "assets", "index.json")
    try:
        await asyncio.to_thread(
            storage.put_platform,
            index_rel,
            json.dumps(assets_manifest, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"冻结素材索引失败：{exc}")

    # 节点文本
    for node_id in _node_ids_from_flow(flow_data):
        for name, ctype in (
            (f"{node_id}.txt", "text/plain; charset=utf-8"),
            (f"{node_id}.meta.json", "application/json; charset=utf-8"),
        ):
            try:
                src_key = storage.project_key(
                    source_pid, f"text/{name}", storage_folder=source_folder
                )
                if not await asyncio.to_thread(storage.object_exists, src_key):
                    continue
                text_data = await asyncio.to_thread(storage.get_bytes, src_key)
                if not text_data:
                    continue
                await asyncio.to_thread(
                    storage.put_platform,
                    _pub_snapshot_rel(publication_id, "text", name),
                    text_data,
                    ctype,
                )
            except Exception as exc:
                logger.warning(
                    "freeze node text failed pub=%s node=%s: %s",
                    publication_id,
                    node_id,
                    exc,
                )

    meta = {
        "title": wf_title,
        "description": wf_description,
        "version": wf_version,
        "frozenAt": to_cst_iso(now_cst_naive()) or "",
        "sourceProjectId": source_pid,
    }
    meta_rel = _pub_snapshot_rel(publication_id, "meta.json")
    try:
        await asyncio.to_thread(
            storage.put_platform,
            meta_rel,
            json.dumps(meta, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"冻结工作流元数据失败：{exc}")

    flow_rel = _pub_snapshot_rel(publication_id, "flow.json")
    try:
        stored_flow = await asyncio.to_thread(
            storage.put_platform,
            flow_rel,
            json.dumps(flow_data, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"冻结工作流失败：{exc}")
    return stored_flow.oss_key


async def _load_publication_snapshot(
    pub: WorkflowPublication,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]] | None:
    """读取发布快照；无 flow_oss_key 或文件缺失时返回 None。"""
    if not (pub.flow_oss_key or "").strip():
        return None
    storage = get_canvas_storage()
    publication_id = int(pub.id)
    try:
        raw = await asyncio.to_thread(storage.get_bytes, pub.flow_oss_key)
    except Exception as exc:
        logger.warning("load publication flow failed pub=%s: %s", publication_id, exc)
        return None
    if not raw:
        return None
    try:
        flow_data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(flow_data, dict):
        flow_data = {}

    assets_manifest: list[dict[str, Any]] = []
    index_key = storage.platform_key(
        _pub_snapshot_rel(publication_id, "assets", "index.json")
    )
    try:
        index_raw = await asyncio.to_thread(storage.get_bytes, index_key)
        if index_raw:
            parsed = json.loads(
                index_raw.decode("utf-8")
                if isinstance(index_raw, (bytes, bytearray))
                else index_raw
            )
            if isinstance(parsed, list):
                assets_manifest = [x for x in parsed if isinstance(x, dict)]
    except Exception as exc:
        logger.warning("load publication assets index failed pub=%s: %s", publication_id, exc)

    meta: dict[str, Any] = {}
    meta_key = storage.platform_key(_pub_snapshot_rel(publication_id, "meta.json"))
    try:
        meta_raw = await asyncio.to_thread(storage.get_bytes, meta_key)
        if meta_raw:
            parsed_meta = json.loads(
                meta_raw.decode("utf-8")
                if isinstance(meta_raw, (bytes, bytearray))
                else meta_raw
            )
            if isinstance(parsed_meta, dict):
                meta = parsed_meta
    except Exception:
        meta = {}

    return flow_data, assets_manifest, meta


async def _copy_snapshot_assets_to_project(
    db: AsyncSession,
    *,
    assets_manifest: list[dict[str, Any]],
    target_project_id: str,
    target_folder: str | None,
) -> dict[str, str]:
    """从发布快照素材拷到目标项目，返回旧 assetId → 新 id。"""
    storage = get_canvas_storage()
    id_map: dict[str, str] = {}
    for item in assets_manifest:
        old_id = str(item.get("id") or "").strip()
        oss_key = str(item.get("ossKey") or "").strip()
        if not old_id or not oss_key or old_id in id_map:
            continue
        try:
            data = await asyncio.to_thread(storage.get_bytes, oss_key)
        except Exception as exc:
            fail(
                ErrorCode.INTERNAL_ERROR,
                message=f"读取发布快照素材失败（assetId={old_id}）：{exc}",
            )
        if not data:
            fail(
                ErrorCode.BAD_REQUEST,
                message=f"发布快照素材为空（assetId={old_id}）",
            )
        await assert_storage_quota_for_project(db, target_project_id, len(data))
        category = str(item.get("category") or "image")
        _, ext = os.path.splitext(oss_key)
        ext = (ext or ".bin").lstrip(".") or "bin"
        new_uuid = new_asset_id()
        rel = f"assets/{category}/{new_uuid}.{ext}"
        try:
            stored = await asyncio.to_thread(
                storage.put,
                target_project_id,
                rel,
                data,
                str(item.get("fileType") or "application/octet-stream"),
                storage_folder=target_folder,
            )
        except StorageWriteError as exc:
            fail(
                ErrorCode.INTERNAL_ERROR,
                message=f"写入目标项目素材失败（assetId={old_id}）：{exc}",
            )

        thumb_url = None
        thumb_key = str(item.get("thumbOssKey") or "").strip()
        if thumb_key:
            try:
                thumb_bytes = await asyncio.to_thread(storage.get_bytes, thumb_key)
                if thumb_bytes:
                    thumb_rel = f"assets/{category}/{new_uuid}.thumb.jpg"
                    thumb_obj = await asyncio.to_thread(
                        storage.put,
                        target_project_id,
                        thumb_rel,
                        thumb_bytes,
                        "image/jpeg",
                        storage_folder=target_folder,
                    )
                    thumb_url = thumb_obj.file_url
            except Exception:
                thumb_url = None

        record = {
            "title": item.get("title") or "未命名",
            "category": category,
            "subcategory": item.get("subcategory"),
            "fileType": item.get("fileType") or "application/octet-stream",
            "fileSize": len(data),
            "ossKey": stored.oss_key,
            "thumbnailUrl": thumb_url,
            "source": "publication_copy",
        }
        inserted = await insert_project_asset(db, target_project_id, record, sync_oss=False)
        new_id = str(inserted.get("id") or "").strip()
        if not new_id:
            fail(ErrorCode.INTERNAL_ERROR, message=f"目标素材入库失败（源 assetId={old_id}）")
        id_map[old_id] = new_id

    await invalidate_manifest_cache(target_project_id)
    return id_map


async def _copy_snapshot_node_texts(
    *,
    publication_id: int,
    target_project_id: str,
    target_folder: str | None,
    node_ids: list[str],
) -> None:
    """从发布快照拷贝节点文本到目标项目。"""
    storage = get_canvas_storage()
    for node_id in node_ids:
        for name, ctype in (
            (f"{node_id}.txt", "text/plain; charset=utf-8"),
            (f"{node_id}.meta.json", "application/json; charset=utf-8"),
        ):
            try:
                src_key = storage.platform_key(
                    _pub_snapshot_rel(publication_id, "text", name)
                )
                if not await asyncio.to_thread(storage.object_exists, src_key):
                    continue
                data = await asyncio.to_thread(storage.get_bytes, src_key)
                if not data:
                    continue
                await asyncio.to_thread(
                    storage.put,
                    target_project_id,
                    f"text/{name}",
                    data,
                    ctype,
                    storage_folder=target_folder,
                )
            except Exception as exc:
                logger.warning(
                    "copy snapshot node text failed pub=%s node=%s: %s",
                    publication_id,
                    node_id,
                    exc,
                )


async def _liked_set_for_user(
    db: AsyncSession, user_id: int | None, publication_ids: list[int]
) -> set[int]:
    if not user_id or not publication_ids:
        return set()
    result = await db.execute(
        select(WorkflowPublicationLike.publication_id).filter(
            WorkflowPublicationLike.user_id == user_id,
            WorkflowPublicationLike.publication_id.in_(publication_ids),
        )
    )
    return {int(r) for r in result.scalars().all()}


async def _author_map(
    db: AsyncSession, author_ids: list[int]
) -> dict[int, User]:
    if not author_ids:
        return {}
    result = await db.execute(select(User).filter(User.id.in_(author_ids)))
    return {int(u.id): u for u in result.scalars().all()}


def publication_display_status(pub: WorkflowPublication) -> str:
    """合成展示态：listed | unlisted | pending | rejected。"""
    rs = str(getattr(pub, "review_status", None) or "approved")
    if rs == "pending":
        return "pending"
    if rs == "rejected":
        return "rejected"
    if bool(pub.is_public) and rs == "approved":
        return "listed"
    if rs == "approved" and not bool(pub.is_public):
        return "unlisted"
    return "rejected"


def _normalize_pub_review_history(raw: object | None) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "action": str(item.get("action") or ""),
                "note": str(item.get("note") or "") or None,
                "at": str(item.get("at") or "") or None,
                "by": str(item.get("by") or "") or None,
                "byName": str(item.get("byName") or "") or None,
            }
        )
    return out


def _append_pub_review_history(
    pub: WorkflowPublication,
    *,
    action: str,
    note: str | None,
    at,
    admin: User,
) -> None:
    """追加一条审核记录（最多保留 50 条）。"""
    hist = _normalize_pub_review_history(getattr(pub, "review_history", None))
    hist.append(
        {
            "action": action,
            "note": note,
            "at": to_cst_iso(at),
            "by": str(admin.id),
            "byName": (admin.display_name or "").strip() or str(admin.id),
        }
    )
    pub.review_history = hist[-50:]


def publication_to_dict(
    pub: WorkflowPublication,
    *,
    author: User | None = None,
    liked: bool = False,
    reviewer: User | None = None,
) -> dict[str, Any]:
    video_url = public_url_for_key(pub.video_oss_key) if pub.video_oss_key else ""
    cover_key = str(getattr(pub, "cover_oss_key", None) or "").strip()
    cover_url = public_url_for_key(cover_key) if cover_key else ""
    reviewed_by = getattr(pub, "reviewed_by", None)
    return {
        "id": str(pub.id),
        "sourceProjectId": str(pub.source_project_id),
        "title": pub.title or "",
        "description": pub.description or "",
        "category": pub.category or "",
        "isPublic": bool(pub.is_public),
        "reviewStatus": str(getattr(pub, "review_status", None) or "approved"),
        "reviewNote": str(getattr(pub, "review_note", None) or "") or None,
        "reviewedAt": to_cst_iso(getattr(pub, "reviewed_at", None))
        if getattr(pub, "reviewed_at", None)
        else None,
        "reviewedBy": str(reviewed_by) if reviewed_by is not None else None,
        "reviewedByName": (
            ((reviewer.display_name or "").strip() or str(reviewer.id)) if reviewer else None
        ),
        "reviewHistory": _normalize_pub_review_history(getattr(pub, "review_history", None)),
        "displayStatus": publication_display_status(pub),
        "videoUrl": video_url,
        "videoAssetId": pub.video_asset_id or "",
        "coverUrl": cover_url,
        "coverAssetId": str(getattr(pub, "cover_asset_id", None) or ""),
        "likeCount": int(pub.like_count or 0),
        "useCount": int(pub.use_count or 0),
        "liked": bool(liked),
        "authorId": str(pub.author_id),
        "authorName": (author.display_name if author else "") or "创作者",
        "authorAvatar": (
            normalize_browser_storage_url(author.avatar_url)
            if author and author.avatar_url
            else ""
        ),
        "publishedAt": to_cst_iso(pub.published_at) or "",
        "updatedAt": to_cst_iso(pub.updated_at) or "",
    }


async def upsert_publication(
    db: AsyncSession,
    user: User,
    *,
    project_id: str,
    title: str,
    description: str,
    category: str,
    video_asset_id: str,
    cover_asset_id: str,
    is_public: bool,
) -> dict[str, Any]:
    """发布或更新同一源项目的发布条目。"""
    title_n = (title or "").strip()
    desc_n = (description or "").strip()
    cat_n = (category or "").strip()
    video_id = (video_asset_id or "").strip()
    cover_id = (cover_asset_id or "").strip()

    if not project_id or not str(project_id).strip():
        fail(ErrorCode.PUBLICATION_PROJECT_REQUIRED)
    if not title_n or len(title_n) > 20:
        fail(ErrorCode.BAD_REQUEST, message="作品名称须为 1–20 个字符")
    if not desc_n or len(desc_n) > 100:
        fail(ErrorCode.BAD_REQUEST, message="作品描述须为 1–100 个字符")
    allowed_cats = await _allowed_publication_categories(db)
    if cat_n not in allowed_cats:
        fail(ErrorCode.PUBLICATION_CATEGORY_INVALID)
    if not video_id:
        fail(ErrorCode.PUBLICATION_VIDEO_REQUIRED)
    if not cover_id:
        fail(ErrorCode.PUBLICATION_COVER_REQUIRED)

    project = await _load_owned_project(db, user, project_id)
    asset = await get_project_asset(db, str(project.id), video_id, sync_oss=True)
    if not asset:
        fail(ErrorCode.ASSET_NOT_FOUND)
    if str(asset.get("category") or "").lower() != "video":
        fail(ErrorCode.PUBLICATION_VIDEO_REQUIRED, message="只能选择视频类素材")
    source_oss = str(asset.get("ossKey") or "").strip()
    if not source_oss:
        fail(ErrorCode.PUBLICATION_VIDEO_REQUIRED)

    cover_asset = await get_project_asset(db, str(project.id), cover_id, sync_oss=True)
    if not cover_asset:
        fail(ErrorCode.ASSET_NOT_FOUND, message="封面素材不存在")
    if str(cover_asset.get("category") or "").lower() != "image":
        fail(ErrorCode.PUBLICATION_COVER_REQUIRED, message="封面须为图片类素材")
    cover_oss = str(cover_asset.get("ossKey") or "").strip()
    if not cover_oss:
        fail(ErrorCode.PUBLICATION_COVER_REQUIRED)

    pid = int(project.id)
    existing = await db.execute(
        select(WorkflowPublication).filter(WorkflowPublication.source_project_id == pid)
    )
    pub = existing.scalar_one_or_none()
    now = now_cst_naive()

    # 申请公开 → 进入审核；未公开则直接 approved（仅「我的发布」可见）
    want_public = bool(is_public)
    review_status = "pending" if want_public else "approved"

    if pub is None:
        pub = WorkflowPublication(
            author_id=int(user.id),
            source_project_id=pid,
            title=title_n,
            description=desc_n,
            category=cat_n,
            video_asset_id=video_id,
            video_oss_key="",
            cover_asset_id=cover_id,
            cover_oss_key="",
            is_public=want_public,
            review_status=review_status,
            like_count=0,
            use_count=0,
            published_at=now,
            updated_at=now,
        )
        db.add(pub)
        await db.flush()
    else:
        if int(pub.author_id) != int(user.id):
            fail(ErrorCode.FORBIDDEN)
        pub.title = title_n
        pub.description = desc_n
        pub.category = cat_n
        pub.video_asset_id = video_id
        pub.cover_asset_id = cover_id
        pub.is_public = want_public
        # 申请/更新公开内容 → 重新进审，避免未审内容继续展示
        if want_public:
            pub.review_status = "pending"
            pub.review_note = None
            pub.reviewed_at = None
            pub.reviewed_by = None
        else:
            pub.review_status = "approved"
            pub.review_note = None
        pub.updated_at = now

    content_type = str(asset.get("fileType") or "video/mp4")
    pub.video_oss_key = await _copy_preview_video_to_platform(
        publication_id=int(pub.id),
        source_oss_key=source_oss,
        content_type=content_type,
    )
    cover_type = str(cover_asset.get("fileType") or "image/jpeg")
    pub.cover_oss_key = await _copy_preview_cover_to_platform(
        publication_id=int(pub.id),
        source_oss_key=cover_oss,
        content_type=cover_type,
    )

    # 冻结工作流快照：他人复制/预览只读本次发布内容，不跟随源项目后续改动
    pub.flow_oss_key = await _freeze_publication_snapshot(db, pub=pub, source=project)

    # 项目公开标记仅在审核通过后打开；待审时保持私有
    project.is_public = bool(want_public and str(pub.review_status) == "approved")
    await db.flush()

    return publication_to_dict(pub, author=user, liked=False)


async def delete_my_publication(
    db: AsyncSession,
    user: User,
    publication_id: str,
) -> dict[str, Any]:
    """作者删除自己的发布条目（硬删 DB；平台预览对象尽力清理；可再发同项目）。"""
    pub = await _get_publication_or_fail(db, publication_id)
    if int(pub.author_id) != int(user.id):
        fail(ErrorCode.FORBIDDEN, message="只能删除自己的发布")

    pub_id = int(pub.id)
    source_pid = int(pub.source_project_id)
    # 平台侧预览/快照 key，删除后尽力清 OSS（失败不影响 DB 删除）
    oss_keys = [
        str(k).strip()
        for k in (
            getattr(pub, "video_oss_key", None),
            getattr(pub, "cover_oss_key", None),
            getattr(pub, "flow_oss_key", None),
        )
        if k and str(k).strip()
    ]

    await db.execute(
        delete(WorkflowPublicationLike).where(
            WorkflowPublicationLike.publication_id == pub_id
        )
    )
    await db.execute(
        delete(WorkflowPublicationUse).where(
            WorkflowPublicationUse.publication_id == pub_id
        )
    )

    proj = (
        await db.execute(
            select(Project).filter(Project.id == source_pid).limit(1)
        )
    ).scalar_one_or_none()
    if proj is not None and bool(getattr(proj, "is_public", False)):
        # 发布条目已删，源项目不再保持广场公开
        proj.is_public = False

    await db.delete(pub)
    await db.flush()

    if oss_keys:
        try:
            storage = get_canvas_storage()
            for key in oss_keys:
                try:
                    storage.delete(key)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "delete publication oss key failed pub=%s key=%s err=%s",
                        pub_id,
                        key,
                        exc,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "delete publication oss storage unavailable pub=%s err=%s",
                pub_id,
                exc,
            )

    return {"id": str(pub_id), "deleted": True}


async def list_publications(
    db: AsyncSession,
    viewer: User | None,
    *,
    scope: ScopeKey,
    category: str | None = None,
    sort: str = "latest",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """按 scope 分页列出发布条目；sort=latest|hot（最热按 like_count）。"""
    page = max(1, int(page or 1))
    page_size = min(50, max(1, int(page_size or 20)))
    cat = (category or "").strip()
    if cat in ("", "全部"):
        cat = ""
    # 非法 sort 回退最新，避免前端传错导致 500
    sort_key = (sort or "latest").strip().lower()
    if sort_key not in ("latest", "hot"):
        sort_key = "latest"

    if scope == "mine":
        if not viewer:
            fail(ErrorCode.UNAUTHORIZED)
        base = select(WorkflowPublication).filter(
            WorkflowPublication.author_id == int(viewer.id)
        )
        count_q = (
            select(func.count())
            .select_from(WorkflowPublication)
            .filter(WorkflowPublication.author_id == int(viewer.id))
        )
        if cat:
            base = base.filter(WorkflowPublication.category == cat)
            count_q = count_q.filter(WorkflowPublication.category == cat)
        if sort_key == "hot":
            base = base.order_by(
                WorkflowPublication.like_count.desc(),
                WorkflowPublication.updated_at.desc(),
            )
        else:
            base = base.order_by(WorkflowPublication.updated_at.desc())
    elif scope == "used":
        if not viewer:
            fail(ErrorCode.UNAUTHORIZED)
        # 取用户复制过的发布（按最近使用）
        used_sub = (
            select(
                WorkflowPublicationUse.publication_id,
                func.max(WorkflowPublicationUse.created_at).label("last_used"),
            )
            .filter(WorkflowPublicationUse.user_id == int(viewer.id))
            .group_by(WorkflowPublicationUse.publication_id)
            .subquery()
        )
        base = select(WorkflowPublication).join(
            used_sub, used_sub.c.publication_id == WorkflowPublication.id
        )
        count_q = (
            select(func.count())
            .select_from(WorkflowPublication)
            .join(used_sub, used_sub.c.publication_id == WorkflowPublication.id)
        )
        if cat:
            base = base.filter(WorkflowPublication.category == cat)
            count_q = count_q.filter(WorkflowPublication.category == cat)
        if sort_key == "hot":
            base = base.order_by(
                WorkflowPublication.like_count.desc(),
                used_sub.c.last_used.desc(),
            )
        else:
            base = base.order_by(used_sub.c.last_used.desc())
    else:
        # public：广场仅展示审核通过的公开条目
        base = select(WorkflowPublication).filter(
            WorkflowPublication.is_public.is_(True),
            WorkflowPublication.review_status == "approved",
        )
        count_q = (
            select(func.count())
            .select_from(WorkflowPublication)
            .filter(
                WorkflowPublication.is_public.is_(True),
                WorkflowPublication.review_status == "approved",
            )
        )
        if cat:
            base = base.filter(WorkflowPublication.category == cat)
            count_q = count_q.filter(WorkflowPublication.category == cat)
        if sort_key == "hot":
            # 最热：点赞多优先，同赞按发布时间
            base = base.order_by(
                WorkflowPublication.like_count.desc(),
                WorkflowPublication.published_at.desc(),
            )
        else:
            base = base.order_by(WorkflowPublication.published_at.desc())

    total = int((await db.execute(count_q)).scalar_one() or 0)
    offset = (page - 1) * page_size
    result = await db.execute(base.offset(offset).limit(page_size))
    rows = list(result.scalars().all())

    author_ids = list({int(r.author_id) for r in rows})
    authors = await _author_map(db, author_ids)
    liked = await _liked_set_for_user(
        db,
        int(viewer.id) if viewer else None,
        [int(r.id) for r in rows],
    )

    items = [
        publication_to_dict(
            r,
            author=authors.get(int(r.author_id)),
            liked=int(r.id) in liked,
        )
        for r in rows
    ]
    # 分类 Tab 与后台「作品广场分类」同步，不再写死常量
    categories = await _allowed_publication_categories(db)
    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "categories": categories,
    }


async def get_publication_detail(
    db: AsyncSession,
    viewer: User | None,
    publication_id: str,
) -> dict[str, Any]:
    pub = await _get_publication_or_fail(db, publication_id)
    _assert_can_view(pub, viewer)
    authors = await _author_map(db, [int(pub.author_id)])
    liked = False
    if viewer:
        liked_ids = await _liked_set_for_user(db, int(viewer.id), [int(pub.id)])
        liked = int(pub.id) in liked_ids
    return publication_to_dict(
        pub, author=authors.get(int(pub.author_id)), liked=liked
    )


async def toggle_publication_like(
    db: AsyncSession,
    user: User,
    publication_id: str,
) -> dict[str, Any]:
    """点赞切换：再点取消。"""
    pub = await _get_publication_or_fail(db, publication_id)
    _assert_can_view(pub, user)

    result = await db.execute(
        select(WorkflowPublicationLike).filter(
            WorkflowPublicationLike.publication_id == int(pub.id),
            WorkflowPublicationLike.user_id == int(user.id),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)
        pub.like_count = max(0, int(pub.like_count or 0) - 1)
        liked = False
    else:
        db.add(
            WorkflowPublicationLike(
                publication_id=int(pub.id),
                user_id=int(user.id),
                created_at=now_cst_naive(),
            )
        )
        pub.like_count = int(pub.like_count or 0) + 1
        liked = True
    await db.flush()
    return {"liked": liked, "likeCount": int(pub.like_count or 0)}


async def _latest_workflow(
    db: AsyncSession, project_id: int
) -> Workflow | None:
    result = await db.execute(
        select(Workflow)
        .filter(Workflow.project_id == project_id)
        .order_by(Workflow.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _node_ids_from_flow(flow_data: dict[str, Any]) -> list[str]:
    nodes = flow_data.get("nodes")
    if not isinstance(nodes, list):
        return []
    ids: list[str] = []
    seen: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        ids.append(nid)
    return ids


async def get_publication_preview(
    db: AsyncSession,
    viewer: User | None,
    publication_id: str,
) -> dict[str, Any]:
    """只读预览：优先读发布时冻结快照；无快照时回退源项目（兼容旧数据）。"""
    pub = await _get_publication_or_fail(db, publication_id)
    _assert_can_view(pub, viewer)

    source_pid = str(pub.source_project_id)
    snapshot = await _load_publication_snapshot(pub)
    if snapshot is not None:
        flow_data, assets_manifest, _meta = snapshot
        assets_out: list[dict[str, Any]] = []
        for item in assets_manifest:
            oss_key = str(item.get("ossKey") or "")
            assets_out.append(
                {
                    "id": str(item.get("id") or ""),
                    "legacyId": None,
                    "category": item.get("category") or "image",
                    "title": item.get("title") or "",
                    "fileUrl": public_url_for_key(oss_key) if oss_key else "",
                    "thumbnailUrl": public_url_for_key(
                        str(item.get("thumbOssKey") or oss_key)
                    )
                    if (item.get("thumbOssKey") or oss_key)
                    else "",
                    "fileType": item.get("fileType") or "",
                }
            )
        return {
            "publicationId": str(pub.id),
            "title": pub.title,
            "sourceProjectId": source_pid,
            "flowJson": flow_data,
            "assets": assets_out,
            "nodeIds": _node_ids_from_flow(flow_data),
            "fromSnapshot": True,
        }

    # 兼容：旧发布无快照时仍读源项目最新（建议作者重新发布一次以冻结）
    project_result = await db.execute(
        select(Project).filter(
            Project.id == int(pub.source_project_id),
            Project.isdel.is_(False),
        )
    )
    project = project_result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    wf = await _latest_workflow(db, int(project.id))
    if not wf:
        return {
            "publicationId": str(pub.id),
            "title": pub.title,
            "flowJson": {"nodes": [], "edges": []},
            "assets": [],
            "fromSnapshot": False,
        }

    storage = get_canvas_storage()
    raw = await asyncio.to_thread(resolve_flow_json, wf.flow_json, storage)
    try:
        flow_data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        flow_data = {}
    if not isinstance(flow_data, dict):
        flow_data = {}

    asset_ids = collect_asset_ids_from_json(flow_data)
    assets_out = []
    for aid in asset_ids:
        item = await get_project_asset(db, source_pid, aid, sync_oss=False)
        if not item:
            continue
        oss_key = str(item.get("ossKey") or "")
        assets_out.append(
            {
                "id": str(item.get("id") or aid),
                "legacyId": item.get("legacyId"),
                "category": item.get("category") or "image",
                "title": item.get("title") or "",
                "fileUrl": public_url_for_key(oss_key) if oss_key else "",
                "thumbnailUrl": public_url_for_key(oss_key) if oss_key else "",
                "fileType": item.get("fileType") or "",
            }
        )

    return {
        "publicationId": str(pub.id),
        "title": pub.title,
        "sourceProjectId": source_pid,
        "flowJson": flow_data,
        "assets": assets_out,
        "nodeIds": _node_ids_from_flow(flow_data),
        "fromSnapshot": False,
    }


async def _copy_node_texts(
    *,
    source_project_id: str,
    target_project_id: str,
    source_folder: str | None,
    target_folder: str | None,
    node_ids: list[str],
) -> None:
    """拷贝节点文本与 meta（存在则拷）。"""
    storage = get_canvas_storage()
    for node_id in node_ids:
        text_rel = f"text/{node_id}.txt"
        meta_rel = f"text/{node_id}.meta.json"
        for rel, ctype in (
            (text_rel, "text/plain; charset=utf-8"),
            (meta_rel, "application/json; charset=utf-8"),
        ):
            try:
                src_key = storage.project_key(
                    source_project_id, rel, storage_folder=source_folder
                )
                if not await asyncio.to_thread(storage.object_exists, src_key):
                    continue
                data = await asyncio.to_thread(storage.get_bytes, src_key)
                if not data:
                    continue
                await asyncio.to_thread(
                    storage.put,
                    target_project_id,
                    rel,
                    data,
                    ctype,
                    storage_folder=target_folder,
                )
            except Exception as exc:
                logger.warning(
                    "copy node text failed project=%s node=%s rel=%s: %s",
                    source_project_id,
                    node_id,
                    rel,
                    exc,
                )


async def copy_publication_to_new_project(
    db: AsyncSession,
    user: User,
    publication_id: str,
) -> dict[str, Any]:
    """全量复制为新项目：优先从发布快照复制；无快照时回退源项目（兼容旧数据）。"""
    pub = await _get_publication_or_fail(db, publication_id)
    _assert_can_view(pub, user)

    await assert_project_create_allowed(db, int(user.id))

    snapshot = await _load_publication_snapshot(pub)
    source_result = await db.execute(
        select(Project).filter(
            Project.id == int(pub.source_project_id),
            Project.isdel.is_(False),
        )
    )
    source = source_result.scalar_one_or_none()
    # 有快照时即使源项目已删仍可复制；无快照则必须有源项目
    if snapshot is None and not source:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    new_title = f"{(pub.title or (source.title if source else '') or '未命名')[:40]} 副本"
    target = Project(
        owner_id=int(user.id),
        project_no=_gen_project_no(),
        title=new_title,
        description=pub.description or (source.description if source else "") or "",
        storage_folder=new_storage_folder(),
        is_public=False,
    )
    db.add(target)
    await db.flush()

    target_folder = project_storage_folder(target)
    target_pid = str(target.id)

    flow_data: dict[str, Any] = {"nodes": [], "edges": []}
    wf_title = pub.title or "未命名工作流"
    wf_description = pub.description or ""
    wf_version = "1"
    id_map: dict[str, str] = {}

    if snapshot is not None:
        flow_data, assets_manifest, meta = snapshot
        wf_title = str(meta.get("title") or wf_title)
        wf_description = str(meta.get("description") or wf_description)
        wf_version = str(meta.get("version") or "1")
        id_map = await _copy_snapshot_assets_to_project(
            db,
            assets_manifest=assets_manifest,
            target_project_id=target_pid,
            target_folder=target_folder,
        )
        remapped = remap_asset_ids_in_json(flow_data, id_map)
        if not isinstance(remapped, dict):
            remapped = {"nodes": [], "edges": []}
        node_ids = _node_ids_from_flow(flow_data)
        await _copy_snapshot_node_texts(
            publication_id=int(pub.id),
            target_project_id=target_pid,
            target_folder=target_folder,
            node_ids=node_ids,
        )
    else:
        # 兼容旧发布：仍从源项目最新复制（作者重新发布后即走快照）
        assert source is not None
        source_folder = project_storage_folder(source)
        source_pid = str(source.id)
        wf = await _latest_workflow(db, int(source.id))
        if wf:
            storage = get_canvas_storage()
            raw = await asyncio.to_thread(resolve_flow_json, wf.flow_json, storage)
            try:
                parsed = json.loads(raw) if raw else {}
                if isinstance(parsed, dict):
                    flow_data = parsed
            except json.JSONDecodeError:
                flow_data = {"nodes": [], "edges": []}
            wf_title = wf.title or wf_title
            wf_description = wf.description or wf_description
            wf_version = wf.version or "1"

        asset_ids = collect_asset_ids_from_json(flow_data)
        id_map = await copy_assets_batch(
            db,
            source_project_id=source_pid,
            target_project_id=target_pid,
            asset_ids=asset_ids,
            source_folder=source_folder,
            target_folder=target_folder,
        )
        remapped = remap_asset_ids_in_json(flow_data, id_map)
        if not isinstance(remapped, dict):
            remapped = {"nodes": [], "edges": []}
        node_ids = _node_ids_from_flow(flow_data)
        await _copy_node_texts(
            source_project_id=source_pid,
            target_project_id=target_pid,
            source_folder=source_folder,
            target_folder=target_folder,
            node_ids=node_ids,
        )

    new_wf = Workflow(
        project_id=int(target.id),
        title=wf_title,
        description=wf_description,
        flow_json="{}",
        version=wf_version,
        revision=1,
        node_count=str(len(_node_ids_from_flow(remapped if isinstance(remapped, dict) else {}))),
        status="draft",
    )
    db.add(new_wf)
    await db.flush()

    pointer = await asyncio.to_thread(
        store_flow_json,
        target_pid,
        str(new_wf.id),
        json.dumps(remapped, ensure_ascii=False),
        None,
        target_folder,
    )
    new_wf.flow_json = pointer

    db.add(
        WorkflowPublicationUse(
            publication_id=int(pub.id),
            user_id=int(user.id),
            target_project_id=int(target.id),
            created_at=now_cst_naive(),
        )
    )
    pub.use_count = int(pub.use_count or 0) + 1
    await db.flush()

    return {
        "projectId": target_pid,
        "workflowId": str(new_wf.id),
        "title": target.title,
        "copiedAssetCount": len(id_map),
        "useCount": int(pub.use_count or 0),
        "fromSnapshot": snapshot is not None,
    }


def _pub_display_status_clause(display_status: str):
    """按合成展示态过滤工作流发布。"""
    from sqlalchemy import and_

    ds = display_status.strip()
    if ds == "listed":
        return and_(
            WorkflowPublication.is_public.is_(True),
            WorkflowPublication.review_status == "approved",
        )
    if ds == "unlisted":
        return and_(
            WorkflowPublication.is_public.is_(False),
            WorkflowPublication.review_status == "approved",
        )
    if ds == "pending":
        return WorkflowPublication.review_status == "pending"
    if ds == "rejected":
        return WorkflowPublication.review_status == "rejected"
    return None


async def admin_list_publications(
    db: AsyncSession,
    *,
    review_status: str | None = None,
    display_status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """管理端工作流发布分页列表（含展示态筛选与角标计数）。"""
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    base = select(WorkflowPublication)
    kw = (q or "").strip()
    if kw:
        like = f"%{kw}%"
        base = base.filter(
            or_(
                WorkflowPublication.title.like(like),
                WorkflowPublication.description.like(like),
            )
        )
    cat = (category or "").strip()
    if cat and cat != "all":
        base = base.filter(WorkflowPublication.category == cat)

    ds = (display_status or "").strip()
    rs = (review_status or "").strip()
    if ds and ds != "all":
        clause = _pub_display_status_clause(ds)
        if clause is not None:
            base = base.filter(clause)
    elif rs and rs != "all":
        base = base.filter(WorkflowPublication.review_status == rs)

    count_q = select(func.count()).select_from(base.subquery())
    total = int((await db.execute(count_q)).scalar_one() or 0)
    rows = list(
        (
            await db.execute(
                base.order_by(WorkflowPublication.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    author_ids = {int(r.author_id) for r in rows}
    reviewer_ids = {
        int(r.reviewed_by) for r in rows if getattr(r, "reviewed_by", None) is not None
    }
    people = await _author_map(db, list(author_ids | reviewer_ids))
    items = [
        publication_to_dict(
            r,
            author=people.get(int(r.author_id)),
            liked=False,
            reviewer=people.get(int(r.reviewed_by))
            if getattr(r, "reviewed_by", None) is not None
            else None,
        )
        for r in rows
    ]

    count_base = select(WorkflowPublication)
    if kw:
        like = f"%{kw}%"
        count_base = count_base.filter(
            or_(
                WorkflowPublication.title.like(like),
                WorkflowPublication.description.like(like),
            )
        )
    if cat and cat != "all":
        count_base = count_base.filter(WorkflowPublication.category == cat)
    all_for_counts = list((await db.execute(count_base)).scalars().all())
    status_counts: dict[str, int] = {
        "all": len(all_for_counts),
        "listed": 0,
        "unlisted": 0,
        "pending": 0,
        "rejected": 0,
    }
    for r in all_for_counts:
        key = publication_display_status(r)
        status_counts[key] = status_counts.get(key, 0) + 1

    return {
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "statusCounts": status_counts,
    }


async def admin_review_publication(
    db: AsyncSession,
    admin: User,
    publication_id: str,
    *,
    action: str,
    note: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """管理端审核：approve | reject | unpublish。下架保留 approved。"""
    pub = await _get_publication_or_fail(db, publication_id)
    act = (action or "").strip().lower()
    now = now_cst_naive()
    note_s = (note or "").strip()[:500] or None
    cat_s = (category or "").strip()[:32] or None
    # 审核时改分类须落在后台配置的作品广场分类内
    if cat_s:
        allowed = await _allowed_publication_categories(db)
        if cat_s not in allowed:
            fail(ErrorCode.PUBLICATION_CATEGORY_INVALID)
    if act == "approve":
        pub.is_public = True
        pub.review_status = "approved"
        pub.review_note = note_s
        pub.reviewed_at = now
        pub.reviewed_by = int(admin.id)
        if cat_s:
            pub.category = cat_s
        _append_pub_review_history(
            pub, action="approve", note=note_s, at=now, admin=admin
        )
    elif act == "reject":
        pub.review_status = "rejected"
        pub.is_public = False
        pub.review_note = note_s or "未通过审核"
        pub.reviewed_at = now
        pub.reviewed_by = int(admin.id)
        _append_pub_review_history(
            pub, action="reject", note=pub.review_note, at=now, admin=admin
        )
    elif act == "unpublish":
        # 下架：仅关闭公开；保留 approved，与驳回区分
        pub.is_public = False
        if str(pub.review_status or "") == "approved":
            pub.review_status = "approved"
        elif str(pub.review_status or "") == "pending":
            pub.review_status = "rejected"
            note_s = note_s or "已下架"
        pub.review_note = note_s or "已下架"
        pub.reviewed_at = now
        pub.reviewed_by = int(admin.id)
        _append_pub_review_history(
            pub, action="unpublish", note=pub.review_note, at=now, admin=admin
        )
    else:
        fail(ErrorCode.BAD_REQUEST, message="action 须为 approve / reject / unpublish")
    pub.updated_at = now
    # 同步源项目公开标记
    proj = (
        await db.execute(
            select(Project).filter(Project.id == int(pub.source_project_id)).limit(1)
        )
    ).scalar_one_or_none()
    if proj is not None:
        proj.is_public = bool(pub.is_public and pub.review_status == "approved")
    await db.flush()
    # 审核结果通知作者
    if act in ("approve", "reject", "unpublish"):
        try:
            from .user_notifications import CATEGORY_WORKFLOW_REVIEW, notify_user
            from ..core.datetime_util import to_cst_iso

            title_map = {
                "approve": "工作流审核通过",
                "reject": "工作流审核未通过",
                "unpublish": "工作流已下架",
            }
            note_part = f"：{pub.review_note}" if pub.review_note else ""
            await notify_user(
                db,
                int(pub.author_id),
                category=CATEGORY_WORKFLOW_REVIEW,
                title=title_map[act],
                body=f"「{pub.title}」{title_map[act]}{note_part}",
                dedupe_key=(
                    f"workflow_review:{act}:{int(pub.id)}:"
                    f"{to_cst_iso(now) or now.isoformat()}"
                ),
                link_url="/discover",
                ref_type="workflow_publication",
                ref_id=str(pub.id),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "notify workflow review failed pub=%s", getattr(pub, "id", None), exc_info=True
            )
    authors = await _author_map(db, [int(pub.author_id)])
    return publication_to_dict(
        pub,
        author=authors.get(int(pub.author_id)),
        liked=False,
        reviewer=admin,
    )


async def admin_update_publication_category(
    db: AsyncSession,
    publication_id: str,
    category: str,
) -> dict[str, Any]:
    """管理端修改工作流发布分类。"""
    pub = await _get_publication_or_fail(db, publication_id)
    cat = (category or "").strip()[:32]
    if not cat:
        fail(ErrorCode.VALIDATION_ERROR, message="分类不能为空")
    allowed = await _allowed_publication_categories(db)
    if cat not in allowed:
        fail(ErrorCode.PUBLICATION_CATEGORY_INVALID)
    pub.category = cat
    pub.updated_at = now_cst_naive()
    await db.flush()
    authors = await _author_map(db, [int(pub.author_id)])
    return publication_to_dict(pub, author=authors.get(int(pub.author_id)), liked=False)
