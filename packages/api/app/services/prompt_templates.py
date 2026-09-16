"""Prompt template storage (JSON file) for canvas tools like multi_angle."""

from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..integrations.oss.canvas_storage import PROJECT_ROOT

logger = logging.getLogger(__name__)

BUNDLED_DEFAULT_PATH = Path(__file__).with_name("prompt_templates.default.json")

DEFAULT_STORE: dict[str, Any] = {
    "version": 1,
    "templates": [],
}

_write_path_cache: Path | None = None


def _read_candidates() -> list[Path]:
    return [
        PROJECT_ROOT / "data" / "prompt-templates.json",
        PROJECT_ROOT / "data" / "oss-local" / "prompt-templates.json",
        BUNDLED_DEFAULT_PATH,
    ]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("templates"), list):
            return raw
    except Exception as exc:
        logger.warning("Failed to read prompt templates from %s: %s", path, exc)
    return None


def _load_store_from_files() -> dict[str, Any]:
    for path in _read_candidates():
        data = _load_json(path)
        if data:
            return deepcopy(data)
    if BUNDLED_DEFAULT_PATH.is_file():
        data = _load_json(BUNDLED_DEFAULT_PATH)
        if data:
            return deepcopy(data)
    return deepcopy(DEFAULT_STORE)


def load_store() -> dict[str, Any]:
    from .prompt_platform_runtime import get_l1_prompt_templates

    l1 = get_l1_prompt_templates()
    if l1 is not None:
        return deepcopy(l1)
    return _load_store_from_files()


def _resolve_write_path() -> Path:
    global _write_path_cache
    if _write_path_cache is not None:
        return _write_path_cache

    primary = PROJECT_ROOT / "data" / "prompt-templates.json"
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        if not primary.is_file() and BUNDLED_DEFAULT_PATH.is_file():
            primary.write_text(BUNDLED_DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        _write_path_cache = primary
        return primary
    except OSError as exc:
        logger.warning("Cannot write prompt templates to %s: %s", primary, exc)

    fallback = PROJECT_ROOT / "data" / "oss-local" / "prompt-templates.json"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    _write_path_cache = fallback
    return fallback


def _write_store_local(store: dict[str, Any]) -> None:
    path = _resolve_write_path()
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def save_store(store: dict[str, Any]) -> None:
    """保存模板：本地 mirror + OSS 权威 + Redis 共享缓存 + 本进程 L1。"""
    from .prompt_platform_runtime import apply_l1_prompt_templates
    from .prompt_platform_storage import persist_prompt_templates_shared

    _write_store_local(store)
    ver = await persist_prompt_templates_shared(store)
    apply_l1_prompt_templates(store, ver=ver)


def list_templates(tool: str | None = None) -> list[dict[str, Any]]:
    store = load_store()
    items = store.get("templates") or []
    if not isinstance(items, list):
        return []

    out = [t for t in items if isinstance(t, dict)]
    if tool:
        out = [t for t in out if t.get("tool") == tool]
    out.sort(
        key=lambda t: (
            str(t.get("category") or ""),
            int(t.get("sort_order") or 0),
            str(t.get("key") or ""),
        )
    )
    return out


def get_template(template_id: str) -> dict[str, Any] | None:
    for item in list_templates():
        if str(item.get("id")) == template_id:
            return item
    return None


async def create_template(payload: dict[str, Any]) -> dict[str, Any]:
    store = load_store()
    items = list(store.get("templates") or [])
    new_item = {
        **payload,
        "id": payload.get("id") or f"pt_{uuid.uuid4().hex[:12]}",
    }
    items.append(new_item)
    store["templates"] = items
    await save_store(store)
    return new_item


async def update_template(template_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    store = load_store()
    items = list(store.get("templates") or [])
    for index, item in enumerate(items):
        if str(item.get("id")) == template_id:
            updated = {**item, **patch, "id": template_id}
            items[index] = updated
            store["templates"] = items
            await save_store(store)
            return updated
    return None


async def delete_template(template_id: str) -> bool:
    store = load_store()
    items = list(store.get("templates") or [])
    filtered = [item for item in items if str(item.get("id")) != template_id]
    if len(filtered) == len(items):
        return False
    store["templates"] = filtered
    await save_store(store)
    return True


async def reset_tool_templates(tool: str) -> list[dict[str, Any]]:
    """Replace all templates for a tool with bundled defaults."""
    bundled = _load_json(BUNDLED_DEFAULT_PATH)
    if not bundled:
        return []
    bundled_items = [
        deepcopy(item)
        for item in (bundled.get("templates") or [])
        if isinstance(item, dict) and item.get("tool") == tool
    ]
    if not bundled_items:
        return []

    store = load_store()
    kept = [
        item
        for item in (store.get("templates") or [])
        if isinstance(item, dict) and item.get("tool") != tool
    ]
    store["templates"] = kept + bundled_items
    await save_store(store)
    return bundled_items
