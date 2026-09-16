"""Generation presets — 开源空壳。

无预置型号 UI 选项；保留合成 / 规范化 API，供管理后台自行配置后的运行时使用。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def default_presets_for_model(name: str, provider: str, category: str) -> dict[str, Any] | None:
    return None


def default_parameters_for_model(name: str, provider: str, category: str) -> dict[str, Any]:
    return {}


def _find_item(group: dict[str, Any], item_id: str | None) -> dict[str, Any] | None:
    items = group.get("items") or []
    if item_id:
        for item in items:
            if item.get("id") == item_id and item.get("enabled", True):
                return item
    default_id = group.get("defaultId")
    if default_id:
        for item in items:
            if item.get("id") == default_id and item.get("enabled", True):
                return item
    for item in items:
        if item.get("enabled", True):
            return item
    return None


def normalize_option_ids(
    presets: dict[str, Any] | None,
    option_ids: dict[str, str] | None,
) -> dict[str, str]:
    if not presets:
        return {}
    groups = presets.get("groups") or []
    normalized: dict[str, str] = {}
    incoming = option_ids or {}
    for group in groups:
        gid = group.get("id")
        if not gid:
            continue
        item = _find_item(group, incoming.get(gid))
        if item and item.get("id"):
            normalized[gid] = item["id"]
    return normalized


def compose_generation(
    user_prompt: str,
    presets: dict[str, Any] | None,
    option_ids: dict[str, str] | None,
) -> tuple[str, dict[str, Any]]:
    prompt = user_prompt.strip()
    if not presets:
        return prompt, {}

    groups = {g["id"]: g for g in (presets.get("groups") or []) if g.get("id")}
    normalized = normalize_option_ids(presets, option_ids)
    rules = presets.get("composeRules") or {}
    joiner = rules.get("promptJoiner") or "，"
    api_priority: list[str] = rules.get("apiMergePriority") or ["size", "quality", "aspect"]

    selected_by_group: dict[str, dict[str, Any]] = {}
    for gid, iid in normalized.items():
        group = groups.get(gid)
        if not group:
            continue
        item = _find_item(group, iid)
        if item:
            selected_by_group[gid] = item

    prompt_parts = [prompt] if prompt else []
    for group in presets.get("groups") or []:
        gid = group.get("id")
        if not gid:
            continue
        item = selected_by_group.get(gid)
        if not item:
            continue
        suffix = (item.get("promptSuffix") or "").strip()
        if suffix:
            prompt_parts.append(suffix)

    api_params: dict[str, Any] = {}
    ordered_groups = sorted(
        selected_by_group.keys(),
        key=lambda g: api_priority.index(g) if g in api_priority else 999,
    )
    for gid in ordered_groups:
        item = selected_by_group[gid]
        api = item.get("api") or {}
        if not isinstance(api, dict):
            continue
        for key, value in api.items():
            if key == "size" and "size" in api_params:
                continue
            api_params[key] = value

    final_prompt = joiner.join(p for p in prompt_parts if p)
    return final_prompt, api_params


def public_presets(presets: dict[str, Any] | None) -> dict[str, Any] | None:
    if not presets:
        return None
    out = deepcopy(presets)
    groups = []
    for group in out.get("groups") or []:
        items = [i for i in (group.get("items") or []) if i.get("enabled", True)]
        items.sort(key=lambda x: x.get("sortOrder", 0))
        if not items:
            continue
        g = {**group, "items": items}
        if g.get("defaultId") not in {i.get("id") for i in items}:
            g["defaultId"] = items[0].get("id")
        groups.append(g)
    out["groups"] = groups
    return out
