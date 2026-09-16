"""管理端模型目录 CRUD（创建 / 扩展编辑 / 软删 / 连通性测试）。"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..core.datetime_util import now_cst_naive
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..core.llm_keys import get_llm_keys, is_model_configured, is_provider_configured
from ..core.model_registry import (
    catalog_dict_for_spec,
    get_model_spec,
)
from ..models.job import GenerationJob, Model
from .storage_urls import normalize_browser_storage_url, persistable_media_url
from ..services.credit_pricing import default_pricing_for_model
from ..services.generation_presets import default_parameters_for_model
from ..services.model_availability import is_catalog_model_implemented, provider_group_label
from ..services.model_catalog import _enrich_catalog_item
from ..services.model_catalog_runtime import reload_runtime_model_catalog
from ..services.model_channels import normalize_channels_payload

# 模型 name 创建后不可改：小写字母开头，仅字母数字下划线
_MODEL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

VALID_CATEGORIES = frozenset({"text", "image", "video", "audio", "tool"})
VALID_PROVIDERS = frozenset(
    {
        "doubao",
        "ark",
        "deepseek",
        "dashscope",
        "kling",
        "vidu",
        "runninghub",
        "ltx_runninghub",
        "nodyhub",
        "huahu",
        "jumengai",
        "comfyui",
        "openai",
        "qwen",
        "zhipu",
        "moonshot",
    }
)
VALID_IMPLEMENTATIONS = frozenset({"live", "reserved"})


def _normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def _validate_name(name: str) -> str:
    normalized = _normalize_name(name)
    if not normalized or not _MODEL_NAME_RE.match(normalized):
        fail(ErrorCode.VALIDATION_ERROR, message="模型标识须以小写字母开头，仅含字母、数字、下划线")
    return normalized


def _validate_category(category: str) -> str:
    cat = (category or "").strip().lower()
    if cat not in VALID_CATEGORIES:
        fail(ErrorCode.VALIDATION_ERROR, message=f"无效分类: {category}")
    return cat


def _validate_provider(provider: str) -> str:
    prov = (provider or "").strip().lower()
    if prov not in VALID_PROVIDERS:
        fail(ErrorCode.VALIDATION_ERROR, message=f"无效供应商: {provider}")
    return prov


def _merge_parameters_metadata(
    params: dict[str, Any],
    *,
    upstream_model: str | None = None,
    capabilities: list[str] | None = None,
    implementation: str | None = None,
    channels: list[dict[str, Any]] | None = None,
    catalog_name: str,
    provider: str,
) -> dict[str, Any]:
    """合并运营可编辑的 parameters 元数据字段。"""
    merged = dict(params)
    if upstream_model is not None:
        merged["upstreamModel"] = upstream_model.strip()
    if capabilities is not None:
        merged["capabilities"] = list(capabilities)
    if implementation is not None:
        impl = implementation.strip().lower()
        if impl not in VALID_IMPLEMENTATIONS:
            fail(ErrorCode.VALIDATION_ERROR, message="implementation 须为 live 或 reserved")
        merged["implementation"] = impl
    if channels is not None:
        try:
            merged["channels"] = normalize_channels_payload(channels, catalog_name=catalog_name)
        except ValueError as exc:
            fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    merged["providerGroup"] = provider_group_label(provider)
    return merged


async def _assert_channel_model_names_exist(
    db: AsyncSession,
    channels: list[dict[str, Any]] | None,
    *,
    allow_names: set[str] | None = None,
) -> None:
    """校验通道引用的目录模型 name 均存在且未软删（allow_names 用于新建时放行自身）。"""
    if not channels:
        return
    names: set[str] = set()
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("modelName") or entry.get("model_name") or "").strip()
        if name:
            names.add(name)
    allowed = allow_names or set()
    names -= allowed
    if not names:
        return
    result = await db.execute(select(Model).filter(Model.name.in_(list(names))))
    rows = list(result.scalars().all())
    found = {
        row.name
        for row in rows
        if not (isinstance(row.parameters, dict) and row.parameters.get("adminSoftDeleted"))
    }
    missing = sorted(names - found)
    if missing:
        fail(ErrorCode.VALIDATION_ERROR, message=f"通道引用的模型不存在: {', '.join(missing)}")


def _build_parameters_for_new_model(
    *,
    name: str,
    provider: str,
    category: str,
    parameters: dict[str, Any] | None,
    upstream_model: str | None,
    capabilities: list[str] | None,
    implementation: str | None,
    channels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """新建模型 parameters：registry 同名则继承种子，否则用请求体 + 默认定价。"""
    spec = get_model_spec(name)
    if spec:
        base = catalog_dict_for_spec(spec)
        enriched = _enrich_catalog_item(name, base)
        params = dict(enriched.get("parameters") or {})
    else:
        defaults = default_parameters_for_model(name, provider, category) or {}
        params = dict(defaults)
        meta = {
            "upstreamModel": (upstream_model or "").strip(),
            "capabilities": list(capabilities or []),
            "implementation": (implementation or "reserved").strip().lower(),
            "providerGroup": provider_group_label(provider),
        }
        params = {**params, **meta}
        presets = params.get("generationPresets")
        presets_dict = presets if isinstance(presets, dict) else None
        if not params.get("pricing"):
            params["pricing"] = default_pricing_for_model(category, presets_dict)

    if parameters:
        params = {**params, **parameters}

    return _merge_parameters_metadata(
        params,
        upstream_model=upstream_model,
        capabilities=capabilities,
        implementation=implementation or params.get("implementation"),
        channels=channels,
        catalog_name=name,
        provider=provider,
    )


async def create_admin_model(
    db: AsyncSession,
    *,
    name: str,
    display_name: str,
    provider: str,
    model_type: str,
    category: str,
    description: str | None = None,
    sort_order: int = 0,
    is_available: bool = False,
    cover_url: str | None = None,
    parameters: dict[str, Any] | None = None,
    upstream_model: str | None = None,
    capabilities: list[str] | None = None,
    implementation: str | None = None,
    channels: list[dict[str, Any]] | None = None,
) -> Model:
    """POST 新建模型；name 全局唯一且创建后不可修改。"""
    normalized_name = _validate_name(name)
    prov = _validate_provider(provider)
    cat = _validate_category(category)

    existing = await db.execute(select(Model).filter(Model.name == normalized_name))
    if existing.scalar_one_or_none():
        fail(ErrorCode.CONFLICT, message=f"模型标识 '{normalized_name}' 已存在")

    # 新建时可先写通道；引用本模型 name 合法，其它 name 须已存在
    await _assert_channel_model_names_exist(
        db, channels, allow_names={normalized_name}
    )

    display = (display_name or normalized_name).strip() or normalized_name
    merged_params = _build_parameters_for_new_model(
        name=normalized_name,
        provider=prov,
        category=cat,
        parameters=parameters,
        upstream_model=upstream_model,
        capabilities=capabilities,
        implementation=implementation,
        channels=channels,
    )

    model = Model(
        name=normalized_name,
        display_name=display,
        provider=prov,
        model_type=(model_type or "checkpoint").strip() or "checkpoint",
        category=cat,
        description=(description or "").strip() or None,
        cover_url=persistable_media_url(cover_url),
        parameters=merged_params,
        is_available=is_available,
        sort_order=int(sort_order or 0),
    )
    db.add(model)
    await db.flush()
    await reload_runtime_model_catalog(db)
    return model


async def patch_admin_model_fields(
    db: AsyncSession,
    model: Model,
    *,
    is_available: bool | None = None,
    sort_order: int | None = None,
    display_name: str | None = None,
    provider: str | None = None,
    model_type: str | None = None,
    category: str | None = None,
    description: str | None = None,
    cover_url: str | None = None,
    parameters: dict[str, Any] | None = None,
    generation_presets: dict[str, Any] | None = None,
    upstream_model: str | None = None,
    capabilities: list[str] | None = None,
    implementation: str | None = None,
    channels: list[dict[str, Any]] | None = None,
) -> Model:
    """PATCH 扩展字段（禁止修改 name）。"""
    if is_available is not None:
        model.is_available = is_available
    if sort_order is not None:
        model.sort_order = sort_order
    if display_name is not None:
        model.display_name = display_name.strip() or model.name
    if provider is not None:
        model.provider = _validate_provider(provider)
    if model_type is not None:
        model.model_type = model_type.strip() or model.model_type
    if category is not None:
        model.category = _validate_category(category)
    if description is not None:
        model.description = description.strip() or None
    if cover_url is not None:
        model.cover_url = persistable_media_url(cover_url)

    params = dict(model.parameters or {}) if isinstance(model.parameters, dict) else {}
    changed_params = False

    if generation_presets is not None:
        params["generationPresets"] = generation_presets
        changed_params = True
    elif parameters is not None:
        params = {**params, **parameters}
        changed_params = True

    if channels is not None:
        await _assert_channel_model_names_exist(db, channels)

    if any(v is not None for v in (upstream_model, capabilities, implementation, channels)):
        params = _merge_parameters_metadata(
            params,
            upstream_model=upstream_model,
            capabilities=capabilities,
            implementation=implementation,
            channels=channels,
            catalog_name=model.name,
            provider=model.provider,
        )
        changed_params = True

    if changed_params:
        model.parameters = params
        flag_modified(model, "parameters")

    await db.flush()
    await reload_runtime_model_catalog(db)
    return model


async def soft_delete_admin_model(db: AsyncSession, model: Model) -> Model:
    """软删：停用并从画布隐藏；不删除 DB 行（保留历史任务引用）。"""
    params = dict(model.parameters or {}) if isinstance(model.parameters, dict) else {}
    if params.get("adminSoftDeleted"):
        return model

    params["adminSoftDeleted"] = True
    params["adminSoftDeletedAt"] = now_cst_naive().isoformat()
    model.parameters = params
    model.is_available = False
    flag_modified(model, "parameters")
    await db.flush()
    await reload_runtime_model_catalog(db)
    return model


async def count_generation_jobs_for_model(db: AsyncSession, model_name: str) -> int:
    """统计引用该模型名的历史生成任务数。"""
    result = await db.execute(
        select(func.count()).select_from(GenerationJob).filter(GenerationJob.model == model_name)
    )
    return int(result.scalar_one() or 0)


async def hard_delete_admin_model(db: AsyncSession, model: Model) -> None:
    """硬删：仅当无历史任务时允许（管理端显式硬删接口暂不提供，仅供内部校验）。"""
    job_count = await count_generation_jobs_for_model(db, model.name)
    if job_count > 0:
        fail(
            ErrorCode.CONFLICT,
            message=f"该模型已有 {job_count} 条生成任务记录，仅可软删（停用）",
        )
    await db.delete(model)
    await db.flush()
    await reload_runtime_model_catalog(db)


def evaluate_model_connectivity(model: Model) -> dict[str, Any]:
    """连通性测试：校验密钥与接入状态（不调用上游 HTTP）。"""
    configured = is_model_configured(model.name) or is_provider_configured(
        model.provider, model_id=model.name
    )
    implemented = is_catalog_model_implemented(model)

    if model.provider == "comfyui":
        from ..core.config import get_settings

        ok = bool(get_settings().comfyui_base_url.strip())
        return {
            "ok": ok,
            "message": "ComfyUI 地址已配置" if ok else "ComfyUI 地址未配置",
            "is_configured": ok,
            "is_implemented": implemented,
        }

    if not configured:
        return {
            "ok": False,
            "message": "供应商 API 密钥未配置，请先在「供应商密钥」中配置",
            "is_configured": False,
            "is_implemented": implemented,
        }

    params = model.parameters if isinstance(model.parameters, dict) else {}
    impl = params.get("implementation", "live")
    spec = get_model_spec(model.name)
    if spec is None and impl != "live":
        return {
            "ok": False,
            "message": "模型为 reserved 状态，尚未接入上游 dispatch",
            "is_configured": True,
            "is_implemented": False,
        }

    try:
        if spec:
            from ..core.llm_keys import get_model_credentials

            get_model_credentials(model.name)
        else:
            cfg = get_llm_keys()
            from ..core.llm_keys import _provider_keys

            creds = _provider_keys(cfg, model.provider, model_id=model.name)
            if not creds.api_key:
                raise ValueError("missing api key")
    except ValueError as exc:
        return {
            "ok": False,
            "message": str(exc),
            "is_configured": False,
            "is_implemented": implemented,
        }

    if not implemented:
        return {
            "ok": False,
            "message": "密钥已配置，但模型尚未标记为可接入（implementation=live 且 dispatch 支持）",
            "is_configured": True,
            "is_implemented": False,
        }

    return {
        "ok": True,
        "message": "密钥可读且模型可接入",
        "is_configured": True,
        "is_implemented": True,
    }
