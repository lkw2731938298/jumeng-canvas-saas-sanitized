from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from ....core.entity_ids import require_entity_id
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_PRICING
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.job import Model
from ....models.user import User
from ....schemas.admin import AdminModelOut
from ....core.model_registry import get_model_spec
from ....services.credit_pricing import (
    default_pricing_for_model,
    extract_pricing_config,
    quote_generation_cost,
)
from ....core.credit_amount import normalize_credit_amount
from ....services.generation_presets import public_presets, normalize_option_ids
from ....services.model_catalog import _ensure_model_pricing
from ....services.model_catalog_runtime import reload_runtime_model_catalog
from ....services.upstream_cost import extract_upstream_cost_config, merge_upstream_cost_patch
from .models import _model_out

router = APIRouter()

_IMAGE_TIER_GROUP_IDS = frozenset({"quality", "resolution", "size"})


def _model_has_image_tier_presets(model: Model) -> bool:
    """预设是否含画质/清晰度档（无则按次基础算力，如悠船 Niji 7）。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    presets = params.get("generationPresets")
    if not isinstance(presets, dict):
        return False
    for group in presets.get("groups") or []:
        if not isinstance(group, dict):
            continue
        if str(group.get("id") or "") in _IMAGE_TIER_GROUP_IDS:
            return True
    return False


class AdminPricingItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model_id: str = Field(..., alias="modelId")
    name: str
    display_name: str = Field(..., alias="displayName")
    category: str
    is_available: bool = Field(..., alias="isAvailable")
    pricing_version: int = Field(..., alias="pricingVersion")
    base_cost: float = Field(..., alias="baseCost")
    mode: str
    min_cost: float = Field(..., alias="minCost")
    primary_group_id: Optional[str] = Field(None, alias="primaryGroupId")
    options: dict[str, dict[str, float]] = Field(default_factory=dict)
    options_with_video_reference: dict[str, dict[str, float]] = Field(
        default_factory=dict, alias="optionsWithVideoReference"
    )
    ref_video_group_id: Optional[str] = Field(None, alias="refVideoGroupId")
    matrix_group_ids: list[str] = Field(default_factory=list, alias="matrixGroupIds")
    matrix: dict[str, dict[str, float]] = Field(default_factory=dict)
    preset_groups: list[dict] = Field(default_factory=list, alias="presetGroups")
    video_mode: Optional[str] = Field(None, alias="videoMode")
    in_canvas_catalog: bool = Field(True, alias="inCanvasCatalog")
    default_option_snapshot: dict[str, str] = Field(default_factory=dict, alias="defaultOptionSnapshot")
    video_yuan_per_second: Optional[float] = Field(None, alias="videoYuanPerSecond")
    yuan_per_call: Optional[float] = Field(None, alias="yuanPerCall")
    # MiniMax-H3：输入视频按秒另计 + 超额参考图计费（供后台展示/编辑）
    bill_input_video_seconds: bool = Field(False, alias="billInputVideoSeconds")
    extra_image_billing: Optional[dict[str, float]] = Field(None, alias="extraImageBilling")


class AdminPricingPatchIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_cost: Optional[float] = Field(None, alias="baseCost")
    mode: Optional[str] = None
    min_cost: Optional[float] = Field(None, alias="minCost")
    primary_group_id: Optional[str] = Field(None, alias="primaryGroupId")
    options: Optional[dict[str, dict[str, float]]] = None
    options_with_video_reference: Optional[dict[str, dict[str, float]]] = Field(
        None, alias="optionsWithVideoReference"
    )
    matrix: Optional[dict[str, dict[str, float]]] = None
    video_yuan_per_second: Optional[float] = Field(None, alias="videoYuanPerSecond")
    yuan_per_call: Optional[float] = Field(None, alias="yuanPerCall")
    bill_input_video_seconds: Optional[bool] = Field(None, alias="billInputVideoSeconds")
    extra_image_billing: Optional[dict[str, float]] = Field(None, alias="extraImageBilling")


class AdminPricingQuoteIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    generation_options: dict[str, str] = Field(default_factory=dict, alias="generationOptions")
    base_cost: Optional[float] = Field(None, alias="baseCost")
    mode: Optional[str] = None
    min_cost: Optional[float] = Field(None, alias="minCost")
    primary_group_id: Optional[str] = Field(None, alias="primaryGroupId")
    options: Optional[dict[str, dict[str, float]]] = None
    matrix: Optional[dict[str, dict[str, float]]] = None


class AdminPricingQuoteOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: float
    base: float
    pricing_version: int = Field(..., alias="pricingVersion")
    option_snapshot: dict[str, str] = Field(default_factory=dict, alias="optionSnapshot")
    breakdown: list[dict]
    credits_enabled: bool = Field(..., alias="creditsEnabled")


def _merge_pricing_draft(pricing: dict, body: AdminPricingQuoteIn) -> dict:
    merged = dict(pricing)
    if body.base_cost is not None:
        merged["baseCost"] = body.base_cost
    if body.mode is not None:
        merged["mode"] = body.mode
    if body.min_cost is not None:
        merged["minCost"] = body.min_cost
    if body.primary_group_id is not None:
        merged["primaryGroupId"] = body.primary_group_id or None
    if body.options is not None:
        merged["options"] = body.options
    if body.matrix is not None:
        merged["matrix"] = _sanitize_matrix(body.matrix)
    return merged


def _sanitize_option_costs(raw: Any) -> dict[str, dict[str, float]]:
    """清洗 options / optionsWithVideoReference：{组id: {选项id: 非负一位小数}}。"""
    out: dict[str, dict[str, float]] = {}
    if not isinstance(raw, dict):
        return out
    for group_id, values in raw.items():
        if not isinstance(values, dict):
            continue
        group: dict[str, float] = {}
        for item_id, val in values.items():
            group[str(item_id)] = normalize_credit_amount(val, default=0)
        out[str(group_id)] = group
    return out


def _sanitize_matrix(raw: Any) -> dict[str, dict[str, float]]:
    """把前端传入的二维矩阵清洗为 {行id: {列id: 非负一位小数}}。"""
    return _sanitize_option_costs(raw)


def _pricing_item_out(model: Model) -> AdminPricingItemOut:
    params = model.parameters if isinstance(model.parameters, dict) else {}
    pricing = extract_pricing_config(params, category=model.category)
    presets = params.get("generationPresets")
    preset_groups: list[dict] = []
    default_snapshot: dict[str, str] = {}
    if isinstance(presets, dict):
        public = public_presets(presets) or presets
        default_snapshot = normalize_option_ids(presets, {})
        for group in public.get("groups") or []:
            gid = group.get("id")
            if not gid:
                continue
            preset_groups.append(
                {
                    "id": gid,
                    "label": group.get("label") or gid,
                    "items": [
                        {"id": item.get("id"), "label": item.get("label") or item.get("id")}
                        for item in (group.get("items") or [])
                        if item.get("id")
                    ],
                }
            )
    cost_cfg = extract_upstream_cost_config(params)
    with_ref = pricing.get("optionsWithVideoReference")
    eib_raw = pricing.get("extraImageBilling")
    eib_out: dict[str, float] | None = None
    if isinstance(eib_raw, dict):
        try:
            eib_out = {
                "freeCount": max(0, int(eib_raw.get("freeCount") or 0)),
                "costPerImage": normalize_credit_amount(eib_raw.get("costPerImage"), default=0),
            }
        except (TypeError, ValueError):
            eib_out = None
    return AdminPricingItemOut(
        modelId=str(model.id),
        name=model.name,
        displayName=model.display_name or model.name,
        category=model.category,
        isAvailable=bool(model.is_available),
        pricingVersion=int(pricing.get("version") or 1),
        baseCost=normalize_credit_amount(pricing.get("baseCost"), default=0),
        mode=str(pricing.get("mode") or "additive"),
        minCost=normalize_credit_amount(pricing.get("minCost"), default=0),
        primaryGroupId=pricing.get("primaryGroupId"),
        options=pricing.get("options") if isinstance(pricing.get("options"), dict) else {},
        optionsWithVideoReference=with_ref if isinstance(with_ref, dict) else {},
        refVideoGroupId=(
            str(pricing["refVideoGroupId"])
            if isinstance(pricing.get("refVideoGroupId"), str) and pricing.get("refVideoGroupId")
            else None
        ),
        matrixGroupIds=pricing.get("matrixGroupIds") if isinstance(pricing.get("matrixGroupIds"), list) else [],
        matrix=pricing.get("matrix") if isinstance(pricing.get("matrix"), dict) else {},
        presetGroups=preset_groups,
        videoMode=params.get("videoMode") if isinstance(params.get("videoMode"), str) else None,
        inCanvasCatalog=get_model_spec(model.name) is not None,
        defaultOptionSnapshot=default_snapshot,
        videoYuanPerSecond=cost_cfg.get("videoYuanPerSecond"),
        yuanPerCall=cost_cfg.get("yuanPerCall"),
        billInputVideoSeconds=bool(pricing.get("billInputVideoSeconds")),
        extraImageBilling=eib_out,
    )


@router.get("/pricing", response_model=list[AdminPricingItemOut])
async def list_admin_pricing(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    canvas_only: bool = Query(True, alias="canvasOnly"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    query = select(Model)
    if category:
        query = query.filter(Model.category == category)
    if search:
        query = query.filter(
            (Model.name.ilike(f"%{search}%")) | (Model.display_name.ilike(f"%{search}%"))
        )
    query = query.order_by(Model.category.asc(), Model.sort_order.asc(), Model.display_name.asc())
    result = await db.execute(query)
    models = list(result.scalars().all())
    backfilled = 0
    for model in models:
        if _ensure_model_pricing(model):
            backfilled += 1
    if backfilled:
        await db.flush()
        await reload_runtime_model_catalog(db)
    if canvas_only:
        models = [model for model in models if get_model_spec(model.name) is not None]
    return [_pricing_item_out(model) for model in models]


class AdminCanvasToolPricingItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(..., alias="toolId")
    label: str
    group: str = "canvas_image"
    billing_mode: str = Field("fixed", alias="billingMode")
    credit_cost: float = Field(..., alias="creditCost")
    credits_per_second: Optional[float] = Field(None, alias="creditsPerSecond")
    # model=跟随功能模型切换主模型价；platform=本地固定价（可编辑）
    price_source: str = Field("platform", alias="priceSource")
    primary_model: Optional[str] = Field(None, alias="primaryModel")
    primary_model_display_name: Optional[str] = Field(None, alias="primaryModelDisplayName")
    editable: bool = True


class AdminCanvasToolPricingOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: int
    items: list[AdminCanvasToolPricingItemOut]


class AdminCanvasToolPricingIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tools: dict[str, float] = Field(default_factory=dict)
    # 可选：单独提交视频工具每秒算力；未传时 tools 内视频键同样写入 videoToolRates
    video_tool_rates: Optional[dict[str, float]] = Field(None, alias="videoToolRates")


def _admin_canvas_tool_pricing_items(view: dict) -> list[AdminCanvasToolPricingItemOut]:
    return [
        AdminCanvasToolPricingItemOut(
            toolId=row["toolId"],
            label=row["label"],
            group=str(row.get("group") or "canvas_image"),
            billingMode=str(row.get("billingMode") or "fixed"),
            creditCost=row["creditCost"],
            creditsPerSecond=row.get("creditsPerSecond"),
            priceSource=str(row.get("priceSource") or "platform"),
            primaryModel=row.get("primaryModel"),
            primaryModelDisplayName=row.get("primaryModelDisplayName"),
            editable=bool(row.get("editable", True)),
        )
        for row in view.get("items") or []
    ]


@router.get("/pricing/canvas-tools", response_model=AdminCanvasToolPricingOut)
async def get_admin_canvas_tool_pricing(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    """管理端：工具算力展示（有主模型则跟模型价，否则平台固定价）。"""
    from ....services.canvas_tool_pricing import build_canvas_tool_pricing_view

    view = await build_canvas_tool_pricing_view(db)
    return AdminCanvasToolPricingOut(
        version=int(view["version"]),
        items=_admin_canvas_tool_pricing_items(view),
    )


@router.put("/pricing/canvas-tools", response_model=AdminCanvasToolPricingOut)
async def put_admin_canvas_tool_pricing(
    body: AdminCanvasToolPricingIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    """管理端：仅保存未纳管主模型的本地工具价（如宫格切分）；模型价请改模型定价页。"""
    from ....services.canvas_tool_models import CANVAS_TOOL_MODEL_IDS
    from ....services.canvas_tool_pricing import (
        build_canvas_tool_pricing_view,
        set_canvas_tool_pricing,
    )

    # 已跟主模型价的工具忽略写入，避免误覆盖平台兜底表造成混淆
    editable_tools = {
        tid: amount
        for tid, amount in (body.tools or {}).items()
        if tid not in CANVAS_TOOL_MODEL_IDS
    }
    editable_rates = None
    if body.video_tool_rates:
        editable_rates = {
            tid: rate
            for tid, rate in body.video_tool_rates.items()
            if tid not in CANVAS_TOOL_MODEL_IDS
        }
    if editable_tools or editable_rates:
        await set_canvas_tool_pricing(
            db,
            editable_tools,
            bump_version=True,
            video_tool_rates=editable_rates,
        )
    view = await build_canvas_tool_pricing_view(db)
    return AdminCanvasToolPricingOut(
        version=int(view["version"]),
        items=_admin_canvas_tool_pricing_items(view),
    )


class AdminCanvasToolModelItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(..., alias="toolId")
    label: str
    group: str = "canvas_image"
    category: str = "image"
    primary: str = ""
    secondary: str = ""


class AdminCanvasToolModelsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: int
    items: list[AdminCanvasToolModelItemOut]


class AdminCanvasToolModelEntryIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    primary: Optional[str] = None
    secondary: Optional[str] = None


class AdminCanvasToolModelsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tools: dict[str, AdminCanvasToolModelEntryIn] = Field(default_factory=dict)


def _canvas_tool_model_items(cfg: dict) -> list[AdminCanvasToolModelItemOut]:
    from ....services.canvas_tool_models import canvas_tool_models_public_items

    return [
        AdminCanvasToolModelItemOut(
            toolId=row["toolId"],
            label=row["label"],
            group=str(row.get("group") or "canvas_image"),
            category=str(row.get("category") or "image"),
            primary=str(row.get("primary") or ""),
            secondary=str(row.get("secondary") or ""),
        )
        for row in canvas_tool_models_public_items(cfg)
    ]


@router.get("/pricing/canvas-tool-models", response_model=AdminCanvasToolModelsOut)
async def get_admin_canvas_tool_models(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    """管理端：画布/分镜各功能主副模型切换配置。"""
    from ....services.canvas_tool_models import get_canvas_tool_models

    cfg = await get_canvas_tool_models(db)
    return AdminCanvasToolModelsOut(
        version=int(cfg["version"]),
        items=_canvas_tool_model_items(cfg),
    )


@router.put("/pricing/canvas-tool-models", response_model=AdminCanvasToolModelsOut)
async def put_admin_canvas_tool_models(
    body: AdminCanvasToolModelsIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    """管理端：保存画布/分镜各功能主副模型（校验模型有效并递增 version）。"""
    from ....services.canvas_tool_models import set_canvas_tool_models

    tools_payload = {
        tid: {
            "primary": entry.primary or "",
            "secondary": entry.secondary or "",
        }
        for tid, entry in (body.tools or {}).items()
    }
    cfg = await set_canvas_tool_models(db, tools_payload, bump_version=True)
    return AdminCanvasToolModelsOut(
        version=int(cfg["version"]),
        items=_canvas_tool_model_items(cfg),
    )


@router.patch("/pricing/{model_id}", response_model=AdminModelOut)
async def patch_admin_pricing(
    model_id: str,
    body: AdminPricingPatchIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)

    params = dict(model.parameters or {})
    pricing = extract_pricing_config(params, category=model.category)

    cost_only = (
        body.video_yuan_per_second is not None or body.yuan_per_call is not None
    ) and all(
        field is None
        for field in (
            body.base_cost,
            body.mode,
            body.min_cost,
            body.primary_group_id,
            body.options,
            body.options_with_video_reference,
        )
    )

    if body.video_yuan_per_second is not None or body.yuan_per_call is not None:
        video_rate = None
        call_rate = None
        if body.video_yuan_per_second is not None:
            try:
                video_rate = max(float(body.video_yuan_per_second), 0.0)
            except (TypeError, ValueError):
                fail(ErrorCode.INVALID_COST_PRICE, message="videoYuanPerSecond 无效")
        if body.yuan_per_call is not None:
            try:
                call_rate = max(float(body.yuan_per_call), 0.0)
            except (TypeError, ValueError):
                fail(ErrorCode.INVALID_COST_PRICE, message="yuanPerCall 无效")
        params = merge_upstream_cost_patch(
            params,
            video_yuan_per_second=video_rate,
            yuan_per_call=call_rate,
            patch_video=body.video_yuan_per_second is not None,
            patch_call=body.yuan_per_call is not None,
        )
        model.parameters = params
        flag_modified(model, "parameters")
        await db.flush()
        await reload_runtime_model_catalog(db)
        if cost_only:
            return _model_out(model)

    if body.base_cost is not None:
        pricing["baseCost"] = normalize_credit_amount(body.base_cost, default=0)
    if body.mode is not None:
        mode = body.mode.strip().lower()
        if mode not in (
            "additive",
            "primary_group",
            "video_per_second",
            "image_by_tier",
            "image_matrix",
        ):
            fail(ErrorCode.INVALID_PRICING_MODE)
        pricing["mode"] = mode
        if mode == "video_per_second":
            pricing.setdefault("rateGroupId", "resolution")
            pricing.setdefault("durationGroupId", "duration")
        if mode == "image_by_tier":
            pricing.setdefault("tierGroupIds", ["quality", "resolution", "size"])
        if mode == "image_matrix":
            pricing.setdefault("matrixGroupIds", ["quality", "resolution"])
    if body.min_cost is not None:
        pricing["minCost"] = normalize_credit_amount(body.min_cost, default=0)
    if body.primary_group_id is not None:
        pricing["primaryGroupId"] = body.primary_group_id.strip() or None
    if body.options is not None:
        pricing["options"] = _sanitize_option_costs(body.options)
    if body.options_with_video_reference is not None:
        # 多模态有参考视频单价表；写入后标记 refVideoGroupId 供报价切换
        cleaned = _sanitize_option_costs(body.options_with_video_reference)
        pricing["optionsWithVideoReference"] = cleaned
        pricing["refVideoGroupId"] = str(pricing.get("refVideoGroupId") or "refVideo")
    if body.matrix is not None:
        pricing["matrix"] = _sanitize_matrix(body.matrix)
    if body.bill_input_video_seconds is not None:
        pricing["billInputVideoSeconds"] = bool(body.bill_input_video_seconds)
    if body.extra_image_billing is not None:
        # 超额参考图：freeCount / costPerImage（算力点，一位小数）
        try:
            pricing["extraImageBilling"] = {
                "freeCount": max(0, int(body.extra_image_billing.get("freeCount") or 0)),
                "costPerImage": normalize_credit_amount(
                    body.extra_image_billing.get("costPerImage"), default=0
                ),
            }
        except (TypeError, ValueError, AttributeError):
            fail(ErrorCode.BAD_REQUEST, message="extraImageBilling 无效")

    if model.category == "video":
        pricing["mode"] = "video_per_second"
        pricing["baseCost"] = 0
        pricing["minCost"] = 0
        pricing.setdefault("rateGroupId", "resolution")
        pricing.setdefault("durationGroupId", "duration")

    if model.category == "image":
        if str(pricing.get("mode")) == "image_matrix":
            # 二维矩阵定价：画质 × 清晰度 9 档独立，不再使用 image_by_tier 的累加 options
            pricing["baseCost"] = 0
            pricing["minCost"] = 0
            pricing["mode"] = "image_matrix"
            pricing.setdefault("matrixGroupIds", ["quality", "resolution"])
            matrix = pricing.get("matrix")
            pricing["matrix"] = matrix if isinstance(matrix, dict) else {}
            pricing.pop("options", None)
            pricing.pop("tierGroupIds", None)
        elif _model_has_image_tier_presets(model):
            # 有画质/清晰度档：分档累加，基础价归零
            pricing["baseCost"] = 0
            pricing["minCost"] = 0
            pricing["mode"] = "image_by_tier"
            pricing.setdefault("tierGroupIds", ["quality", "resolution", "size"])
            tier_ids = set(pricing.get("tierGroupIds") or [])
            options = pricing.get("options")
            if isinstance(options, dict):
                pricing["options"] = {k: v for k, v in options.items() if k in tier_ids}
            pricing.pop("matrix", None)
            pricing.pop("matrixGroupIds", None)
        else:
            # 无画质/清晰度档（如悠船 Niji 7）：按次基础算力
            pricing["mode"] = "additive"
            pricing["baseCost"] = normalize_credit_amount(pricing.get("baseCost"), default=0)
            pricing["minCost"] = normalize_credit_amount(pricing.get("minCost"), default=0)
            pricing.pop("tierGroupIds", None)
            pricing.pop("matrix", None)
            pricing.pop("matrixGroupIds", None)

    pricing["version"] = int(pricing.get("version") or 1) + 1
    params["pricing"] = pricing
    model.parameters = params
    flag_modified(model, "parameters")
    await db.flush()
    await reload_runtime_model_catalog(db)
    return _model_out(model)


@router.post("/pricing/{model_id}/reset", response_model=AdminModelOut)
async def reset_admin_pricing(
    model_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)

    params = dict(model.parameters or {})
    presets = params.get("generationPresets")
    presets_dict = presets if isinstance(presets, dict) else None
    pricing = default_pricing_for_model(model.category, presets_dict)
    params["pricing"] = pricing
    model.parameters = params
    flag_modified(model, "parameters")
    await db.flush()
    await reload_runtime_model_catalog(db)
    return _model_out(model)


@router.post("/pricing/{model_id}/quote", response_model=AdminPricingQuoteOut)
async def preview_admin_pricing_quote(
    model_id: str,
    body: AdminPricingQuoteIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    model_id_int = require_entity_id(model_id, message="模型 ID 无效")

    result = await db.execute(select(Model).filter(Model.id == model_id_int))
    model = result.scalar_one_or_none()
    if not model:
        fail(ErrorCode.MODEL_NOT_FOUND)

    params = dict(model.parameters or {})
    pricing = extract_pricing_config(params, category=model.category)
    pricing = _merge_pricing_draft(pricing, body)
    quote = quote_generation_cost(
        model,
        body.generation_options,
        pricing_config=pricing,
    )
    breakdown_dicts = [item.to_dict() for item in quote.breakdown]
    return AdminPricingQuoteOut(
        total=quote.total,
        base=quote.base,
        pricingVersion=quote.pricing_version,
        optionSnapshot=quote.option_snapshot,
        breakdown=breakdown_dicts,
        creditsEnabled=quote.credits_enabled,
    )


# —— AI 操控：控制器模型白名单 + 一次对话算力 ——


class AdminAgentControllerModelOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    label: str
    configured: bool = False
    supports_files: bool = Field(False, alias="supportsFiles")
    enabled: bool = True
    provider: str = ""


class AdminAgentSkillPricingOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: int
    conversation_turn: float = Field(..., alias="conversationTurn")
    session_start: float = Field(..., alias="sessionStart")
    skill_ai_fill: float = Field(..., alias="skillAiFill")
    skills: dict[str, float] = Field(default_factory=dict)
    controller_models: list[str] = Field(default_factory=list, alias="controllerModels")
    catalog: list[AdminAgentControllerModelOut] = Field(default_factory=list)


class AdminAgentSkillPricingIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    conversation_turn: float | None = Field(None, alias="conversationTurn")
    skill_ai_fill: float | None = Field(None, alias="skillAiFill")
    skills: dict[str, float] | None = None
    controller_models: list[str] | None = Field(None, alias="controllerModels")


@router.get("/pricing/agent-control", response_model=AdminAgentSkillPricingOut)
async def get_admin_agent_skill_pricing(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    """管理端：AI 操控可选模型 + 一次对话算力。"""
    from ....services.agent_controller import all_controller_catalog_public
    from ....services.agent_skill_pricing import get_agent_skill_pricing

    pricing = await get_agent_skill_pricing(db)
    enabled = set(str(x) for x in (pricing.get("controllerModels") or []))
    # 白名单为空 = 全部启用（与运行时一致）
    all_enabled = len(enabled) == 0
    catalog = [
        AdminAgentControllerModelOut(
            id=row["id"],
            label=row["label"],
            configured=bool(row.get("configured")),
            supportsFiles=bool(row.get("supportsFiles")),
            enabled=all_enabled or row["id"] in enabled,
            provider=str(row.get("provider") or ""),
        )
        for row in all_controller_catalog_public()
    ]
    return AdminAgentSkillPricingOut(
        version=int(pricing["version"]),
        conversationTurn=normalize_credit_amount(pricing["conversationTurn"], default=0),
        sessionStart=normalize_credit_amount(pricing["sessionStart"], default=0),
        skillAiFill=normalize_credit_amount(pricing.get("skillAiFill") or 0, default=0),
        skills=dict(pricing.get("skills") or {}),
        controllerModels=list(pricing.get("controllerModels") or []),
        catalog=catalog,
    )


@router.put("/pricing/agent-control", response_model=AdminAgentSkillPricingOut)
async def put_admin_agent_skill_pricing(
    body: AdminAgentSkillPricingIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_PRICING)),
):
    """管理端：保存 AI 操控模型白名单、一次对话算力与 Skill AI 填充价。"""
    from ....services.agent_controller import (
        all_controller_catalog_public,
        normalize_controller_model,
    )
    from ....services.agent_skill_pricing import (
        get_agent_skill_pricing,
        set_agent_skill_pricing,
    )

    models_in = body.controller_models
    cleaned: list[str] | None = None
    if models_in is not None:
        cleaned = []
        seen: set[str] = set()
        for mid in models_in:
            n = normalize_controller_model(mid)
            if not n or n in seen:
                continue
            seen.add(n)
            cleaned.append(n)

    await set_agent_skill_pricing(
        db,
        conversation_turn=body.conversation_turn,
        skill_ai_fill=body.skill_ai_fill,
        skills=body.skills,
        controller_models=cleaned,
        bump_version=True,
    )
    await db.commit()

    pricing = await get_agent_skill_pricing(db)
    enabled = set(str(x) for x in (pricing.get("controllerModels") or []))
    all_enabled = len(enabled) == 0
    catalog = [
        AdminAgentControllerModelOut(
            id=row["id"],
            label=row["label"],
            configured=bool(row.get("configured")),
            supportsFiles=bool(row.get("supportsFiles")),
            enabled=all_enabled or row["id"] in enabled,
            provider=str(row.get("provider") or ""),
        )
        for row in all_controller_catalog_public()
    ]
    return AdminAgentSkillPricingOut(
        version=int(pricing["version"]),
        conversationTurn=normalize_credit_amount(pricing["conversationTurn"], default=0),
        sessionStart=normalize_credit_amount(pricing["sessionStart"], default=0),
        skillAiFill=normalize_credit_amount(pricing.get("skillAiFill") or 0, default=0),
        skills=dict(pricing.get("skills") or {}),
        controllerModels=list(pricing.get("controllerModels") or []),
        catalog=catalog,
    )

