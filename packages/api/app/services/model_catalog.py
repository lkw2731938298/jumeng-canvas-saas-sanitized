"""Default model catalog — seeded from core.model_registry."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..core.model_registry import (
    CANVAS_MODEL_SPECS,
    catalog_dict_for_spec,
    parameters_metadata_for_spec,
)
from ..models.job import Model
from .credit_pricing import default_pricing_for_model
from .generation_presets import default_parameters_for_model
from .model_catalog_runtime import reload_runtime_model_catalog


def _enrich_catalog_item(spec_name: str, base: dict) -> dict:
    spec = next(s for s in CANVAS_MODEL_SPECS if s.name == spec_name)
    params = default_parameters_for_model(spec.name, spec.provider, spec.category)
    meta = parameters_metadata_for_spec(spec)
    if params:
        merged = {**params, **meta}
    else:
        merged = meta
    presets = merged.get("generationPresets")
    presets_dict = presets if isinstance(presets, dict) else None
    if not merged.get("pricing"):
        merged["pricing"] = default_pricing_for_model(spec.category, presets_dict)
    return {**base, "parameters": merged}


def _ensure_model_pricing(model: Model) -> bool:
    """Persist default pricing when parameters.pricing is missing."""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    if params.get("pricing"):
        return False
    presets = params.get("generationPresets")
    presets_dict = presets if isinstance(presets, dict) else None
    merged = dict(params)
    merged["pricing"] = default_pricing_for_model(model.category, presets_dict)
    model.parameters = merged
    flag_modified(model, "parameters")
    return True


async def sync_model_catalog(db: AsyncSession) -> dict[str, int]:
    """Insert catalog entries missing from DB; backfill presets, metadata, and pricing.

    已有行的 ``display_name`` / ``description`` 以管理后台为准，启动同步不再用
    ``model_registry`` 覆盖（避免部署/重启冲掉后台改名）。新建模型仍用注册表默认值。
    """
    result = await db.execute(select(Model))
    all_models = list(result.scalars().all())
    existing_models = {m.name: m for m in all_models}
    added = 0
    updated = 0

    for spec in CANVAS_MODEL_SPECS:
        base = catalog_dict_for_spec(spec)
        item = _enrich_catalog_item(spec.name, base)

        if spec.name not in existing_models:
            # 官方稳定副模型默认不对用户目录展示，仅供通道托底
            available = not spec.name.endswith("_official")
            db.add(Model(**item, is_available=available))
            added += 1
            continue

        model = existing_models[spec.name]
        params = model.parameters if isinstance(model.parameters, dict) else {}
        incoming = item.get("parameters") or {}
        merged = dict(params)
        changed = False

        # 展示名/简介：后台可改，禁止 registry 覆盖；仅空值时补默认
        for field in ("display_name", "description"):
            current = getattr(model, field)
            if (current is None or not str(current).strip()) and item.get(field):
                setattr(model, field, item[field])
                changed = True

        # provider/类型/分类/排序仍跟注册表（运维字段，非展示文案）
        # 分类须同步：如 NodyHub SD2.0 从误标 image 纠正为 video
        for field in ("provider", "model_type", "category", "sort_order"):
            if item.get(field) is not None and getattr(model, field) != item.get(field):
                setattr(model, field, item[field])
                changed = True

        incoming_presets = incoming.get("generationPresets")
        stored_presets = params.get("generationPresets")
        if incoming_presets and (
            not stored_presets
            or (incoming_presets.get("version") or 0) > ((stored_presets or {}).get("version") or 0)
        ):
            merged["generationPresets"] = incoming_presets
            changed = True

        for key in (
            "upstreamModel",
            "capabilities",
            "videoMode",
            "implementation",
            "compatible_node_types",
            "providerGroup",
            "runninghub_route",
            "use_ltx_key",
            "rh_llm",
            "bailian_compatible",
            "channels",
            "videoYuanPerSecond",
            "upstreamCost",
        ):
            if incoming.get(key) is not None and merged.get(key) != incoming.get(key):
                merged[key] = incoming[key]
                changed = True

        # 定价：缺失时补默认；registry 显式 version 更高时覆盖（与 generationPresets 同策略）
        incoming_pricing = incoming.get("pricing")
        stored_pricing = merged.get("pricing")
        if isinstance(incoming_pricing, dict) and (
            not isinstance(stored_pricing, dict)
            or int(incoming_pricing.get("version") or 0) > int(stored_pricing.get("version") or 0)
        ):
            merged["pricing"] = incoming_pricing
            changed = True
        elif not merged.get("pricing"):
            presets_for_pricing = merged.get("generationPresets")
            presets_dict = presets_for_pricing if isinstance(presets_for_pricing, dict) else None
            merged["pricing"] = default_pricing_for_model(spec.category, presets_dict)
            changed = True

        if changed:
            model.parameters = merged
            flag_modified(model, "parameters")
            updated += 1

    for model in all_models:
        if _ensure_model_pricing(model):
            updated += 1

    if added or updated:
        await db.flush()
        await reload_runtime_model_catalog(db)
    return {"added": added, "updated": updated}
