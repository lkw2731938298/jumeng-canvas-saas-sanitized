"""Prompt tool config v2 storage and migration from v1 templates."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..integrations.oss.canvas_storage import PROJECT_ROOT
from .prompt_render import (
    DEFAULT_E_FORMAT,
    DEFAULT_H_FORMAT,
    LEGACY_E_FORMATS,
    LEGACY_H_FORMATS,
)

LEGACY_BASE_TEXTS = frozenset({"同一主体多角度展示，视角一致"})
LEGACY_CONSISTENCY_TEXTS = frozenset(
    {
        "保持与原图相同的主体、服装、场景布局与风格，仅改变摄像机机位",
        "保持主体人物、服装、场景与风格一致，摄像机绕主体水平旋转并调整俯仰后重新拍摄",
    }
)

logger = logging.getLogger(__name__)

BUNDLED_DEFAULT_PATH = Path(__file__).with_name("prompt_config.default.json")
LEGACY_V1_PATHS = [
    PROJECT_ROOT / "data" / "prompt-templates.json",
    PROJECT_ROOT / "data" / "oss-local" / "prompt-templates.json",
    Path(__file__).with_name("prompt_templates.default.json"),
]

_write_path_cache: Path | None = None


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception as exc:
        logger.warning("Failed to read prompt config from %s: %s", path, exc)
        return None


def _bundled_default() -> dict[str, Any]:
    data = _load_json(BUNDLED_DEFAULT_PATH)
    return deepcopy(data) if data else {"version": 2, "tools": {}}


def migrate_v1_templates(v1: dict[str, Any]) -> dict[str, Any]:
    base = _bundled_default()
    tools: dict[str, Any] = deepcopy(base.get("tools") or {})
    ma = deepcopy(tools.get("multi_angle") or {})
    items = [t for t in (v1.get("templates") or []) if isinstance(t, dict)]

    def pick_content(tool: str, category: str, key: str, fallback: str = "") -> str:
        for item in items:
            if item.get("tool") == tool and item.get("category") == category and str(item.get("key")) == key:
                if item.get("enabled", True):
                    return str(item.get("content") or fallback)
        return fallback

    def pick_label(tool: str, category: str, key: str, fallback: str = "") -> str:
        for item in items:
            if item.get("tool") == tool and item.get("category") == category and str(item.get("key")) == key:
                if item.get("enabled", True):
                    return str(item.get("label") or item.get("content") or fallback)
        return fallback

    joiner = pick_content("multi_angle", "joiner", "default", ma.get("static", {}).get("j", "，"))
    consistency = pick_content(
        "multi_angle",
        "consistency",
        "default",
        ma.get("static", {}).get("c", ""),
    )
    ma.setdefault("static", {})
    ma["static"]["j"] = joiner
    ma["static"]["c"] = consistency

    for lookup_key, category in (("h", "horizontal"), ("e", "elevation"), ("s", "shot")):
        ma.setdefault("lookups", {}).setdefault(lookup_key, {})
        ma.setdefault("labels", {}).setdefault(lookup_key, {})
        defaults_lookup = (base.get("tools", {}).get("multi_angle", {}).get("lookups", {}) or {}).get(lookup_key, {})
        defaults_labels = (base.get("tools", {}).get("multi_angle", {}).get("labels", {}) or {}).get(lookup_key, {})
        keys = set(defaults_lookup.keys()) | {
            str(item.get("key"))
            for item in items
            if item.get("tool") == "multi_angle" and item.get("category") == category
        }
        for key in sorted(keys, key=lambda k: (k not in defaults_lookup, k)):
            content = pick_content("multi_angle", category, key, str(defaults_lookup.get(key, "")))
            label = pick_label("multi_angle", category, key, str(defaults_labels.get(key, content)))
            if content:
                ma["lookups"][lookup_key][key] = content
            if label:
                ma["labels"][lookup_key][key] = label

    ma.setdefault("kind", "composer")
    ma.setdefault("template", "{base} → $h → $e → $s → $c → {extra?}")
    ma.setdefault("formats", deepcopy(base.get("tools", {}).get("multi_angle", {}).get("formats", {})))
    tools["multi_angle"] = ma

    for tool_id in (
        "panorama",
        "lighting",
        "grid_9",
        "grid_split",
        "outpaint",
        "cutout",
        "hd_upscale",
        "hd_upscale_video",
        "portrait_adjust",
        "emotion_adjust",
        "video_subject_remove",
        "video_smart_matting",
        "video_subject_edit",
        "video_subject_replace",
    ):
        append = deepcopy(tools.get(tool_id) or {})
        content = pick_content(tool_id, "suffix", "default", append.get("appendText", ""))
        label = pick_label(tool_id, "suffix", "default", append.get("menuLabel", tool_id))
        append["kind"] = "append"
        append["menuLabel"] = label
        append["appendText"] = content
        tools[tool_id] = append

    return {"version": 2, "tools": tools}


def _read_candidates() -> list[Path]:
    return [
        PROJECT_ROOT / "data" / "prompt-config.json",
        PROJECT_ROOT / "data" / "oss-local" / "prompt-config.json",
        BUNDLED_DEFAULT_PATH,
    ]


def _resolve_write_path() -> Path:
    global _write_path_cache
    if _write_path_cache is not None:
        return _write_path_cache

    primary = PROJECT_ROOT / "data" / "prompt-config.json"
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        if not primary.is_file():
            migrated = _try_load_migrated()
            primary.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_path_cache = primary
        return primary
    except OSError as exc:
        logger.warning("Cannot write prompt config to %s: %s", primary, exc)

    fallback = PROJECT_ROOT / "data" / "oss-local" / "prompt-config.json"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    _write_path_cache = fallback
    return fallback


def _try_load_migrated() -> dict[str, Any]:
    for path in _read_candidates():
        data = _load_json(path)
        if data and data.get("version") == 2 and isinstance(data.get("tools"), dict):
            return deepcopy(data)

    for path in LEGACY_V1_PATHS:
        data = _load_json(path)
        if data and isinstance(data.get("templates"), list):
            logger.info("Migrating prompt templates v1 from %s to v2", path)
            return migrate_v1_templates(data)

    return _bundled_default()


def _deep_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = deepcopy(value) if isinstance(value, dict) else value
    return out


def _normalize_composer_tool(tool_cfg: dict[str, Any]) -> dict[str, Any]:
    if tool_cfg.get("kind") != "composer":
        return tool_cfg
    out = deepcopy(tool_cfg)
    defaults = (_bundled_default().get("tools") or {}).get("multi_angle") or {}
    default_formats = defaults.get("formats") if isinstance(defaults.get("formats"), dict) else {}
    formats = out.get("formats") if isinstance(out.get("formats"), dict) else {}
    h_fmt = str(formats.get("h") or "").strip()
    e_fmt = str(formats.get("e") or "").strip()
    if h_fmt in LEGACY_H_FORMATS or (h_fmt and "{azimuthDesc}" not in h_fmt):
        h_fmt = str(default_formats.get("h") or DEFAULT_H_FORMAT)
    if e_fmt in LEGACY_E_FORMATS or (e_fmt and "{elevationDesc}" not in e_fmt):
        e_fmt = str(default_formats.get("e") or DEFAULT_E_FORMAT)
    out["formats"] = {
        "h": h_fmt or str(default_formats.get("h") or DEFAULT_H_FORMAT),
        "e": e_fmt or str(default_formats.get("e") or DEFAULT_E_FORMAT),
    }
    default_labels = defaults.get("labels") if isinstance(defaults.get("labels"), dict) else {}
    labels = out.get("labels") if isinstance(out.get("labels"), dict) else {}
    merged_labels = dict(labels)
    for axis in ("h", "e", "s"):
        axis_defaults = default_labels.get(axis) if isinstance(default_labels.get(axis), dict) else {}
        axis_labels = merged_labels.get(axis) if isinstance(merged_labels.get(axis), dict) else {}
        merged_labels[axis] = {**axis_defaults, **axis_labels}
    out["labels"] = merged_labels

    default_lookups = defaults.get("lookups") if isinstance(defaults.get("lookups"), dict) else {}
    lookups = out.get("lookups") if isinstance(out.get("lookups"), dict) else {}
    merged_lookups = dict(lookups)
    for axis in ("h", "e", "s"):
        axis_defaults = default_lookups.get(axis) if isinstance(default_lookups.get(axis), dict) else {}
        axis_lookups = merged_lookups.get(axis) if isinstance(merged_lookups.get(axis), dict) else {}
        merged_lookups[axis] = {**axis_defaults, **axis_lookups}
    out["lookups"] = merged_lookups

    default_static = defaults.get("static") if isinstance(defaults.get("static"), dict) else {}
    static = out.get("static") if isinstance(out.get("static"), dict) else {}
    merged_static = {**default_static, **static}
    base_text = str(merged_static.get("base") or "").strip()
    c_text = str(merged_static.get("c") or "").strip()
    if base_text in LEGACY_BASE_TEXTS:
        merged_static["base"] = default_static.get("base")
    if c_text in LEGACY_CONSISTENCY_TEXTS:
        merged_static["c"] = default_static.get("c")
    out["static"] = merged_static
    return out


def _normalize_lighting_tool(tool_cfg: dict[str, Any]) -> dict[str, Any]:
    if tool_cfg.get("kind") != "composer":
        bundled = (_bundled_default().get("tools") or {}).get("lighting") or {}
        if isinstance(bundled, dict) and bundled.get("kind") == "composer":
            return deepcopy(bundled)
        return tool_cfg
    out = deepcopy(tool_cfg)
    defaults = (_bundled_default().get("tools") or {}).get("lighting") or {}
    default_labels = defaults.get("labels") if isinstance(defaults.get("labels"), dict) else {}
    labels = out.get("labels") if isinstance(out.get("labels"), dict) else {}
    merged_labels = dict(labels)
    dir_defaults = default_labels.get("dir") if isinstance(default_labels.get("dir"), dict) else {}
    dir_labels = merged_labels.get("dir") if isinstance(merged_labels.get("dir"), dict) else {}
    merged_labels["dir"] = {**dir_defaults, **dir_labels}
    out["labels"] = merged_labels

    default_lookups = defaults.get("lookups") if isinstance(defaults.get("lookups"), dict) else {}
    lookups = out.get("lookups") if isinstance(out.get("lookups"), dict) else {}
    merged_lookups = dict(lookups)
    for axis in ("dir", "rim", "smart"):
        axis_defaults = default_lookups.get(axis) if isinstance(default_lookups.get(axis), dict) else {}
        axis_lookups = merged_lookups.get(axis) if isinstance(merged_lookups.get(axis), dict) else {}
        merged_lookups[axis] = {**axis_defaults, **axis_lookups}
    out["lookups"] = merged_lookups

    default_formats = defaults.get("formats") if isinstance(defaults.get("formats"), dict) else {}
    formats = out.get("formats") if isinstance(out.get("formats"), dict) else {}
    out["formats"] = {**default_formats, **formats}

    default_static = defaults.get("static") if isinstance(defaults.get("static"), dict) else {}
    static = out.get("static") if isinstance(out.get("static"), dict) else {}
    out["static"] = {**default_static, **static}
    return out


def _load_config_from_files() -> dict[str, Any]:
    for path in _read_candidates():
        data = _load_json(path)
        if not data:
            continue
        if data.get("version") == 2 and isinstance(data.get("tools"), dict):
            tools = dict(data.get("tools") or {})
            if isinstance(tools.get("multi_angle"), dict):
                tools["multi_angle"] = _normalize_composer_tool(tools["multi_angle"])
            if isinstance(tools.get("lighting"), dict):
                tools["lighting"] = _normalize_lighting_tool(tools["lighting"])
            return deepcopy({**data, "tools": tools})
        if isinstance(data.get("templates"), list):
            return migrate_v1_templates(data)
    loaded = _try_load_migrated()
    if isinstance((loaded.get("tools") or {}).get("multi_angle"), dict):
        tools = dict(loaded.get("tools") or {})
        tools["multi_angle"] = _normalize_composer_tool(tools["multi_angle"])
        if isinstance(tools.get("lighting"), dict):
            tools["lighting"] = _normalize_lighting_tool(tools["lighting"])
        loaded["tools"] = tools
    return loaded


def _ensure_bundled_tools(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """把内置默认里有、当前配置缺失的工具补齐；bundledRevision 升高时覆盖 Prompt 字段。

    返回 (合并后配置, 是否变更)。
    - 缺失工具：整份写入 bundled 默认
    - 已有工具：仅当 bundled 的 bundledRevision > 已存 revision 时，覆盖
      kind/menuLabel/systemPrompt/userPrefix/bundledRevision（保留运营其它自定义键）
    """
    out = deepcopy(config) if isinstance(config, dict) else {"version": 2, "tools": {}}
    tools = dict(out.get("tools") or {})
    bundled_tools = (_bundled_default().get("tools") or {})
    changed = False
    # 随 bundledRevision 同步的 Prompt 核心字段（避免旧 L1 锁死出海文案）
    sync_keys = ("kind", "menuLabel", "systemPrompt", "userPrefix", "bundledRevision", "roleRule", "appendText")
    for tool_id, default_cfg in bundled_tools.items():
        if not isinstance(default_cfg, dict):
            continue
        existing = tools.get(tool_id)
        if not isinstance(existing, dict):
            tools[tool_id] = deepcopy(default_cfg)
            changed = True
            continue
        try:
            bundled_rev = int(default_cfg.get("bundledRevision") or 0)
            existing_rev = int(existing.get("bundledRevision") or 0)
        except (TypeError, ValueError):
            bundled_rev, existing_rev = 0, 0
        if bundled_rev > existing_rev:
            merged = dict(existing)
            for key in sync_keys:
                if key in default_cfg:
                    merged[key] = deepcopy(default_cfg[key])
            tools[tool_id] = merged
            changed = True
    out["version"] = int(out.get("version") or 2)
    out["tools"] = tools
    return out, changed


def load_config() -> dict[str, Any]:
    from .prompt_platform_runtime import get_l1_prompt_config

    l1 = get_l1_prompt_config()
    if l1 is not None:
        merged, _ = _ensure_bundled_tools(l1)
        return merged
    loaded = _load_config_from_files()
    merged, _ = _ensure_bundled_tools(loaded)
    return merged


def _write_config_local(config: dict[str, Any]) -> None:
    payload = {"version": 2, "tools": config.get("tools") or {}}
    path = _resolve_write_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def save_config(config: dict[str, Any]) -> None:
    """保存 v2 配置：本地 mirror + OSS + Redis + L1。"""
    from .prompt_platform_runtime import apply_l1_prompt_config
    from .prompt_platform_storage import persist_prompt_config_shared

    payload = {"version": 2, "tools": config.get("tools") or {}}
    _write_config_local(payload)
    ver = await persist_prompt_config_shared(payload)
    apply_l1_prompt_config(payload, ver=ver)


def _normalize_creative_tools_tool(tool_cfg: dict[str, Any]) -> dict[str, Any]:
    """九宫格创作工具：合并内置 items（按 id），兼容旧版 kind=append。"""
    bundled = (_bundled_default().get("tools") or {}).get("grid_9")
    if not isinstance(bundled, dict):
        bundled = {}
    out = deepcopy(tool_cfg) if isinstance(tool_cfg, dict) else {}
    # 旧配置仅有 appendText：升级为 creative_tools 并补齐默认 items
    if out.get("kind") not in ("creative_tools", "append"):
        out["kind"] = "creative_tools"
    if out.get("kind") == "append" or not out.get("kind"):
        out["kind"] = "creative_tools"
    out["menuLabel"] = str(out.get("menuLabel") or bundled.get("menuLabel") or "九宫格").strip() or "九宫格"
    out["joiner"] = str(out.get("joiner") or bundled.get("joiner") or "，")
    if not str(out.get("appendText") or "").strip():
        out["appendText"] = str(bundled.get("appendText") or "").strip()

    bundled_items = bundled.get("items") if isinstance(bundled.get("items"), list) else []
    bundled_by_id = {
        str(item.get("id")).strip(): deepcopy(item)
        for item in bundled_items
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    existing_items = out.get("items") if isinstance(out.get("items"), list) else []
    existing_by_id: dict[str, dict[str, Any]] = {}
    for item in existing_items:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or "").strip()
        if iid:
            existing_by_id[iid] = item

    merged_items: list[dict[str, Any]] = []
    # 以内置顺序为准，保留运营已改文案
    for iid, base in bundled_by_id.items():
        cur = existing_by_id.pop(iid, None)
        if cur is None:
            merged_items.append(base)
            continue
        row = deepcopy(base)
        if str(cur.get("label") or "").strip():
            row["label"] = str(cur.get("label")).strip()
        if str(cur.get("prompt") or "").strip():
            row["prompt"] = str(cur.get("prompt")).strip()
        if "enabled" in cur:
            row["enabled"] = bool(cur.get("enabled"))
        if cur.get("sortOrder") is not None:
            try:
                row["sortOrder"] = int(cur.get("sortOrder"))
            except (TypeError, ValueError):
                pass
        merged_items.append(row)
    # 额外自定义项（若有）排在后面
    for iid, cur in existing_by_id.items():
        if not str(cur.get("prompt") or "").strip() and not str(cur.get("label") or "").strip():
            continue
        merged_items.append(
            {
                "id": iid,
                "label": str(cur.get("label") or iid).strip(),
                "prompt": str(cur.get("prompt") or "").strip(),
                "enabled": bool(cur.get("enabled", True)),
                "sortOrder": int(cur.get("sortOrder") or 1000),
            }
        )
    merged_items.sort(key=lambda x: (int(x.get("sortOrder") or 0), str(x.get("id") or "")))
    out["items"] = merged_items
    out["kind"] = "creative_tools"
    return out


def get_tool(tool_id: str) -> dict[str, Any] | None:
    tools = load_config().get("tools") or {}
    tool = tools.get(tool_id)
    if not isinstance(tool, dict):
        tool = (_bundled_default().get("tools") or {}).get(tool_id)
    if not isinstance(tool, dict):
        return None
    if tool.get("kind") == "composer":
        if tool_id == "lighting":
            return _normalize_lighting_tool(tool)
        return _normalize_composer_tool(tool)
    if tool_id == "grid_9" or tool.get("kind") == "creative_tools":
        return _normalize_creative_tools_tool(tool)
    # 视觉风格展示图：读取时重签，避免配置里过期预签名导致管理端/画布预览 403
    if tool_id == "visual_style" or tool.get("kind") == "visual_styles":
        from .visual_style import resign_visual_styles_tool_config

        return resign_visual_styles_tool_config(deepcopy(tool))
    return deepcopy(tool)


async def save_tool(tool_id: str, tool_config: dict[str, Any]) -> dict[str, Any]:
    config = load_config()
    tools = dict(config.get("tools") or {})
    existing = tools.get(tool_id) if isinstance(tools.get(tool_id), dict) else {}
    # creative_tools 的 items 以提交体为准整表替换，避免 deep_merge 残留旧项
    if tool_id == "grid_9" or tool_config.get("kind") == "creative_tools":
        merged = deepcopy(existing) if isinstance(existing, dict) else {}
        merged.update(deepcopy(tool_config))
        if isinstance(tool_config.get("items"), list):
            merged["items"] = deepcopy(tool_config["items"])
        merged = _normalize_creative_tools_tool(merged)
    else:
        merged = _deep_merge_dict(existing, tool_config)
        if not merged.get("kind"):
            merged["kind"] = existing.get("kind") or "append"
        if merged.get("kind") == "composer":
            if tool_id == "lighting":
                merged = _normalize_lighting_tool(merged)
            else:
                merged = _normalize_composer_tool(merged)
        # 视觉风格：落库前把预签名换成稳定代理 URL
        if tool_id == "visual_style" or merged.get("kind") == "visual_styles":
            from .visual_style import stabilize_visual_styles_tool_config

            if isinstance(tool_config.get("items"), list):
                merged["items"] = deepcopy(tool_config["items"])
            merged = stabilize_visual_styles_tool_config(merged)
    tools[tool_id] = merged
    config["tools"] = tools
    await save_config(config)
    saved = get_tool(tool_id)
    if not isinstance(saved, dict):
        return deepcopy(merged)
    return saved


async def reset_tool(tool_id: str) -> dict[str, Any]:
    defaults = _bundled_default()
    default_tool = defaults.get("tools", {}).get(tool_id)
    if not isinstance(default_tool, dict):
        raise ValueError(f"No bundled default for tool '{tool_id}'")
    return await save_tool(tool_id, deepcopy(default_tool))


def list_tool_ids() -> list[str]:
    tools = load_config().get("tools") or {}
    return sorted(str(key) for key in tools.keys())
