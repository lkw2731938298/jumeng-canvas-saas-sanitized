"""项目素材索引 —— MySQL project_assets 为权威，兼容 OSS index.json 迁移与去重。"""

from __future__ import annotations

import re
from ..core.datetime_util import now_cst_naive
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.entity_ids import is_legacy_uuid, parse_entity_id, require_entity_id
from ..integrations.oss.canvas_storage import get_canvas_storage
from ..integrations.oss.service import get_oss
from ..models.asset import ProjectAsset
from ..models.project import Project
from ..services.asset_index import load_index as load_json_index, storage_folder_to_uuid
from ..services.project_scope import project_storage_folder
from ..services.storage_urls import normalize_browser_storage_url, oss_key_from_browser_url

ASSETS_INDEX = "assets/index.json"
_MEDIA_CATEGORIES = frozenset({"image", "video", "audio", "document", "model"})
_LEGACY_ASSET_UUID_RE = re.compile(
    r"/assets/[^/]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.",
    re.IGNORECASE,
)


def legacy_asset_uuid_from_oss_key(oss_key: str | None) -> str | None:
    """从 OSS 路径中提取历史 UUID 文件名（用于兼容旧资产 id）。"""
    if not oss_key:
        return None
    match = _LEGACY_ASSET_UUID_RE.search(str(oss_key))
    if not match:
        return None
    return match.group(1).lower()


def _canonical_asset_oss_key(oss_key: str) -> str:
    """Normalize legacy canvas/ ↔ Ihuabu/ prefixes to the configured object prefix."""
    raw = str(oss_key or "").strip()
    if not raw:
        return raw
    configured = (get_settings().oss_object_prefix or "canvas").rstrip("/")
    for prefix in ("canvas", "Ihuabu"):
        if raw.startswith(f"{prefix}/"):
            if prefix == configured:
                return raw
            return raw.replace(f"{prefix}/", f"{configured}/", 1)
    return raw


def _asset_identity_keys(oss_key: str, record: dict | None = None) -> set[str]:
    keys: set[str] = set()
    raw = str(oss_key or "").strip()
    if raw:
        keys.add(raw)
        canonical = _canonical_asset_oss_key(raw)
        keys.add(canonical)
        storage = get_canvas_storage()
        for alt in storage._alternate_oss_keys(raw):
            keys.add(alt)
            keys.add(_canonical_asset_oss_key(alt))
    if record:
        raw_id = str(record.get("id") or "").strip()
        if is_legacy_uuid(raw_id):
            keys.add(raw_id.lower())
        legacy = legacy_asset_uuid_from_oss_key(raw)
        if legacy:
            keys.add(legacy)
    return {key for key in keys if key}


def _parse_entity_id(value: str) -> int:
    return require_entity_id(value)


def _persistable_thumbnail_value(record: dict) -> str | None:
    """写入 DB 的缩略图：优先存 OSS key，避免把会过期的预签名 URL 落库。"""
    thumb_key = record.get("thumbnailOssKey") or record.get("thumbnail_oss_key")
    if thumb_key:
        key = str(thumb_key).strip()
        if key:
            return key
    thumb = record.get("thumbnailUrl") or record.get("thumbnail_url")
    if thumb is None:
        return None
    raw = str(thumb).strip()
    if not raw:
        return None
    if raw.startswith("/uploads/"):
        return raw
    parsed = oss_key_from_browser_url(raw)
    if parsed:
        return parsed
    return raw


def _record_from_row(row: ProjectAsset) -> dict:
    file_url = normalize_browser_storage_url("", oss_key=row.oss_key)
    thumb_raw = row.thumbnail_url
    if not thumb_raw or thumb_raw == row.oss_key:
        thumb = file_url
    elif str(thumb_raw).startswith("/uploads/"):
        thumb = str(thumb_raw)
    else:
        # 读库时按 key / 过期签名 URL 重新签发，与 fileUrl 对称
        thumb = normalize_browser_storage_url(str(thumb_raw))
    legacy_id = legacy_asset_uuid_from_oss_key(row.oss_key)
    record = {
        "id": str(row.id),
        "projectId": str(row.project_id),
        "title": row.title,
        "category": row.category,
        "subcategory": row.subcategory,
        "fileUrl": file_url,
        "thumbnailUrl": thumb,
        "fileType": row.file_type,
        "fileSize": int(row.file_size or 0),
        "ossKey": row.oss_key,
        "source": row.source or "upload",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }
    if legacy_id:
        record["legacyId"] = legacy_id
    return record


def _row_from_legacy(item: dict, project_id: int) -> ProjectAsset | None:
    oss_key = item.get("ossKey") or item.get("oss_key")
    if not oss_key:
        return None

    created_raw = item.get("createdAt") or item.get("created_at")
    created_at = now_cst_naive()
    if isinstance(created_raw, str) and created_raw:
        try:
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            pass

    thumb = _persistable_thumbnail_value(item)
    return ProjectAsset(
        project_id=project_id,
        title=str(item.get("title") or "未命名"),
        category=str(item.get("category") or "image"),
        subcategory=item.get("subcategory"),
        file_type=str(item.get("fileType") or item.get("file_type") or "application/octet-stream"),
        file_size=int(item.get("fileSize") or item.get("file_size") or 0),
        oss_key=str(oss_key),
        thumbnail_url=thumb,
        source=str(item.get("source") or "upload"),
        created_at=created_at,
    )


def _guess_file_type(category: str, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    defaults = {
        "image": "image/png",
        "video": "video/mp4",
        "audio": "audio/mpeg",
        "model": "model/gltf-binary",
    }
    by_ext = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "glb": "model/gltf-binary",
        "gltf": "model/gltf+json",
    }
    return by_ext.get(ext, defaults.get(category, "application/octet-stream"))


def _row_from_oss_key(oss_key: str, project_id: int, *, size: int = 0) -> ProjectAsset | None:
    parts = str(oss_key).split("/")
    try:
        assets_idx = parts.index("assets")
    except ValueError:
        return None
    if assets_idx + 2 >= len(parts):
        return None
    category = parts[assets_idx + 1]
    filename = parts[-1]
    if category not in _MEDIA_CATEGORIES:
        return None
    if filename in ("index.json",) or ".thumb." in filename:
        return None
    if category == "model" and assets_idx + 3 < len(parts):
        filename = parts[assets_idx + 2]
    title = filename.rsplit(".", 1)[0] if "." in filename else filename
    return ProjectAsset(
        project_id=project_id,
        title=title or "未命名",
        category=category,
        subcategory=None,
        file_type=_guess_file_type(category, filename),
        file_size=int(size or 0),
        oss_key=str(oss_key),
        thumbnail_url=None,
        source="upload",
        created_at=now_cst_naive(),
    )


def _scan_oss_asset_keys(project_id: str, storage_folder: str) -> list[str]:
    storage = get_canvas_storage()
    oss = get_oss()
    if not oss.bucket:
        return []
    folders = [storage_folder]
    dashed = storage_folder_to_uuid(storage_folder)
    if dashed != storage_folder:
        folders.append(dashed)
    keys: list[str] = []
    seen: set[str] = set()
    for folder in folders:
        prefix = storage.project_key(project_id, "assets/", storage_folder=folder)
        if not prefix.endswith("/"):
            prefix = f"{prefix}/"
        for candidate in (prefix, prefix.replace("Ihuabu/", "canvas/", 1), prefix.replace("canvas/", "Ihuabu/", 1)):
            for key in oss.list_object_keys(candidate):
                resolved = storage.resolve_existing_key(key) or key
                canonical = _canonical_asset_oss_key(resolved)
                if canonical in seen:
                    continue
                seen.add(canonical)
                keys.append(canonical)
    return keys


async def sync_project_assets_from_oss(
    db: AsyncSession,
    project_id: str,
    *,
    project: Project | None = None,
    scan_oss: bool = True,
) -> int:
    """从 OSS index.json 与可选目录扫描补全缺失的素材索引行。"""
    pid = _parse_entity_id(project_id)
    if project is None:
        project = await db.get(Project, pid)
    if not project:
        return 0
    folder = project_storage_folder(project)

    result = await db.execute(
        select(ProjectAsset.oss_key).where(ProjectAsset.project_id == pid)
    )
    existing_keys = {str(row[0]) for row in result.all() if row[0]}

    inserted = 0
    legacy_items = load_json_index(project_id, storage_folder=folder)
    for item in legacy_items:
        if not isinstance(item, dict):
            continue
        row = _row_from_legacy(item, pid)
        if not row or row.oss_key in existing_keys:
            continue
        db.add(row)
        existing_keys.add(row.oss_key)
        inserted += 1

    if scan_oss:
        for oss_key in _scan_oss_asset_keys(project_id, folder):
            canonical = _canonical_asset_oss_key(oss_key)
            if canonical in existing_keys:
                continue
            row = _row_from_oss_key(canonical, pid)
            if not row:
                continue
            db.add(row)
            existing_keys.add(canonical)
            inserted += 1

    if inserted:
        await db.flush()
    return inserted


async def dedupe_project_assets_by_oss_key(db: AsyncSession, project_id: str) -> int:
    """按规范 OSS key 或 legacy UUID 去重，删除重复素材行。"""
    pid = _parse_entity_id(project_id)
    result = await db.execute(
        select(ProjectAsset)
        .where(ProjectAsset.project_id == pid)
        .order_by(ProjectAsset.id.asc())
    )
    rows = list(result.scalars().all())
    seen_keys: set[str] = set()
    seen_legacy: set[str] = set()
    removed = 0
    for row in rows:
        key = _canonical_asset_oss_key(str(row.oss_key or ""))
        legacy = legacy_asset_uuid_from_oss_key(row.oss_key)
        duplicate = (key and key in seen_keys) or (legacy and legacy in seen_legacy)
        if duplicate:
            await db.delete(row)
            removed += 1
            continue
        if key:
            seen_keys.add(key)
            if row.oss_key != key:
                row.oss_key = key
        if legacy:
            seen_legacy.add(legacy)
    if removed:
        await db.flush()
    return removed


async def _find_asset_row_for_record(
    db: AsyncSession,
    project_id: int,
    oss_key: str,
    record: dict,
) -> ProjectAsset | None:
    identities = _asset_identity_keys(oss_key, record)
    if not identities:
        return None

    result = await db.execute(
        select(ProjectAsset)
        .where(ProjectAsset.project_id == project_id)
        .order_by(ProjectAsset.id.asc())
    )
    for row in result.scalars().all():
        row_identities = _asset_identity_keys(str(row.oss_key or ""), None)
        row_legacy = legacy_asset_uuid_from_oss_key(row.oss_key)
        if row_legacy:
            row_identities.add(row_legacy)
        if identities & row_identities:
            return row
    return None


async def _find_asset_row_by_oss_key(
    db: AsyncSession,
    project_id: int,
    oss_key: str,
    *,
    record: dict | None = None,
) -> ProjectAsset | None:
    if record is not None:
        found = await _find_asset_row_for_record(db, project_id, oss_key, record)
        if found:
            return found
    canonical = _canonical_asset_oss_key(oss_key)
    keys = {oss_key, canonical, *get_canvas_storage()._alternate_oss_keys(oss_key)}
    keys = {_canonical_asset_oss_key(key) for key in keys if key}
    for key in sorted(keys):
        result = await db.execute(
            select(ProjectAsset)
            .where(
                ProjectAsset.project_id == project_id,
                ProjectAsset.oss_key == key,
            )
            .order_by(ProjectAsset.id.asc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return row
    return None


async def ensure_migrated(db: AsyncSession, project_id: str) -> None:
    """确保项目素材已从 OSS 迁移到 DB 并完成去重。"""
    pid = _parse_entity_id(project_id)
    project = await db.get(Project, pid)
    if not project:
        return
    await sync_project_assets_from_oss(db, project_id, project=project)
    await dedupe_project_assets_by_oss_key(db, project_id)


async def list_project_assets(
    db: AsyncSession,
    project_id: str,
    *,
    category: str | None = None,
    subcategory: str | None = None,
) -> list[dict]:
    """列出项目素材（可选按 category/subcategory 筛选），返回前端 DTO 列表。"""
    await ensure_migrated(db, project_id)
    pid = _parse_entity_id(project_id)
    query = (
        select(ProjectAsset)
        .where(ProjectAsset.project_id == pid)
        .order_by(ProjectAsset.created_at.desc())
    )
    if category:
        query = query.where(ProjectAsset.category == category)
    if subcategory:
        query = query.where(ProjectAsset.subcategory == subcategory)

    result = await db.execute(query)
    return [_record_from_row(row) for row in result.scalars().all()]


async def get_project_assets_by_ids(
    db: AsyncSession,
    project_id: str,
    asset_ids: Iterable[str],
    *,
    sync_oss: bool = True,
) -> list[dict]:
    """批量按 id（含 legacy UUID）查询项目素材，避免 N+1。"""
    if sync_oss:
        await ensure_migrated(db, project_id)
    ids: list[int] = []
    legacy_uuids: list[str] = []
    for raw in asset_ids:
        trimmed = str(raw or "").strip()
        if not trimmed:
            continue
        parsed = parse_entity_id(trimmed)
        if parsed is not None:
            ids.append(parsed)
        elif is_legacy_uuid(trimmed):
            legacy_uuids.append(trimmed.lower())

    if not ids and not legacy_uuids:
        return []

    pid = _parse_entity_id(project_id)
    filters = [ProjectAsset.project_id == pid]
    match_clauses = []
    if ids:
        match_clauses.append(ProjectAsset.id.in_(ids))
    for legacy_id in legacy_uuids:
        match_clauses.append(ProjectAsset.oss_key.ilike(f"%/{legacy_id}.%"))
    if not match_clauses:
        return []

    result = await db.execute(select(ProjectAsset).where(filters[0], or_(*match_clauses)))
    return [_record_from_row(row) for row in result.scalars().all()]


async def get_project_asset(
    db: AsyncSession,
    project_id: str,
    asset_id: str,
    *,
    sync_oss: bool = True,
) -> dict | None:
    """按 asset_id 查询单条项目素材记录。"""
    rows = await get_project_assets_by_ids(db, project_id, [asset_id], sync_oss=sync_oss)
    return rows[0] if rows else None


async def insert_project_asset(
    db: AsyncSession,
    project_id: str,
    record: dict,
    *,
    sync_oss: bool = True,
) -> dict:
    """插入或更新项目素材索引（按 OSS key 去重合并），返回规范化的记录。"""
    from .storage_quota import apply_storage_delta_for_project

    if sync_oss:
        await ensure_migrated(db, project_id)
    pid = _parse_entity_id(project_id)
    oss_key = _canonical_asset_oss_key(str(record.get("ossKey") or record.get("oss_key") or "").strip())

    if oss_key:
        existing = await _find_asset_row_by_oss_key(db, pid, oss_key, record=record)
        if existing:
            old_size = int(existing.file_size or 0)
            existing.oss_key = oss_key
            existing.title = str(record.get("title") or existing.title or "未命名")
            if record.get("subcategory") is not None:
                existing.subcategory = record.get("subcategory")
            existing.file_type = str(
                record.get("fileType")
                or record.get("file_type")
                or existing.file_type
                or "application/octet-stream"
            )
            size = record.get("fileSize") if record.get("fileSize") is not None else record.get("file_size")
            if size is not None:
                existing.file_size = int(size or 0)
            thumb = _persistable_thumbnail_value(record)
            if thumb:
                existing.thumbnail_url = thumb
            if record.get("source"):
                existing.source = str(record.get("source"))
            await db.flush()
            delta = int(existing.file_size or 0) - old_size
            if delta:
                await apply_storage_delta_for_project(db, pid, delta)
            return _record_from_row(existing)

    raw_id = record.get("id")
    aid = parse_entity_id(str(raw_id)) if raw_id is not None else None
    new_size = int(record.get("fileSize") or record.get("file_size") or 0)
    row_kwargs = dict(
        project_id=pid,
        title=str(record.get("title") or "未命名"),
        category=str(record.get("category") or "image"),
        subcategory=record.get("subcategory"),
        file_type=str(record.get("fileType") or record.get("file_type") or "application/octet-stream"),
        file_size=new_size,
        oss_key=oss_key,
        thumbnail_url=_persistable_thumbnail_value(record),
        source=str(record.get("source") or "upload"),
        created_at=now_cst_naive(),
    )
    if aid is not None:
        row = ProjectAsset(id=aid, **row_kwargs)
    else:
        row = ProjectAsset(**row_kwargs)
    db.add(row)
    await db.flush()
    if new_size:
        await apply_storage_delta_for_project(db, pid, new_size)
    return _record_from_row(row)


async def delete_project_asset(
    db: AsyncSession,
    project_id: str,
    asset_id: str,
) -> dict | None:
    """删除项目素材 DB 行（不删 OSS 对象，由调用方决定）。"""
    from .storage_quota import apply_storage_delta_for_project

    await ensure_migrated(db, project_id)
    rows = await get_project_assets_by_ids(db, project_id, [asset_id])
    if not rows:
        return None
    pid = _parse_entity_id(project_id)
    aid = parse_entity_id(rows[0]["id"])
    if aid is None:
        return None
    result = await db.execute(
        select(ProjectAsset).where(
            ProjectAsset.project_id == pid,
            ProjectAsset.id == aid,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    record = _record_from_row(row)
    removed_size = int(row.file_size or 0)
    await db.delete(row)
    await db.flush()
    if removed_size:
        await apply_storage_delta_for_project(db, pid, -removed_size)
    return record


def delete_oss_object(oss_key: str) -> None:
    """删除 OSS 上的素材对象（按 key）。"""
    key = str(oss_key or "").strip()
    if not key:
        return
    get_canvas_storage().delete(key)
