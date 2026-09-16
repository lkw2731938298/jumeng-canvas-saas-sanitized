"""画布节点组库 API：按项目隔离，快照存 OSS；支持标签/排序与跨项目深拷贝。"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.datetime_util import to_cst_iso
from ...core.deps import get_current_user
from ...core.entity_ids import parse_entity_id, require_entity_id
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...integrations.oss.canvas_storage import (
    StorageWriteError,
    get_canvas_storage,
)
from ...models.database import get_db
from ...models.node_group import CanvasNodeGroup
from ...models.user import User
from ...services.asset_copy import copy_asset_to_project
from ...services.cache import invalidate_manifest_cache
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder

router = APIRouter()

SortKey = Literal["updated_at", "created_at", "title", "node_count"]


class NodeGroupCreateIn(BaseModel):
    title: str = Field(default="未命名组", max_length=256)
    snapshot: dict[str, Any]
    cover_asset_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    node_count: int = 0


class NodeGroupPatchIn(BaseModel):
    project_id: str
    title: str | None = Field(default=None, max_length=256)
    cover_asset_id: str | None = None
    tags: list[str] | None = None


class NodeGroupCopyIn(BaseModel):
    source_project_id: str
    target_project_id: str
    title: str | None = Field(default=None, max_length=256)


def _snapshot_rel(group_id: int | str) -> str:
    return f"groups/{group_id}/snapshot.json"


def _normalize_asset_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip() if item is not None else ""
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _normalize_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip() if item is not None else ""
        if not s or len(s) > 32 or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= 16:
            break
    return out


def _group_list_item(row: CanvasNodeGroup) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.project_id),
        "title": row.title or "未命名组",
        "coverAssetId": str(row.cover_asset_id) if row.cover_asset_id is not None else None,
        "assetIds": _normalize_asset_ids(row.asset_ids),
        "tags": _normalize_tags(getattr(row, "tags", None) or []),
        "nodeCount": int(row.node_count or 0),
        "createdAt": to_cst_iso(row.created_at) or "",
        "updatedAt": to_cst_iso(row.updated_at) or "",
    }


def _group_detail(row: CanvasNodeGroup, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    item = _group_list_item(row)
    item["snapshot"] = snapshot if isinstance(snapshot, dict) else {}
    item["ossKey"] = row.oss_key or ""
    return item


async def _load_group(
    db: AsyncSession, project_id: str, group_id: str
) -> CanvasNodeGroup:
    pid = require_entity_id(project_id, message="项目 ID 无效")
    gid = require_entity_id(group_id, message="组 ID 无效")
    result = await db.execute(
        select(CanvasNodeGroup).filter(
            CanvasNodeGroup.id == gid,
            CanvasNodeGroup.project_id == pid,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        fail(ErrorCode.NODE_GROUP_NOT_FOUND)
    return row


def _collect_snapshot_asset_ids(snapshot: dict[str, Any], cover: Any = None) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        s = str(raw).strip() if raw is not None else ""
        if not s or s in seen:
            return
        seen.add(s)
        ids.append(s)

    if cover is not None:
        add(cover)
    assets = snapshot.get("assets")
    if isinstance(assets, list):
        for a in assets:
            if isinstance(a, dict):
                add(a.get("assetId"))
            else:
                add(a)
    nodes = snapshot.get("nodes")
    if isinstance(nodes, list):
        for n in nodes:
            if not isinstance(n, dict):
                continue
            data = n.get("data") if isinstance(n.get("data"), dict) else {}
            params = data.get("params") if isinstance(data.get("params"), dict) else {}
            add(params.get("assetId"))
    return ids


# 跨项目素材拷贝统一走 services.asset_copy
_copy_asset_to_project = copy_asset_to_project


def _remap_snapshot_assets(snapshot: dict[str, Any], id_map: dict[str, str]) -> dict[str, Any]:
    """深拷贝快照并替换 assetId；未映射的引用一律清除，禁止残留源项目 id。"""
    import copy

    out = copy.deepcopy(snapshot)
    assets = out.get("assets")
    if isinstance(assets, list):
        new_assets = []
        for a in assets:
            if isinstance(a, dict):
                old = str(a.get("assetId") or "").strip()
                mapped = id_map.get(old)
                if not mapped:
                    continue
                new_assets.append({**a, "assetId": mapped})
            else:
                old = str(a).strip()
                mapped = id_map.get(old)
                if mapped:
                    new_assets.append(mapped)
        out["assets"] = new_assets
    nodes = out.get("nodes")
    if isinstance(nodes, list):
        for n in nodes:
            if not isinstance(n, dict):
                continue
            data = n.get("data")
            if not isinstance(data, dict):
                continue
            params = data.get("params")
            if not isinstance(params, dict):
                continue
            old = str(params.get("assetId") or "").strip()
            if old:
                mapped = id_map.get(old)
                if mapped:
                    params["assetId"] = mapped
                else:
                    params.pop("assetId", None)
            # 跨项目禁止保留源项目临时/签名 URL
            for key in ("imageUrl", "videoUrl", "audioUrl"):
                params.pop(key, None)
    return out


@router.get("/projects/{project_id}")
async def list_node_groups(
    project_id: str,
    q: str | None = Query(default=None, description="按标题搜索"),
    tag: str | None = Query(default=None, description="按标签过滤"),
    sort: SortKey = Query(default="updated_at"),
    order: Literal["asc", "desc"] = Query(default="desc"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出项目内已保存的节点组（支持搜索/标签/排序）。"""
    await require_project_access(db, current_user, project_id)
    pid = require_entity_id(project_id, message="项目 ID 无效")
    result = await db.execute(
        select(CanvasNodeGroup).filter(CanvasNodeGroup.project_id == pid)
    )
    rows = list(result.scalars().all())

    q_norm = (q or "").strip().lower()
    tag_norm = (tag or "").strip()
    filtered: list[CanvasNodeGroup] = []
    for row in rows:
        if q_norm and q_norm not in (row.title or "").lower():
            continue
        if tag_norm:
            tags = _normalize_tags(getattr(row, "tags", None) or [])
            if tag_norm not in tags:
                continue
        filtered.append(row)

    reverse = order != "asc"

    def sort_key(row: CanvasNodeGroup):
        if sort == "title":
            return (row.title or "").lower()
        if sort == "node_count":
            return int(row.node_count or 0)
        if sort == "created_at":
            return row.created_at or row.updated_at
        return row.updated_at or row.created_at

    filtered.sort(key=sort_key, reverse=reverse)
    return [_group_list_item(r) for r in filtered]


@router.post("/projects/{project_id}", status_code=201)
async def create_node_group(
    project_id: str,
    body: NodeGroupCreateIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存画布组快照到组库（MySQL + OSS）。"""
    project = await require_project_access(db, current_user, project_id)
    if not isinstance(body.snapshot, dict) or not body.snapshot:
        fail(ErrorCode.BAD_REQUEST, message="组快照无效")

    title = (body.title or "").strip() or "未命名组"
    asset_ids = _normalize_asset_ids(body.asset_ids)
    if not asset_ids:
        asset_ids = _collect_snapshot_asset_ids(body.snapshot, body.cover_asset_id)
    tags = _normalize_tags(body.tags)

    nodes = body.snapshot.get("nodes")
    node_count = int(body.node_count or 0)
    if node_count <= 0 and isinstance(nodes, list):
        node_count = len(nodes)

    cover_id = None
    if body.cover_asset_id:
        cover_id = parse_entity_id(str(body.cover_asset_id))
    if cover_id is None and asset_ids:
        cover_id = parse_entity_id(asset_ids[0])

    row = CanvasNodeGroup(
        project_id=project.id,
        owner_id=current_user.id,
        title=title,
        cover_asset_id=cover_id,
        oss_key="",
        asset_ids=asset_ids,
        tags=tags,
        node_count=node_count,
    )
    db.add(row)
    await db.flush()

    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    rel = _snapshot_rel(row.id)
    try:
        stored = await asyncio.to_thread(
            storage.write_json,
            project_id,
            rel,
            body.snapshot,
            storage_folder=folder,
        )
    except StorageWriteError:
        await db.rollback()
        fail(ErrorCode.INTERNAL_ERROR, message="组快照写入存储失败")

    row.oss_key = stored.oss_key
    await db.commit()
    await db.refresh(row)
    return _group_detail(row, body.snapshot)


@router.get("/{group_id}")
async def get_node_group(
    group_id: str,
    projectId: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取组详情（含 OSS 快照）。"""
    project = await require_project_access(db, current_user, projectId)
    row = await _load_group(db, projectId, group_id)
    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    snapshot = await asyncio.to_thread(
        storage.read_json, projectId, _snapshot_rel(row.id), storage_folder=folder
    )
    if not isinstance(snapshot, dict):
        fail(ErrorCode.NODE_GROUP_NOT_FOUND, message="组快照不存在或已损坏")
    return _group_detail(row, snapshot)


@router.patch("/{group_id}")
async def patch_node_group(
    group_id: str,
    body: NodeGroupPatchIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重命名、换封面或更新标签。"""
    await require_project_access(db, current_user, body.project_id)
    row = await _load_group(db, body.project_id, group_id)
    if body.title is not None:
        row.title = body.title.strip() or "未命名组"
    if body.cover_asset_id is not None:
        if body.cover_asset_id == "":
            row.cover_asset_id = None
        else:
            row.cover_asset_id = require_entity_id(
                str(body.cover_asset_id), message="封面素材 ID 无效"
            )
    if body.tags is not None:
        row.tags = _normalize_tags(body.tags)
    await db.commit()
    await db.refresh(row)
    return _group_list_item(row)


@router.delete("/{group_id}")
async def delete_node_group(
    group_id: str,
    projectId: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除组库条目（不删除 project_assets）。"""
    await require_project_access(db, current_user, projectId)
    row = await _load_group(db, projectId, group_id)
    await db.delete(row)
    await db.commit()
    return {"ok": True}


@router.post("/{group_id}/copy-to-project", status_code=201)
async def copy_node_group_to_project(
    group_id: str,
    body: NodeGroupCopyIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """跨项目复制组：深拷贝引用素材到目标项目后再写组库条目。"""
    if body.source_project_id == body.target_project_id:
        fail(ErrorCode.BAD_REQUEST, message="目标项目不能与源项目相同")

    source_project = await require_project_access(db, current_user, body.source_project_id)
    target_project = await require_project_access(db, current_user, body.target_project_id)
    row = await _load_group(db, body.source_project_id, group_id)

    source_folder = project_storage_folder(source_project)
    target_folder = project_storage_folder(target_project)
    source_pid_str = str(source_project.id)
    target_pid_str = str(target_project.id)
    storage = get_canvas_storage()
    snapshot = await asyncio.to_thread(
        storage.read_json,
        source_pid_str,
        _snapshot_rel(row.id),
        storage_folder=source_folder,
    )
    if not isinstance(snapshot, dict):
        fail(ErrorCode.NODE_GROUP_NOT_FOUND, message="组快照不存在或已损坏")

    asset_ids = _normalize_asset_ids(row.asset_ids)
    for aid in _collect_snapshot_asset_ids(snapshot, row.cover_asset_id):
        if aid not in asset_ids:
            asset_ids.append(aid)

    id_map: dict[str, str] = {}
    for aid in asset_ids:
        # 任一素材复制失败会 fail，禁止写入残留源项目 assetId 的快照
        new_id = await _copy_asset_to_project(
            db,
            source_project_id=source_pid_str,
            target_project_id=target_pid_str,
            asset_id=aid,
            source_folder=source_folder,
            target_folder=target_folder,
        )
        id_map[aid] = new_id

    remapped = _remap_snapshot_assets(snapshot, id_map)
    # 同步快照标题
    title = (body.title or "").strip() or (row.title or "未命名组")
    remapped["title"] = title
    if isinstance(remapped.get("group"), dict):
        remapped["group"] = {**remapped["group"], "label": title}
    if isinstance(remapped.get("groupNode"), dict):
        gn = remapped["groupNode"]
        data = gn.get("data") if isinstance(gn.get("data"), dict) else {}
        remapped["groupNode"] = {
            **gn,
            "data": {**data, "label": title},
        }
    new_asset_ids = [id_map[a] for a in asset_ids if a in id_map]
    new_cover = None
    if row.cover_asset_id is not None:
        mapped = id_map.get(str(row.cover_asset_id))
        new_cover = parse_entity_id(mapped) if mapped else None
    if new_cover is None and new_asset_ids:
        new_cover = parse_entity_id(new_asset_ids[0])

    # 统一用 ORM 项目主键，避免 body 字符串与 DB 类型不一致
    target_pid = int(target_project.id)
    new_row = CanvasNodeGroup(
        project_id=target_pid,
        owner_id=current_user.id,
        title=title,
        cover_asset_id=new_cover,
        oss_key="",
        asset_ids=new_asset_ids,
        tags=_normalize_tags(getattr(row, "tags", None) or []),
        node_count=int(row.node_count or 0),
    )
    db.add(new_row)
    await db.flush()

    try:
        stored = await asyncio.to_thread(
            storage.write_json,
            target_pid_str,
            _snapshot_rel(new_row.id),
            remapped,
            storage_folder=target_folder,
        )
    except StorageWriteError:
        await db.rollback()
        fail(ErrorCode.INTERNAL_ERROR, message="目标组快照写入失败")

    new_row.oss_key = stored.oss_key
    await db.commit()
    await db.refresh(new_row)

    # 提交后校验：确保列表接口能查到（防止事务未落库仍返回成功）
    verify = await db.execute(
        select(CanvasNodeGroup).filter(
            CanvasNodeGroup.id == new_row.id,
            CanvasNodeGroup.project_id == target_pid,
        )
    )
    if verify.scalar_one_or_none() is None:
        fail(ErrorCode.INTERNAL_ERROR, message="组已写入但目标项目校验失败，请重试")

    await invalidate_manifest_cache(target_pid_str)
    return _group_detail(new_row, remapped)
