from fastapi import APIRouter, Query

from ...schemas.prompt_templates import PromptTemplateListOut, PromptTemplateOut
from ...services import prompt_config as cfg

router = APIRouter()


def _composer_to_v1(tool: str, tool_cfg: dict) -> list[dict]:
    items: list[dict] = []
    static = tool_cfg.get("static") if isinstance(tool_cfg.get("static"), dict) else {}
    lookups = tool_cfg.get("lookups") if isinstance(tool_cfg.get("lookups"), dict) else {}
    labels = tool_cfg.get("labels") if isinstance(tool_cfg.get("labels"), dict) else {}

    items.append(
        {
            "id": f"{tool}_joiner",
            "tool": tool,
            "category": "joiner",
            "key": "default",
            "label": "拼接符",
            "content": static.get("j", "，"),
            "enabled": True,
            "sort_order": 0,
        }
    )
    items.append(
        {
            "id": f"{tool}_consistency",
            "tool": tool,
            "category": "consistency",
            "key": "default",
            "label": "一致性约束",
            "content": static.get("c", ""),
            "enabled": True,
            "sort_order": 0,
        }
    )

    category_map = {"h": "horizontal", "e": "elevation", "s": "shot"}
    for lookup_key, category in category_map.items():
        lookup = lookups.get(lookup_key) or {}
        label_table = labels.get(lookup_key) or {}
        if not isinstance(lookup, dict):
            continue
        for index, (key, content) in enumerate(sorted(lookup.items(), key=lambda kv: str(kv[0]))):
            items.append(
                {
                    "id": f"{tool}_{category}_{key}",
                    "tool": tool,
                    "category": category,
                    "key": str(key),
                    "label": str(label_table.get(str(key)) or content),
                    "content": str(content),
                    "enabled": True,
                    "sort_order": index * 10,
                }
            )
    return items


def _append_to_v1(tool: str, tool_cfg: dict) -> list[dict]:
    return [
        {
            "id": f"{tool}_suffix",
            "tool": tool,
            "category": "suffix",
            "key": "default",
            "label": str(tool_cfg.get("menuLabel") or tool_cfg.get("menu_label") or tool),
            "content": str(tool_cfg.get("appendText") or tool_cfg.get("append_text") or ""),
            "enabled": True,
            "sort_order": 0,
        }
    ]


def _creative_tools_to_v1(tool: str, tool_cfg: dict) -> list[dict]:
    """九宫格创作工具：每项提示词映射为一条 v1 模板（兼容旧列表接口）。"""
    items: list[dict] = []
    items.extend(_append_to_v1(tool, tool_cfg))
    raw_items = tool_cfg.get("items") if isinstance(tool_cfg.get("items"), list) else []
    for index, row in enumerate(raw_items):
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("id") or "").strip()
        if not item_id:
            continue
        items.append(
            {
                "id": f"{tool}_{item_id}",
                "tool": tool,
                "category": "creative_tool",
                "key": item_id,
                "label": str(row.get("label") or item_id),
                "content": str(row.get("prompt") or ""),
                "enabled": bool(row.get("enabled", True)),
                "sort_order": int(row.get("sortOrder") or index * 10),
            }
        )
    return items


def _tool_to_v1_templates(tool: str, tool_cfg: dict) -> list[dict]:
    kind = str(tool_cfg.get("kind") or "append")
    if kind == "composer":
        return _composer_to_v1(tool, tool_cfg)
    if kind == "creative_tools":
        return _creative_tools_to_v1(tool, tool_cfg)
    return _append_to_v1(tool, tool_cfg)


def _out(raw: dict) -> PromptTemplateOut:
    return PromptTemplateOut(
        id=str(raw.get("id") or ""),
        tool=str(raw.get("tool") or ""),
        category=str(raw.get("category") or ""),
        key=str(raw.get("key") or ""),
        label=str(raw.get("label") or ""),
        content=str(raw.get("content") or ""),
        enabled=bool(raw.get("enabled", True)),
        sort_order=int(raw.get("sort_order") or 0),
    )


@router.get("", response_model=PromptTemplateListOut)
async def list_public_prompt_templates(
    tool: str = Query("multi_angle", description="Tool key, e.g. multi_angle"),
):
    from ...services.prompt_platform_runtime import ensure_prompt_platform_fresh

    await ensure_prompt_platform_fresh()
    tool_cfg = cfg.get_tool(tool)
    if not tool_cfg:
        return PromptTemplateListOut(tool=tool, templates=[])
    items = _tool_to_v1_templates(tool, tool_cfg)
    return PromptTemplateListOut(
        tool=tool,
        templates=[_out(item) for item in items if item.get("enabled", True)],
    )
