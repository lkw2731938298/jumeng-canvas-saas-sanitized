"""项目级素材索引读写（OSS assets/index.json）。"""

from __future__ import annotations

import asyncio
import re
from typing import Iterable

from ..integrations.oss.canvas_storage import get_canvas_storage

ASSETS_INDEX = "assets/index.json"
_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


def storage_folder_to_uuid(folder: str) -> str:
    """将 32 位十六进制 storage_folder 转为带连字符的 UUID（兼容历史 OSS 路径）。"""
    hexed = folder.replace("-", "").lower()
    if not _HEX32.match(hexed):
        return folder
    return f"{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:]}"


def _load_index_at_folder(project_id: str, folder: str) -> list[dict]:
    storage = get_canvas_storage()
    data = storage.read_json(project_id, ASSETS_INDEX, storage_folder=folder)
    return data if isinstance(data, list) else []


def load_index(project_id: str, *, storage_folder: str) -> list[dict]:
    """读取项目素材索引 JSON 列表。"""
    folder = str(storage_folder or "").strip()
    if not folder:
        return []
    items = _load_index_at_folder(project_id, folder)
    if items:
        return items
    dashed = storage_folder_to_uuid(folder)
    if dashed != folder:
        return _load_index_at_folder(project_id, dashed)
    return []


def save_index(project_id: str, items: list[dict], *, storage_folder: str) -> None:
    """写入项目素材索引 JSON 列表。"""
    get_canvas_storage().write_json(
        project_id,
        ASSETS_INDEX,
        items,
        storage_folder=storage_folder,
    )


def index_by_id(items: Iterable[dict]) -> dict[str, dict]:
    """将索引列表转为 assetId → 记录 的字典。"""
    out: dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        asset_id = str(item.get("id") or "")
        if asset_id:
            out[asset_id] = item
    return out


def find_asset(items: list[dict], asset_id: str) -> dict | None:
    """在索引列表中按 assetId 查找单条记录。"""
    return index_by_id(items).get(asset_id)


def find_assets(items: list[dict], asset_ids: list[str]) -> list[dict]:
    """在索引列表中批量查找多条 assetId 记录。"""
    lookup = index_by_id(items)
    found: list[dict] = []
    for asset_id in asset_ids:
        record = lookup.get(asset_id)
        if record:
            found.append(record)
    return found


async def aload_index(project_id: str, *, storage_folder: str) -> list[dict]:
    """异步读取项目素材索引（线程池包装）。"""
    return await asyncio.to_thread(load_index, project_id, storage_folder=storage_folder)


async def asave_index(project_id: str, items: list[dict], *, storage_folder: str) -> None:
    """异步写入项目素材索引（线程池包装）。"""
    await asyncio.to_thread(save_index, project_id, items, storage_folder=storage_folder)
