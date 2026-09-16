"""用户算力接口：报价、余额查询、消耗顺序、充值与流水（均基于本地多类型算力账本）。"""

import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.datetime_util import CST, to_cst_iso
from ...core.credit_amount import normalize_credit_amount
from ...core.credit_types import (
    ALL_CREDIT_TYPES,
    CREDIT_TYPE_GENERAL,
    CREDIT_TYPE_LABELS,
    DEFAULT_CONSUME_PRIORITY,
    is_valid_consume_priority,
    normalize_consume_priority,
)
from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...models.database import get_db
from ...models.job import Model
from ...models.user import User
from ...services.credit_flow import credits_enabled, get_user_credit_balance
from ...services.credit_lots import get_balance_breakdown, get_user_consume_priority
from ...services.credit_pricing import CreditQuote, build_quote_token, quote_generation_cost
from ...core.config import get_settings
from ...services.credit_transaction_query import list_credit_transactions

router = APIRouter()


class CreditBreakdownOut(BaseModel):
    """报价明细项：某个选项分组下具体选项的算力消耗。"""

    model_config = ConfigDict(populate_by_name=True)

    group_id: str = Field(..., alias="groupId")
    item_id: str = Field(..., alias="itemId")
    label: str
    cost: float


class CreditQuoteOut(BaseModel):
    """单次生成报价响应：含总价、明细、定价版本与签名后的 quoteToken。"""

    model_config = ConfigDict(populate_by_name=True)

    model: str
    total: float
    base: float
    breakdown: list[CreditBreakdownOut]
    pricing_version: int = Field(..., alias="pricingVersion")
    option_snapshot: dict[str, str] = Field(default_factory=dict, alias="optionSnapshot")
    credits_enabled: bool = Field(..., alias="creditsEnabled")
    quote_token: str = Field(..., alias="quoteToken")
    expires_at: str = Field(..., alias="expiresAt")


class CreditTypeBreakdownOut(BaseModel):
    """余额按四类算力（活动/模型专用/订阅/通用）的分类汇总。"""

    model_config = ConfigDict(populate_by_name=True)

    activity: float = 0
    model_specific: float = Field(0, alias="modelSpecific")
    subscription: float = 0
    general: float = 0


class ModelSpecificBalanceOut(BaseModel):
    """模型专用算力余额明细：指定模型的可用额度及到期时间。"""

    model_config = ConfigDict(populate_by_name=True)

    model: str
    # 目录展示名；前端优先展示此字段
    model_display_name: Optional[str] = Field(None, alias="modelDisplayName")
    balance: float
    expires_at: Optional[str] = Field(None, alias="expiresAt")


class ExpiringCreditOut(BaseModel):
    """即将过期的算力提醒项：类型、数量与到期时间。"""

    model_config = ConfigDict(populate_by_name=True)

    type: str
    amount: float
    expires_at: str = Field(..., alias="expiresAt")
    model: Optional[str] = None
    model_display_name: Optional[str] = Field(None, alias="modelDisplayName")


class CreditBalanceOut(BaseModel):
    """用户算力余额响应：总额、模型可用额、分类明细与消耗顺序。"""

    model_config = ConfigDict(populate_by_name=True)

    balance: Optional[float] = None
    available_for_model: Optional[float] = Field(None, alias="availableForModel")
    credits_enabled: bool = Field(..., alias="creditsEnabled")
    breakdown: Optional[CreditTypeBreakdownOut] = None
    model_specific: list[ModelSpecificBalanceOut] = Field(default_factory=list, alias="modelSpecific")
    expiring_soon: list[ExpiringCreditOut] = Field(default_factory=list, alias="expiringSoon")
    consume_priority: list[str] = Field(default_factory=list, alias="consumePriority")


class CreditConsumePriorityIn(BaseModel):
    """更新消耗顺序请求体：四类算力的扣费优先级排列。"""

    model_config = ConfigDict(populate_by_name=True)

    priority: list[str]


class CreditConsumePriorityOut(BaseModel):
    """消耗顺序响应：当前优先级排列及各类型中文标签。"""

    model_config = ConfigDict(populate_by_name=True)

    priority: list[str]
    labels: dict[str, str]


class CreditQuoteItemIn(BaseModel):
    """批量报价中的单个报价项：模型、类别与生成选项。"""

    model_config = ConfigDict(populate_by_name=True)

    model: str
    category: Optional[str] = None
    generation_options: dict[str, str] = Field(default_factory=dict, alias="generationOptions")


class CreditQuotesBatchIn(BaseModel):
    """批量报价请求体：一组报价项（最多 50 个）。"""

    items: list[CreditQuoteItemIn]


class CreditQuotesBatchOut(BaseModel):
    """批量报价响应：与请求项一一对应的报价结果列表。"""

    items: list[CreditQuoteOut]


def _parse_options_json(raw: str | None) -> dict[str, str]:
    import json

    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        fail(ErrorCode.INVALID_GENERATION_OPTIONS, message="generationOptions JSON 无效")
    if not isinstance(parsed, dict):
        fail(ErrorCode.INVALID_GENERATION_OPTIONS, message="generationOptions 必须为对象")
    return {str(k): str(v) for k, v in parsed.items()}


async def _query_catalog_model(
    db: AsyncSession,
    *,
    model: str,
    category: str | None,
) -> Model | None:
    from ...services.model_catalog_cache import get_cached_model_by_name

    # 后台模型开关仅隐藏前端可选模型；报价按 available_only=False 解析，保证已接入模型均可报价。
    if category:
        catalog_model = await get_cached_model_by_name(
            db, model, category=category, available_only=False
        )
        if catalog_model:
            return catalog_model
    return await get_cached_model_by_name(db, model, available_only=False)


def _model_from_registry(model: str, category: str | None) -> Model | None:
    from ...core.model_registry import get_model_spec
    from ...services.model_catalog import _enrich_catalog_item, catalog_dict_for_spec

    spec = get_model_spec(model)
    if not spec:
        return None
    if category and spec.category != category:
        return None
    base = catalog_dict_for_spec(spec)
    item = _enrich_catalog_item(spec.name, base)
    return Model(**item, is_available=True)


async def _load_model(
    db: AsyncSession,
    *,
    model: str,
    category: str | None,
) -> Model:
    catalog_model = await _query_catalog_model(db, model=model, category=category)
    if catalog_model:
        return catalog_model

    from ...services.model_catalog import sync_model_catalog

    await sync_model_catalog(db)
    await db.flush()
    catalog_model = await _query_catalog_model(db, model=model, category=category)
    if catalog_model:
        return catalog_model

    from ...core.config import get_settings

    if get_settings().model_catalog_source.strip().lower() == "db":
        fail(ErrorCode.MODEL_UNAVAILABLE, content={"model": model})

    fallback = _model_from_registry(model, category)
    if fallback:
        return fallback

    fail(ErrorCode.MODEL_UNAVAILABLE, content={"model": model})


def _quote_out(quote: CreditQuote) -> CreditQuoteOut:
    from ...services.credit_pricing import QUOTE_TOKEN_TTL_S, _reconcile_quote_total

    expires_at = time.time() + QUOTE_TOKEN_TTL_S
    total = _reconcile_quote_total(quote.total, quote.breakdown, 0)
    return CreditQuoteOut(
        model=quote.model,
        total=total,
        base=quote.base,
        breakdown=[
            CreditBreakdownOut(
                groupId=item.group_id,
                itemId=item.item_id,
                label=item.label,
                cost=item.cost,
            )
            for item in quote.breakdown
        ],
        pricingVersion=quote.pricing_version,
        optionSnapshot=quote.option_snapshot,
        creditsEnabled=quote.credits_enabled,
        quoteToken=build_quote_token(quote),
        expiresAt=to_cst_iso(datetime.fromtimestamp(expires_at, tz=CST)),
    )


def _balance_out(
    *,
    enabled: bool,
    snapshot: dict,
    model_name: str | None,
    priority: list[str],
) -> CreditBalanceOut:
    if not enabled:
        return CreditBalanceOut(
            balance=None,
            availableForModel=None,
            creditsEnabled=False,
            consumePriority=priority,
        )
    breakdown_raw = snapshot.get("breakdown") or {}
    return CreditBalanceOut(
        balance=normalize_credit_amount(snapshot.get("balance", 0)),
        availableForModel=(
            normalize_credit_amount(snapshot.get("availableForModel"))
            if snapshot.get("availableForModel") is not None
            else None
        ),
        creditsEnabled=True,
        breakdown=CreditTypeBreakdownOut(
            activity=normalize_credit_amount(breakdown_raw.get("activity") or 0),
            modelSpecific=normalize_credit_amount(breakdown_raw.get("model_specific") or 0),
            subscription=normalize_credit_amount(breakdown_raw.get("subscription") or 0),
            general=normalize_credit_amount(breakdown_raw.get("general") or 0),
        ),
        modelSpecific=[
            ModelSpecificBalanceOut(
                model=row["model"],
                modelDisplayName=row.get("modelDisplayName"),
                balance=normalize_credit_amount(row["balance"]),
                expiresAt=row.get("expiresAt"),
            )
            for row in snapshot.get("modelSpecific") or []
        ],
        expiringSoon=[
            ExpiringCreditOut(
                type=row["type"],
                amount=normalize_credit_amount(row["amount"]),
                expiresAt=row["expiresAt"],
                model=row.get("model"),
                modelDisplayName=row.get("modelDisplayName"),
            )
            for row in snapshot.get("expiringSoon") or []
        ],
        consumePriority=priority,
    )


@router.get("/quote", response_model=CreditQuoteOut)
async def get_credit_quote(
    model: str = Query(...),
    category: Optional[str] = Query(None),
    generation_options: Optional[str] = Query(None, alias="generationOptions"),
    canvas_tool: Optional[str] = Query(None, alias="canvasTool"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """获取单个模型在指定生成选项下的权威算力报价；画布工具可传 canvasTool。

    有主模型配置的 canvasTool：金额跟主模型 ``parameters.pricing``。
    视频工具且主模型为按秒计价时：generationOptions 含 inputVideoSeconds / outputVideoSeconds，
    总价 = (输入秒+输出秒)×主模型默认每秒算力。
    """
    options = _parse_options_json(generation_options)
    if canvas_tool:
        from ...services.canvas_tool_models import (
            CANVAS_TOOL_MODEL_IDS,
            resolve_canvas_tool_generation_model,
        )
        from ...services.canvas_tool_pricing import get_canvas_tool_pricing, quote_canvas_tool_cost

        # 后台主模型为权威：报价模型名与提交一致；金额跟主模型 parameters.pricing
        tool_id = canvas_tool.strip()
        catalog_model = None
        follow_model = tool_id in CANVAS_TOOL_MODEL_IDS
        if follow_model:
            resolution = await resolve_canvas_tool_generation_model(
                db, tool_id, requested_model=model
            )
            if resolution is not None:
                catalog_model = resolution.catalog_model
        if catalog_model is None:
            catalog_model = await _load_model(db, model=model, category=category)
        pricing = await get_canvas_tool_pricing(db)
        return _quote_out(
            quote_canvas_tool_cost(
                catalog_model,
                tool_id,
                pricing,
                generation_options=options,
                follow_model_pricing=follow_model,
            )
        )
    catalog_model = await _load_model(db, model=model, category=category)
    return _quote_out(quote_generation_cost(catalog_model, options))


@router.get("/canvas-tool-pricing")
async def get_canvas_tool_pricing_public(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """画布顶栏工具算力展示：有主模型则跟模型价，其余为平台固定价。"""
    from ...services.canvas_tool_pricing import build_canvas_tool_pricing_view

    view = await build_canvas_tool_pricing_view(db)
    return {
        "version": view["version"],
        "items": view["items"],
        "creditsEnabled": credits_enabled(),
    }


@router.get("/agent-skill-pricing")
async def get_agent_skill_pricing_public(
    skill_slug: str | None = Query(None, alias="skillSlug"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Agent 编排轨 S 报价（创作框展示）。"""
    from ...core.errors import ok
    from ...services.agent_skill_pricing import (
        agent_skill_pricing_public,
        get_agent_skill_pricing,
        quote_agent_session_cost,
    )

    from ...services.agent_skill_pricing import quote_skill_ai_fill_cost

    pricing = await get_agent_skill_pricing(db)
    quote = quote_agent_session_cost(skill_slug=skill_slug, pricing=pricing)
    fill_quote = quote_skill_ai_fill_cost(pricing)
    pub = agent_skill_pricing_public(pricing)
    return ok(
        {
            **pub,
            "quote": {
                "total": quote.total,
                "base": quote.base,
                "pricingVersion": quote.pricing_version,
                "breakdown": [x.to_dict() for x in quote.breakdown],
                "skillSlug": (skill_slug or "").strip() or None,
            },
            # 创建 Skill「按类型 AI 填充」报价（与对话轨分开）
            "skillAiFillQuote": {
                "total": fill_quote.total,
                "base": fill_quote.base,
                "pricingVersion": fill_quote.pricing_version,
                "breakdown": [x.to_dict() for x in fill_quote.breakdown],
            },
        }
    )


@router.post("/quotes", response_model=CreditQuotesBatchOut)
async def post_credit_quotes_batch(
    body: CreditQuotesBatchIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """批量获取多个模型的算力报价（单次最多 50 项）。"""
    if len(body.items) > 50:
        fail(ErrorCode.QUOTE_BATCH_TOO_LARGE, content={"max": 50})
    out: list[CreditQuoteOut] = []
    for item in body.items:
        catalog_model = await _load_model(db, model=item.model, category=item.category)
        quote = quote_generation_cost(catalog_model, item.generation_options)
        out.append(_quote_out(quote))
    return CreditQuotesBatchOut(items=out)


@router.get("/balance", response_model=CreditBalanceOut)
async def get_credit_balance(
    model: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """查询当前用户算力余额、分类明细及指定模型可消费额度。"""
    enabled = credits_enabled()
    priority = get_user_consume_priority(current_user)
    if not enabled:
        return _balance_out(enabled=False, snapshot={}, model_name=model, priority=priority)

    snapshot = await get_balance_breakdown(db, current_user.id)
    if model:
        snapshot["availableForModel"] = await get_user_credit_balance(
            current_user, db, model_name=model
        )
    return _balance_out(enabled=True, snapshot=snapshot, model_name=model, priority=priority)


@router.get("/consume-priority", response_model=CreditConsumePriorityOut)
async def get_consume_priority(
    current_user: User = Depends(get_current_user),
):
    """获取当前用户配置的算力消耗顺序。"""
    priority = get_user_consume_priority(current_user)
    return CreditConsumePriorityOut(priority=priority, labels=CREDIT_TYPE_LABELS)


@router.put("/consume-priority", response_model=CreditConsumePriorityOut)
async def update_consume_priority(
    body: CreditConsumePriorityIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户的算力消耗顺序（四类算力须各出现一次）。"""
    if not is_valid_consume_priority(body.priority):
        fail(ErrorCode.INVALID_CONSUME_PRIORITY, content={"allowed": list(ALL_CREDIT_TYPES)})
    current_user.credit_consume_priority = body.priority
    await db.flush()
    priority = normalize_consume_priority(body.priority)
    return CreditConsumePriorityOut(priority=priority, labels=CREDIT_TYPE_LABELS)



class CreditRechargeIn(BaseModel):
    """已废弃用户侧直连充值请求体（保留仅为兼容旧客户端）。"""

    model_config = ConfigDict(populate_by_name=True)

    amount: int = Field(..., ge=1)
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey", max_length=128)


class CreditRechargeOut(BaseModel):
    """已废弃用户侧直连充值响应体（保留仅为 OpenAPI 兼容）。"""

    model_config = ConfigDict(populate_by_name=True)

    amount: int
    balance: float
    credits_enabled: bool = Field(..., alias="creditsEnabled")


class CreditTransactionOut(BaseModel):
    """算力流水单条记录：变动额、来源、关联任务等审计信息。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    delta: float
    balance_after: float = Field(..., alias="balanceAfter")
    source: str
    source_label: str = Field(..., alias="sourceLabel")
    credit_type: Optional[str] = Field(None, alias="creditType")
    model_name: Optional[str] = Field(None, alias="modelName")
    model_display_name: Optional[str] = Field(None, alias="modelDisplayName")
    job_id: Optional[str] = Field(None, alias="jobId")
    reason: Optional[str] = None
    created_at: datetime = Field(..., alias="createdAt")
    # 入账批次过期时间；充值通用算力为永久（expiresLabel=永久）
    expires_at: Optional[datetime] = Field(None, alias="expiresAt")
    expires_label: Optional[str] = Field(None, alias="expiresLabel")


class CreditTransactionListOut(BaseModel):
    """算力流水分页列表响应：记录列表与分页信息。"""

    model_config = ConfigDict(populate_by_name=True)

    items: list[CreditTransactionOut]
    total: int
    page: int
    page_size: int = Field(..., alias="pageSize")


@router.post("/recharge", response_model=CreditRechargeOut)
async def recharge_credits(
    body: CreditRechargeIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户侧无支付直连充值已永久关闭（§8.12）。

    架构约定（不对用户文案暴露）：加算力仅允许
    1) 支付宝扫码 POST /payments/alipay/recharge（验签+查单）
    2) 管理后台调账 POST /admin/credits/users/{id}/adjust
    对用户统一提示「请使用支付宝扫码充值」。
    """
    _ = (body, current_user, db)
    if not credits_enabled():
        fail(ErrorCode.CREDITS_DISABLED)
    # 用户可见文案仅引导支付宝，不提及管理员加款
    fail(ErrorCode.DIRECT_RECHARGE_DISABLED)


@router.get("/transactions", response_model=CreditTransactionListOut)
async def list_user_credit_transactions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    source: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分页查询当前用户的算力流水记录，可按来源过滤。"""
    items_raw, total = await list_credit_transactions(
        db,
        user_id=current_user.id,
        source=source.strip() if source else None,
        page=page,
        page_size=page_size,
    )
    items = [
        CreditTransactionOut(
            id=row["id"],
            delta=row["delta"],
            balanceAfter=row["balance_after"],
            source=row["source"],
            sourceLabel=row["source_label"],
            creditType=row.get("credit_type"),
            modelName=row.get("model_name"),
            modelDisplayName=row.get("model_display_name"),
            jobId=row.get("job_id"),
            reason=row.get("reason"),
            createdAt=row["created_at"],
            expiresAt=row.get("expires_at"),
            expiresLabel=row.get("expires_label"),
        )
        for row in items_raw
    ]
    return CreditTransactionListOut(
        items=items,
        total=total,
        page=page,
        pageSize=page_size,
    )
