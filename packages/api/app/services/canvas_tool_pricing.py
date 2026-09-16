"""画布/分镜算力价格。

已纳管主/副模型的功能（见 ``canvas_tool_models``）：金额权威来自**主模型**
``Model.parameters.pricing``（与普通生成同一套 ``quote_generation_cost`` / 每秒单价）；
扣费仍按实际调用主模型名匹配模型专用算力。

未纳管模型的本地工具（如宫格切分 ``grid_split``）：仍读
``platform_settings.canvas_tool_pricing`` 固定价兜底。

视频画面/音频工具且主模型为 ``video_per_second`` 时：
总价 = (输入参考视频秒数 + 生成视频秒数) × 主模型默认每秒算力。
主模型为按次计价时：按模型单次报价（不再乘两端时长）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_amount import normalize_credit_amount
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.job import Model
from .credit_flow import credits_enabled
from .credit_pricing import (
    CreditBreakdownItem,
    CreditQuote,
    extract_pricing_config,
    quote_generation_cost,
)
from .platform_settings import get_canvas_tool_pricing_raw, set_canvas_tool_pricing_raw

# (tool_id, 展示名, 分组：canvas_image | canvas_video | storyboard)
CANVAS_TOOL_CREDIT_DEFS: tuple[tuple[str, str, str], ...] = (
    ("multi_angle", "多角度", "canvas_image"),
    ("lighting", "打光", "canvas_image"),
    ("panorama", "全景", "canvas_image"),
    # 九宫格顶栏兼容旧路径；弹层内各功能见下方「九宫格·」条目
    ("grid_9", "九宫格（兼容）", "canvas_image"),
    ("grid_25", "九宫格·25宫格连贯分镜", "canvas_image"),
    ("plot_grid_4", "九宫格·剧情推演四宫格", "canvas_image"),
    ("frame_forward_3s", "九宫格·画面推演3秒后", "canvas_image"),
    ("frame_back_5s", "九宫格·画面推演5秒前", "canvas_image"),
    ("cinematic_lighting", "九宫格·电影级光影校正", "canvas_image"),
    ("multi_cam_grid_9", "九宫格·多机位九宫格", "canvas_image"),
    ("face_tri_view", "九宫格·角色脸部三视图", "canvas_image"),
    ("character_sheet", "九宫格·角色设定图", "canvas_image"),
    ("character_tri_view", "九宫格·角色三视图", "canvas_image"),
    ("scene_sheet", "九宫格·场景设定图", "canvas_image"),
    ("product_sheet", "九宫格·产品设定图", "canvas_image"),
    ("grid_split", "宫格切分", "canvas_image"),
    ("outpaint", "扩图", "canvas_image"),
    ("cutout", "抠图", "canvas_image"),
    ("hd_upscale", "高清", "canvas_image"),
    ("portrait_adjust", "人像调节", "canvas_image"),
    ("emotion_adjust", "情绪调节", "canvas_image"),
    ("drawing_board_ai", "画板 AI", "canvas_image"),
    # 故事板 / 调度故事板：一张多镜合成图（图文一体）
    ("storyboard", "故事板", "canvas_image"),
    ("blocking_storyboard", "调度故事板", "canvas_image"),
    ("hd_upscale_video", "视频高清", "canvas_video"),
    ("video_smart_matting", "智能抠像", "canvas_video"),
    ("video_subject_remove", "主体消除", "canvas_video"),
    ("video_subject_edit", "主体修改", "canvas_video"),
    ("video_subject_replace", "主体替换", "canvas_video"),
    ("video_subtitle_smart_erase", "智能去字幕·智能擦除", "canvas_video"),
    ("video_subtitle_box_erase", "智能去字幕·框选擦除", "canvas_video"),
    ("vocal_separate", "人声分离", "canvas_video"),
    ("vocal_remove", "消除人声", "canvas_video"),
    ("storyboard_table", "分镜表解析", "storyboard"),
    ("storyboard_from_video", "爆款拉片复刻", "storyboard"),
    ("storyboard_from_image", "图片提取分镜", "storyboard"),
    ("storyboard_overseas_localize", "一键出海本地化", "storyboard"),
    ("text_subject", "主体提取", "storyboard"),
    ("storyboard_camera", "运镜提示词", "storyboard"),
    ("storyboard_video", "视频提示词", "storyboard"),
    ("storyboard_sketch", "分镜草图", "storyboard"),
    ("storyboard_subject_image", "主体生图", "storyboard"),
)

# 九宫格弹层内可独立配模型/算力的子功能（不含顶栏兼容 grid_9、故事板入口）
CREATIVE_GRID_CHILD_TOOL_IDS: frozenset[str] = frozenset(
    {
        "grid_25",
        "plot_grid_4",
        "frame_forward_3s",
        "frame_back_5s",
        "cinematic_lighting",
        "multi_cam_grid_9",
        "face_tri_view",
        "character_sheet",
        "character_tri_view",
        "scene_sheet",
        "product_sheet",
    }
)

CANVAS_TOOL_IDS = frozenset(tool_id for tool_id, _, _ in CANVAS_TOOL_CREDIT_DEFS)
# 视频画面/音频工具：按 (输入秒+输出秒)×每秒算力
CANVAS_VIDEO_BILLING_TOOL_IDS = frozenset(
    tid for tid, _, group in CANVAS_TOOL_CREDIT_DEFS if group == "canvas_video"
)
BILLING_MODE_FIXED = "fixed"
BILLING_MODE_VIDEO_IO_PER_SEC = "video_input_plus_output_per_second"

DEFAULT_CANVAS_TOOL_CREDIT = 10.0
# 视频工具默认每秒算力（一位小数）；旧固定价不再沿用，避免把「10/次」误当成「10/秒」
DEFAULT_VIDEO_TOOL_CREDITS_PER_SEC = 1.0
# 故事板合成图默认算力（与产品示意一致；可在后台「通用算力价格」改）
_DEFAULT_TOOL_CREDIT_OVERRIDES: dict[str, float] = {
    "storyboard": 120.0,
    "blocking_storyboard": 120.0,
}

# 计费秒数上下限（含画面编辑 ≤12s 与较长源片工具）
_MIN_BILL_SEC = 1
_MAX_BILL_SEC = 600


def canvas_tool_label(tool_id: str) -> str:
    for tid, label, _ in CANVAS_TOOL_CREDIT_DEFS:
        if tid == tool_id:
            return label
    return tool_id


def canvas_tool_group(tool_id: str) -> str:
    for tid, _, group in CANVAS_TOOL_CREDIT_DEFS:
        if tid == tool_id:
            return group
    return "canvas_image"


def is_canvas_video_billing_tool(tool_id: str) -> bool:
    """是否为视频按时长计费的画布工具。"""
    return (tool_id or "").strip() in CANVAS_VIDEO_BILLING_TOOL_IDS


def canvas_tool_billing_mode(tool_id: str) -> str:
    return (
        BILLING_MODE_VIDEO_IO_PER_SEC
        if is_canvas_video_billing_tool(tool_id)
        else BILLING_MODE_FIXED
    )


def _default_fixed_tools() -> dict[str, float]:
    return {
        tid: _DEFAULT_TOOL_CREDIT_OVERRIDES.get(tid, DEFAULT_CANVAS_TOOL_CREDIT)
        for tid, _, group in CANVAS_TOOL_CREDIT_DEFS
        if group != "canvas_video"
    }


def _default_video_rates() -> dict[str, float]:
    return {tid: DEFAULT_VIDEO_TOOL_CREDITS_PER_SEC for tid in sorted(CANVAS_VIDEO_BILLING_TOOL_IDS)}


def normalize_canvas_tool_pricing(raw: Any) -> dict[str, Any]:
    """规范化配置：固定价 tools + 视频每秒价 videoToolRates；算力一位小数。"""
    version = 1
    tools = _default_fixed_tools()
    video_rates = _default_video_rates()
    src: dict[str, Any] | None = None
    if isinstance(raw, dict):
        try:
            version = max(1, int(raw.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        src = raw.get("tools") if isinstance(raw.get("tools"), dict) else raw
        if isinstance(src, dict):
            for tid, _, group in CANVAS_TOOL_CREDIT_DEFS:
                if tid not in src:
                    continue
                # 旧数据曾把视频工具写在 tools 里当固定价；升级后仅非视频写入 tools
                if group == "canvas_video":
                    continue
                tools[tid] = normalize_credit_amount(src[tid], default=tools[tid])
        rates_src = raw.get("videoToolRates")
        if isinstance(rates_src, dict):
            for tid in CANVAS_VIDEO_BILLING_TOOL_IDS:
                if tid not in rates_src:
                    continue
                video_rates[tid] = normalize_credit_amount(
                    rates_src[tid], default=video_rates[tid]
                )
        else:
            # 兼容：若尚无 videoToolRates，但 tools 里残留视频键，不自动当「每秒价」迁移
            # （避免历史「10 点/次」变成「10 点/秒」）；用默认 1.0/秒
            pass

    # 九宫格子功能：库中尚未单独定价时，继承原「九宫格」统一价，避免升级后默认为另一套默认值
    if isinstance(src, dict):
        legacy_grid = (
            normalize_credit_amount(src["grid_9"], default=tools.get("grid_9", DEFAULT_CANVAS_TOOL_CREDIT))
            if "grid_9" in src
            else None
        )
        inherit = legacy_grid if legacy_grid is not None else tools.get("grid_9")
        if inherit is not None:
            for tid in CREATIVE_GRID_CHILD_TOOL_IDS:
                if tid not in src:
                    tools[tid] = inherit

    return {"version": version, "tools": tools, "videoToolRates": video_rates}


def require_canvas_tool_id(tool_id: str | None) -> str:
    tid = (tool_id or "").strip()
    if tid not in CANVAS_TOOL_IDS:
        fail(ErrorCode.BAD_REQUEST, message=f"无效的画布工具：{tool_id}")
    return tid


async def get_canvas_tool_pricing(db: AsyncSession) -> dict[str, Any]:
    raw = await get_canvas_tool_pricing_raw(db)
    return normalize_canvas_tool_pricing(raw)


async def set_canvas_tool_pricing(
    db: AsyncSession,
    tools: dict[str, float],
    *,
    bump_version: bool = True,
    video_tool_rates: dict[str, float] | None = None,
) -> dict[str, Any]:
    """保存固定算力与/或视频每秒算力；默认递增 version 以便 quoteToken 失效刷新。"""
    current = await get_canvas_tool_pricing(db)
    next_tools = dict(current["tools"])
    next_rates = dict(current["videoToolRates"])
    for tid, _, group in CANVAS_TOOL_CREDIT_DEFS:
        if tid not in tools:
            continue
        if group == "canvas_video":
            # 允许管理端仍把视频价放在 tools 里提交（兼容旧 PUT 体）
            next_rates[tid] = normalize_credit_amount(tools[tid], default=0)
        else:
            next_tools[tid] = normalize_credit_amount(tools[tid], default=0)
    if video_tool_rates:
        for tid, rate in video_tool_rates.items():
            if tid in CANVAS_VIDEO_BILLING_TOOL_IDS:
                next_rates[tid] = normalize_credit_amount(rate, default=0)
    next_version = int(current["version"]) + (1 if bump_version else 0)
    if next_version < 1:
        next_version = 1
    payload = {"version": next_version, "tools": next_tools, "videoToolRates": next_rates}
    await set_canvas_tool_pricing_raw(db, payload)
    return payload


def _parse_bill_seconds(raw: Any, *, default: int | None = None) -> int | None:
    """解析计费秒数；非法则返回 default。"""
    if raw is None or raw == "":
        return default
    try:
        n = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    if n < _MIN_BILL_SEC:
        return _MIN_BILL_SEC if default is None else default
    return min(_MAX_BILL_SEC, n)


def resolve_canvas_video_bill_seconds(
    generation_options: dict[str, Any] | None,
    input_params: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """解析输入/输出计费秒数。

    优先 generationOptions.inputVideoSeconds / outputVideoSeconds；
    其次 input_params.durationSec / 裁剪区间；缺一则用另一端补齐。
    """
    opts = generation_options if isinstance(generation_options, dict) else {}
    params = input_params if isinstance(input_params, dict) else {}

    input_sec = _parse_bill_seconds(
        opts.get("inputVideoSeconds") or opts.get("input_video_seconds")
    )
    output_sec = _parse_bill_seconds(
        opts.get("outputVideoSeconds")
        or opts.get("output_video_seconds")
        or opts.get("duration")
    )

    # 从节点参数兜底：裁剪区间或 durationSec
    fallback: int | None = None
    trim = params.get("inlineVideoTrim") or params.get("videoTrim")
    if isinstance(trim, dict):
        try:
            in_s = float(trim.get("inSec"))
            out_s = float(trim.get("outSec"))
            if out_s > in_s:
                fallback = max(_MIN_BILL_SEC, min(_MAX_BILL_SEC, int(round(out_s - in_s))))
        except (TypeError, ValueError):
            pass
    if fallback is None:
        fallback = _parse_bill_seconds(
            params.get("durationSec") or params.get("duration_sec") or params.get("duration"),
            default=None,
        )

    if input_sec is None and output_sec is None:
        # 未传时长：按 1 秒两端计，避免 0 价误判「未配置」；真实提交应带时长
        sec = fallback if fallback is not None else _MIN_BILL_SEC
        return sec, sec
    if input_sec is None:
        input_sec = output_sec if output_sec is not None else (fallback or _MIN_BILL_SEC)
    if output_sec is None:
        output_sec = input_sec
    return input_sec, output_sec


def _as_str_generation_options(raw: dict[str, Any] | None) -> dict[str, str]:
    """将报价/提交选项规范为 str→str（供 quote_generation_cost）。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        out[str(key)] = str(value)
    return out


def _prepare_model_for_canvas_tool_quote(model: Model) -> Model:
    """补全 generationPresets，使默认档报价与定价页/用户侧预扣一致。

    目录缓存行若缺预设，quote_generation_cost 无法落到默认清晰度，预览会变成 0。
    """
    params = dict(model.parameters) if isinstance(model.parameters, dict) else {}
    presets = params.get("generationPresets")
    if not isinstance(presets, dict) or not (presets.get("groups") or []):
        from .generation_presets import default_presets_for_model

        filled = default_presets_for_model(
            str(model.name or ""),
            str(model.provider or ""),
            str(model.category or "image"),
        )
        if filled:
            params["generationPresets"] = filled
            model.parameters = params
    return model


def _fallback_list_price_from_pricing(model: Model) -> float:
    """默认档报价为 0 时，从定价配置抽一条可展示的列表价（与定价页同一份 JSON）。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    pricing = extract_pricing_config(params, category=getattr(model, "category", None))
    base = normalize_credit_amount(pricing.get("baseCost"), default=0)
    if base > 0:
        return base
    options = pricing.get("options") if isinstance(pricing.get("options"), dict) else {}
    for group_id in ("resolution", "quality", "size"):
        group = options.get(group_id)
        if not isinstance(group, dict) or not group:
            continue
        for prefer in ("2k", "1k", "4k", "720p", "1080p", "480p", "medium", "high"):
            if prefer in group:
                value = normalize_credit_amount(group[prefer], default=0)
                if value > 0:
                    return value
        for raw in group.values():
            value = normalize_credit_amount(raw, default=0)
            if value > 0:
                return value
    matrix = pricing.get("matrix") if isinstance(pricing.get("matrix"), dict) else {}
    for prefer_q in ("medium", "high", "low"):
        row = matrix.get(prefer_q)
        if not isinstance(row, dict):
            continue
        for prefer_r in ("2k", "1k", "4k", "720p", "1080p"):
            if prefer_r in row:
                value = normalize_credit_amount(row[prefer_r], default=0)
                if value > 0:
                    return value
        for raw in row.values():
            value = normalize_credit_amount(raw, default=0)
            if value > 0:
                return value
    for row in matrix.values():
        if not isinstance(row, dict):
            continue
        for raw in row.values():
            value = normalize_credit_amount(raw, default=0)
            if value > 0:
                return value
    return 0.0


def _default_video_rate_from_model(model: Model) -> float:
    """从主模型 pricing 取默认每秒算力；非 video_per_second 返回 0（改走按次报价）。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    pricing = extract_pricing_config(params, category=getattr(model, "category", None))
    if (pricing.get("mode") or "") != "video_per_second":
        return 0.0
    rate_group = str(pricing.get("rateGroupId") or "resolution")
    options = pricing.get("options") if isinstance(pricing.get("options"), dict) else {}
    group = options.get(rate_group) if isinstance(options.get(rate_group), dict) else {}
    if isinstance(group, dict) and group:
        for prefer in ("720p", "1080p", "480p", "2k", "4k", "hd", "sd"):
            if prefer in group:
                return normalize_credit_amount(group[prefer], default=0)
        try:
            return normalize_credit_amount(next(iter(group.values())), default=0)
        except StopIteration:
            pass
    return normalize_credit_amount(pricing.get("baseCost"), default=0)


def _stamp_canvas_tool_quote(
    quote: CreditQuote,
    *,
    tool_id: str,
    billing_mode: str,
    extra_snapshot: dict[str, str] | None = None,
) -> CreditQuote:
    """在模型报价上标注 canvasTool，便于凭证与顶栏识别。"""
    snapshot = dict(quote.option_snapshot or {})
    snapshot["canvasTool"] = tool_id
    snapshot["billingMode"] = billing_mode
    snapshot["priceSource"] = "model"
    if extra_snapshot:
        snapshot.update(extra_snapshot)
    return CreditQuote(
        model=quote.model,
        total=quote.total,
        base=quote.base,
        breakdown=list(quote.breakdown),
        pricing_version=int(quote.pricing_version or 1),
        option_snapshot=snapshot,
        credits_enabled=quote.credits_enabled,
    )


def _quote_from_platform_fixed(
    model: Model,
    tool_id: str,
    pricing: dict[str, Any],
    *,
    generation_options: dict[str, Any] | None = None,
    input_params: dict[str, Any] | None = None,
) -> CreditQuote:
    """未纳管主模型的工具：沿用 platform canvas_tool_pricing 固定价/每秒价。"""
    tid = require_canvas_tool_id(tool_id)
    cfg = normalize_canvas_tool_pricing(pricing)
    label = canvas_tool_label(tid)

    if is_canvas_video_billing_tool(tid):
        rate = normalize_credit_amount(
            cfg["videoToolRates"].get(tid, DEFAULT_VIDEO_TOOL_CREDITS_PER_SEC),
            default=DEFAULT_VIDEO_TOOL_CREDITS_PER_SEC,
        )
        input_sec, output_sec = resolve_canvas_video_bill_seconds(
            generation_options, input_params
        )
        bill_sec = input_sec + output_sec
        cost = normalize_credit_amount(rate * bill_sec, default=0)
        breakdown = [
            CreditBreakdownItem(
                group_id="canvas_tool_rate",
                item_id=tid,
                label=f"{label}·每秒算力",
                cost=rate,
            ),
            CreditBreakdownItem(
                group_id="inputVideoSeconds",
                item_id="input",
                label=f"输入参考视频 {input_sec} 秒",
                cost=normalize_credit_amount(rate * input_sec, default=0),
            ),
            CreditBreakdownItem(
                group_id="outputVideoSeconds",
                item_id="output",
                label=f"生成视频 {output_sec} 秒",
                cost=normalize_credit_amount(rate * output_sec, default=0),
            ),
        ]
        return CreditQuote(
            model=model.name,
            total=cost,
            base=0,
            breakdown=breakdown,
            pricing_version=int(cfg["version"]),
            option_snapshot={
                "canvasTool": tid,
                "billingMode": BILLING_MODE_VIDEO_IO_PER_SEC,
                "creditsPerSecond": str(rate),
                "inputVideoSeconds": str(input_sec),
                "outputVideoSeconds": str(output_sec),
                "priceSource": "platform",
            },
            credits_enabled=credits_enabled(),
        )

    cost = normalize_credit_amount(
        cfg["tools"].get(tid, DEFAULT_CANVAS_TOOL_CREDIT), default=DEFAULT_CANVAS_TOOL_CREDIT
    )
    breakdown = [
        CreditBreakdownItem(
            group_id="canvas_tool",
            item_id=tid,
            label=label,
            cost=cost,
        )
    ]
    return CreditQuote(
        model=model.name,
        total=cost,
        base=cost,
        breakdown=breakdown,
        pricing_version=int(cfg["version"]),
        option_snapshot={
            "canvasTool": tid,
            "billingMode": BILLING_MODE_FIXED,
            "priceSource": "platform",
        },
        credits_enabled=credits_enabled(),
    )


def quote_canvas_tool_cost(
    model: Model,
    tool_id: str,
    pricing: dict[str, Any],
    *,
    generation_options: dict[str, Any] | None = None,
    input_params: dict[str, Any] | None = None,
    follow_model_pricing: bool | None = None,
) -> CreditQuote:
    """画布工具报价：优先跟主模型档位价；未纳管工具回退平台固定价。

    ``follow_model_pricing``：默认对 ``CANVAS_TOOL_MODEL_IDS`` 内工具为 True。
    """
    tid = require_canvas_tool_id(tool_id)
    label = canvas_tool_label(tid)
    model = _prepare_model_for_canvas_tool_quote(model)

    # 延迟导入避免与 canvas_tool_models 循环依赖
    from .canvas_tool_models import CANVAS_TOOL_MODEL_IDS

    use_model = (
        bool(follow_model_pricing)
        if follow_model_pricing is not None
        else tid in CANVAS_TOOL_MODEL_IDS
    )
    if not use_model:
        return _quote_from_platform_fixed(
            model,
            tid,
            pricing,
            generation_options=generation_options,
            input_params=input_params,
        )

    str_opts = _as_str_generation_options(generation_options)

    # 视频工具 + 主模型按秒计价 → (输入+输出)×主模型默认每秒算力
    if is_canvas_video_billing_tool(tid):
        rate = _default_video_rate_from_model(model)
        if rate > 0:
            params = model.parameters if isinstance(model.parameters, dict) else {}
            model_pricing = extract_pricing_config(
                params, category=getattr(model, "category", None)
            )
            input_sec, output_sec = resolve_canvas_video_bill_seconds(
                generation_options, input_params
            )
            cost = normalize_credit_amount(rate * (input_sec + output_sec), default=0)
            breakdown = [
                CreditBreakdownItem(
                    group_id="canvas_tool_rate",
                    item_id=tid,
                    label=f"{label}·主模型每秒算力",
                    cost=rate,
                ),
                CreditBreakdownItem(
                    group_id="inputVideoSeconds",
                    item_id="input",
                    label=f"输入参考视频 {input_sec} 秒",
                    cost=normalize_credit_amount(rate * input_sec, default=0),
                ),
                CreditBreakdownItem(
                    group_id="outputVideoSeconds",
                    item_id="output",
                    label=f"生成视频 {output_sec} 秒",
                    cost=normalize_credit_amount(rate * output_sec, default=0),
                ),
            ]
            return CreditQuote(
                model=model.name,
                total=cost,
                base=0,
                breakdown=breakdown,
                pricing_version=int(model_pricing.get("version") or 1),
                option_snapshot={
                    "canvasTool": tid,
                    "billingMode": BILLING_MODE_VIDEO_IO_PER_SEC,
                    "creditsPerSecond": str(rate),
                    "inputVideoSeconds": str(input_sec),
                    "outputVideoSeconds": str(output_sec),
                    "priceSource": "model",
                },
                credits_enabled=credits_enabled(),
            )

    # 图片/文本/按次视频模型：与普通生成同一套模型计价（默认档位）
    quote = quote_generation_cost(model, str_opts or None, strict=False)
    return _stamp_canvas_tool_quote(
        quote,
        tool_id=tid,
        billing_mode=BILLING_MODE_FIXED,
        extra_snapshot={"toolLabel": label},
    )


def canvas_tool_pricing_public_items(pricing: dict[str, Any]) -> list[dict[str, Any]]:
    """仅平台固定价表（无模型解析）。展示请优先用 ``build_canvas_tool_pricing_view``。"""
    cfg = normalize_canvas_tool_pricing(pricing)
    items: list[dict[str, Any]] = []
    for tid, label, group in CANVAS_TOOL_CREDIT_DEFS:
        if group == "canvas_video":
            rate = normalize_credit_amount(
                cfg["videoToolRates"].get(tid, DEFAULT_VIDEO_TOOL_CREDITS_PER_SEC),
                default=DEFAULT_VIDEO_TOOL_CREDITS_PER_SEC,
            )
            items.append(
                {
                    "toolId": tid,
                    "label": label,
                    "group": group,
                    "billingMode": BILLING_MODE_VIDEO_IO_PER_SEC,
                    "creditCost": rate,
                    "creditsPerSecond": rate,
                    "priceSource": "platform",
                    "primaryModel": None,
                    "primaryModelDisplayName": None,
                    "editable": True,
                }
            )
        else:
            cost = normalize_credit_amount(
                cfg["tools"].get(tid, DEFAULT_CANVAS_TOOL_CREDIT),
                default=DEFAULT_CANVAS_TOOL_CREDIT,
            )
            items.append(
                {
                    "toolId": tid,
                    "label": label,
                    "group": group,
                    "billingMode": BILLING_MODE_FIXED,
                    "creditCost": cost,
                    "creditsPerSecond": None,
                    "priceSource": "platform",
                    "primaryModel": None,
                    "primaryModelDisplayName": None,
                    "editable": True,
                }
            )
    return items


def _model_display_name(model: Model) -> str:
    """管理端/顶栏展示用主模型名（优先中文 display_name）。"""
    return str(getattr(model, "display_name", None) or "").strip() or str(model.name or "")


def _public_item_from_model(
    model: Model,
    *,
    tool_id: str,
    label: str,
    group: str,
) -> dict[str, Any]:
    """由主模型生成展示项：视频按秒模型给每秒价，其余给默认档位单次预扣价。"""
    model = _prepare_model_for_canvas_tool_quote(model)
    display_name = _model_display_name(model)
    if group == "canvas_video":
        rate = _default_video_rate_from_model(model)
        if rate > 0:
            return {
                "toolId": tool_id,
                "label": label,
                "group": group,
                "billingMode": BILLING_MODE_VIDEO_IO_PER_SEC,
                "creditCost": rate,
                "creditsPerSecond": rate,
                "priceSource": "model",
                "primaryModel": model.name,
                "primaryModelDisplayName": display_name,
                "editable": False,
            }
    quote = quote_generation_cost(model, None, strict=False)
    cost = quote.total
    if cost <= 0:
        cost = _fallback_list_price_from_pricing(model)
    return {
        "toolId": tool_id,
        "label": label,
        "group": group,
        "billingMode": BILLING_MODE_FIXED,
        "creditCost": cost,
        "creditsPerSecond": None,
        "priceSource": "model",
        "primaryModel": model.name,
        "primaryModelDisplayName": display_name,
        "editable": False,
        "pricingVersion": quote.pricing_version,
    }


async def build_canvas_tool_pricing_view(db: AsyncSession) -> dict[str, Any]:
    """顶栏/管理端展示：有主模型的工具跟模型价；其余跟平台固定价。"""
    from .canvas_tool_models import (
        CANVAS_TOOL_MODEL_IDS,
        _TOOL_CATEGORY,
        _TOOL_DEFAULT_PRIMARY,
        get_canvas_tool_models,
    )
    from .model_catalog_cache import get_cached_model_by_name

    platform = await get_canvas_tool_pricing(db)
    tool_models = await get_canvas_tool_models(db)
    platform_items = {
        row["toolId"]: row for row in canvas_tool_pricing_public_items(platform)
    }
    version = max(1, int(tool_models.get("version") or 1), int(platform.get("version") or 1))
    items: list[dict[str, Any]] = []

    for tid, label, group in CANVAS_TOOL_CREDIT_DEFS:
        if tid in CANVAS_TOOL_MODEL_IDS:
            entry = (tool_models.get("tools") or {}).get(tid) or {}
            primary = str(entry.get("primary") or _TOOL_DEFAULT_PRIMARY.get(tid) or "").strip()
            category = _TOOL_CATEGORY.get(tid) or "image"
            model = None
            if primary:
                # 先按工具类目查；后台若把跨类目模型配给该功能，再按名称兜底
                model = await get_cached_model_by_name(
                    db, primary, category=category, available_only=False
                )
                if model is None:
                    model = await get_cached_model_by_name(
                        db, primary, available_only=False
                    )
            if model is not None:
                row = _public_item_from_model(
                    model, tool_id=tid, label=label, group=group
                )
                pv = row.get("pricingVersion")
                if isinstance(pv, int):
                    version = max(version, pv)
                items.append(row)
                continue
        # 无主模型或模型缺失：平台固定价（如宫格切分）
        fallback = platform_items.get(tid) or {
            "toolId": tid,
            "label": label,
            "group": group,
            "billingMode": BILLING_MODE_FIXED,
            "creditCost": DEFAULT_CANVAS_TOOL_CREDIT,
            "creditsPerSecond": None,
            "priceSource": "platform",
            "primaryModel": None,
            "primaryModelDisplayName": None,
            "editable": True,
        }
        items.append(fallback)

    return {"version": version, "items": items, "platformVersion": int(platform["version"])}
