"""跨项目素材字节拷贝：节点组复制与工作流发布复制共用。"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.oss.canvas_storage import (
    StorageWriteError,
    get_canvas_storage,
    new_asset_id,
)
from .asset_store import get_project_asset, insert_project_asset
from .cache import invalidate_manifest_cache
from .storage_quota import assert_storage_quota_for_project


async def copy_asset_to_project(
    db: AsyncSession,
    *,
    source_project_id: str,
    target_project_id: str,
    asset_id: str,
    source_folder: str | None,
    target_folder: str | None,
) -> str:
    """把单个素材字节拷到目标项目，返回新 asset id；失败抛错（禁止静默丢素材）。"""
    src = await get_project_asset(db, source_project_id, asset_id, sync_oss=False)
    if not src:
        src = await get_project_asset(db, source_project_id, asset_id, sync_oss=True)
    if not src:
        fail(
            ErrorCode.BAD_REQUEST,
            message=f"源项目素材不存在，无法复制（assetId={asset_id}）",
        )
    oss_key = str(src.get("ossKey") or "").strip()
    if not oss_key:
        fail(
            ErrorCode.BAD_REQUEST,
            message=f"源素材缺少存储路径，无法复制（assetId={asset_id}）",
        )
    storage = get_canvas_storage()
    try:
        data = await asyncio.to_thread(storage.get_bytes, oss_key)
    except Exception as exc:
        fail(
            ErrorCode.INTERNAL_ERROR,
            message=f"读取源素材失败（assetId={asset_id}）：{exc}",
        )
    if not data:
        fail(
            ErrorCode.BAD_REQUEST,
            message=f"源素材文件为空或不存在（assetId={asset_id}）",
        )

    await assert_storage_quota_for_project(db, target_project_id, len(data))

    category = str(src.get("category") or "image")
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
            str(src.get("fileType") or "application/octet-stream"),
            storage_folder=target_folder,
        )
    except StorageWriteError as exc:
        fail(
            ErrorCode.INTERNAL_ERROR,
            message=f"写入目标项目素材失败（assetId={asset_id}）：{exc}",
        )

    # 缩略图：只复制同目录 .thumb.jpg，禁止沿用源项目签名 URL
    thumb_url = None
    thumb_key_guess = oss_key.rsplit(".", 1)[0] + ".thumb.jpg"
    try:
        if await asyncio.to_thread(storage.object_exists, thumb_key_guess):
            thumb_bytes = await asyncio.to_thread(storage.get_bytes, thumb_key_guess)
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
        "title": src.get("title") or "未命名",
        "category": category,
        "subcategory": src.get("subcategory"),
        "fileType": src.get("fileType") or "application/octet-stream",
        "fileSize": len(data),
        "ossKey": stored.oss_key,
        "thumbnailUrl": thumb_url,
        "source": "copy",
    }
    inserted = await insert_project_asset(db, target_project_id, record, sync_oss=False)
    new_id = str(inserted.get("id") or "").strip()
    if not new_id:
        fail(ErrorCode.INTERNAL_ERROR, message=f"目标素材入库失败（源 assetId={asset_id}）")
    return new_id


def collect_asset_ids_from_json(data: Any) -> list[str]:
    """递归收集 JSON 中的 assetId / asset_id 引用。"""
    ids: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        s = str(raw).strip() if raw is not None else ""
        if not s or s in seen:
            return
        seen.add(s)
        ids.append(s)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("assetId", "asset_id") and not isinstance(value, (dict, list)):
                    add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return ids


def remap_asset_ids_in_json(data: Any, id_map: dict[str, str]) -> Any:
    """深拷贝并替换 assetId；清除跨项目临时/签名 URL 字段。"""
    import copy

    out = copy.deepcopy(data)
    url_keys = frozenset(
        {
            "imageUrl",
            "videoUrl",
            "audioUrl",
            "fileUrl",
            "thumbnailUrl",
            "coverUrl",
            "src",
            "url",
        }
    )

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in list(node.keys()):
                value = node[key]
                if key in ("assetId", "asset_id") and not isinstance(value, (dict, list)):
                    old = str(value or "").strip()
                    mapped = id_map.get(old)
                    if mapped:
                        node[key] = mapped
                    else:
                        node.pop(key, None)
                elif key in url_keys and isinstance(value, str):
                    # 禁止残留源项目签名 URL
                    node.pop(key, None)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(out)
    return out


async def copy_assets_batch(
    db: AsyncSession,
    *,
    source_project_id: str,
    target_project_id: str,
    asset_ids: list[str],
    source_folder: str | None,
    target_folder: str | None,
) -> dict[str, str]:
    """批量拷贝素材，返回 old→new id 映射；已映射的跳过。"""
    id_map: dict[str, str] = {}
    for asset_id in asset_ids:
        if asset_id in id_map:
            continue
        new_id = await copy_asset_to_project(
            db,
            source_project_id=source_project_id,
            target_project_id=target_project_id,
            asset_id=asset_id,
            source_folder=source_folder,
            target_folder=target_folder,
        )
        id_map[asset_id] = new_id
    await invalidate_manifest_cache(target_project_id)
    return id_map
