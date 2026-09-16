"""画布生成的权威计价 —— 唯一计价入口，附带 HMAC 报价凭证（quoteToken）签发与校验。

计价与扣款分离：本模块只决定「扣多少」；从哪类 lot 扣由 credit_lots 决定。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..core.credit_amount import normalize_credit_amount

from ..core.config import get_settings
from ..models.job import Model
from .generation_presets import _find_item, normalize_option_ids

QUOTE_TOKEN_TTL_S = 120


def credits_enabled() -> bool:
    """算力功能全局开关（CREDITS_ENABLED）。"""
    return bool(get_settings().credits_enabled)


@dataclass
class CreditBreakdownItem:
    """报价明细中的一项（某选项组的某选项及其对应算力）。"""

    group_id: str
    item_id: str
    label: str
    cost: float

    def to_dict(self) -> dict[str, Any]:
        """序列化为返回给前端/凭证的字典。"""
        return asdict(self)


@dataclass
class CreditQuote:
    """一次生成的完整报价结果（总额、明细、定价版本、选项快照）。"""

    model: str
    total: float
    base: float
    breakdown: list[CreditBreakdownItem] = field(default_factory=list)
    pricing_version: int = 1
    option_snapshot: dict[str, str] = field(default_factory=dict)
    credits_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        """序列化为 API 响应用的驼峰字段字典。"""
        return {
            "model": self.model,
            "total": self.total,
            "base": self.base,
            "breakdown": [item.to_dict() for item in self.breakdown],
            "pricingVersion": self.pricing_version,
            "optionSnapshot": self.option_snapshot,
            "creditsEnabled": self.credits_enabled,
        }


def _model_params(model: Model) -> dict[str, Any]:
    """取模型 parameters JSON 的副本（非 dict 时返回空字典）。"""
    params = model.parameters if isinstance(model.parameters, dict) else {}
    return dict(params)


def extract_pricing_config(
    params: dict[str, Any],
    *,
    category: str | None = None,
) -> dict[str, Any]:
    """从 parameters.pricing 读取定价配置，兼容旧 creditCost 字段与按类目默认值。"""
    raw = params.get("pricing")
    presets = params.get("generationPresets")
    presets_dict = presets if isinstance(presets, dict) else None

    if isinstance(raw, dict) and raw:
        config = dict(raw)
    else:
        legacy = params.get("creditCost") or params.get("credit_cost") or 0
        base = normalize_credit_amount(legacy, default=0)
        if base > 0:
            config = {
                "version": 1,
                "baseCost": base,
                "mode": "additive",
                "minCost": 0,
                "options": {},
            }
        elif category:
            config = dict(default_pricing_for_model(category, presets_dict))
        else:
            config = {
                "version": 1,
                "baseCost": 0,
                "mode": "additive",
                "minCost": 0,
                "options": {},
            }

    try:
        config["version"] = max(int(config.get("version") or 1), 1)
    except (TypeError, ValueError):
        config["version"] = 1

    # 基础/最低算力：允许一位小数
    config["baseCost"] = normalize_credit_amount(config.get("baseCost"), default=0)
    config["minCost"] = normalize_credit_amount(config.get("minCost"), default=0)

    mode = str(config.get("mode") or "additive").strip().lower()
    config["mode"] = mode if mode in (
        "additive",
        "primary_group",
        "video_per_second",
        "image_by_tier",
        "image_matrix",
    ) else "additive"
    if config["mode"] == "video_per_second":
        config.setdefault("rateGroupId", "resolution")
        config.setdefault("durationGroupId", "duration")
    if config["mode"] == "image_by_tier":
        config.setdefault("tierGroupIds", ["quality", "resolution", "size"])
        # 预设无画质/清晰度时（如悠船 Niji 7），按次基础算力，避免报价恒为 0
        tier_ids = {"quality", "resolution", "size"}
        has_tier = bool(
            presets_dict
            and any(
                isinstance(g, dict) and str(g.get("id") or "") in tier_ids
                for g in (presets_dict.get("groups") or [])
            )
        )
        if not has_tier:
            config["mode"] = "additive"
            config.pop("tierGroupIds", None)
    if config["mode"] == "image_matrix":
        # 图片二维矩阵计价：行=画质、列=清晰度，matrix[画质][清晰度]=算力
        # 预设已不再是「画质×清晰度」结构时（如全能图片 G 低价版仅 1K/2K/4K），
        # 读时降级为 image_by_tier，避免报价恒为 0、管理端无法设价。
        if not _is_matrix_image_presets(presets_dict):
            matrix = config.get("matrix") if isinstance(config.get("matrix"), dict) else {}
            res_options: dict[str, float] = {}
            # 优先取 medium 行，其次 high / low，再合并各行已有清晰度价
            for prefer_q in ("medium", "high", "low"):
                row = matrix.get(prefer_q)
                if isinstance(row, dict) and row:
                    for rid, raw in row.items():
                        res_options[str(rid)] = normalize_credit_amount(raw, default=0)
                    break
            if not res_options:
                for row in matrix.values():
                    if not isinstance(row, dict):
                        continue
                    for rid, raw in row.items():
                        if str(rid) in res_options:
                            continue
                        res_options[str(rid)] = normalize_credit_amount(raw, default=0)
            config["mode"] = "image_by_tier"
            config["tierGroupIds"] = ["quality", "resolution", "size"]
            config["options"] = {"resolution": res_options} if res_options else (
                config.get("options") if isinstance(config.get("options"), dict) else {}
            )
            config.pop("matrix", None)
            config.pop("matrixGroupIds", None)
        else:
            config.setdefault("matrixGroupIds", ["quality", "resolution"])
            matrix = config.get("matrix")
            config["matrix"] = matrix if isinstance(matrix, dict) else {}

    options = config.get("options")
    config["options"] = options if isinstance(options, dict) else {}
    return config


def _has_preset_group(presets: dict[str, Any] | None, group_id: str) -> bool:
    """判断预设中是否存在指定选项组。"""
    if not presets:
        return False
    return any(g.get("id") == group_id for g in (presets.get("groups") or []))


def _duration_seconds(
    presets: dict[str, Any] | None,
    option_snapshot: dict[str, str],
) -> int:
    """从选项快照解析视频时长秒数（用于按秒计价），默认 5 秒。"""
    item_id = option_snapshot.get("duration")
    if item_id and str(item_id).isdigit():
        return max(1, int(item_id))
    if presets and item_id:
        for group in presets.get("groups") or []:
            if group.get("id") != "duration":
                continue
            item = _find_item(group, item_id)
            if item and isinstance(item.get("api"), dict):
                raw = item["api"].get("duration")
                if raw is not None:
                    try:
                        return max(1, int(raw))
                    except (TypeError, ValueError):
                        pass
    return 5


def _image_output_count(
    presets: dict[str, Any] | None,
    option_snapshot: dict[str, str],
) -> int:
    """从生成数量选项解析张数（presets.items[].api.n），范围 1–6。"""
    for gid in ("count", "n"):
        item_id = option_snapshot.get(gid)
        if not item_id:
            continue
        if presets:
            group = _preset_group(presets, gid)
            item = _find_item(group, item_id) if group else None
            if item and isinstance(item.get("api"), dict) and item["api"].get("n") is not None:
                try:
                    return max(1, min(int(item["api"]["n"]), 6))
                except (TypeError, ValueError):
                    pass
        if str(item_id).isdigit():
            try:
                return max(1, min(int(item_id), 6))
            except (TypeError, ValueError):
                pass
    return 1


def _image_tier_cost(group_id: str, item_id: str, label: str) -> int:
    """图片按画质/清晰度分档的默认算力（生成默认定价用）。"""
    lid = str(item_id).lower()
    lab = label.lower()
    if group_id in ("size", "resolution"):
        if "8k" in lab or lid in ("8k", "2160p"):
            return 10
        if "4k" in lab or lid in ("4k", "2160p"):
            return 8
        if "3k" in lab or lid == "3k":
            return 5
        if lid in ("2k", "720", "1080", "standard", "default"):
            return 0
        return 2
    if group_id == "quality":
        if lid in ("standard", "default"):
            return 0
        if lid in ("high", "detail", "pro"):
            return 3 if lid == "high" else 5
        return 2
    return 0


def _preset_group(presets: dict[str, Any] | None, group_id: str) -> dict[str, Any] | None:
    """取预设中指定 id 的选项组。"""
    if not presets:
        return None
    for group in presets.get("groups") or []:
        if group.get("id") == group_id:
            return group
    return None


def _enabled_item_ids(group: dict[str, Any] | None) -> list[str]:
    """取选项组内启用项的 id 列表（保持顺序）。"""
    if not group:
        return []
    ids: list[str] = []
    for item in group.get("items") or []:
        if item.get("enabled") is False:
            continue
        iid = item.get("id")
        if iid:
            ids.append(str(iid))
    return ids


def _is_matrix_image_presets(presets: dict[str, Any] | None) -> bool:
    """判断图片预设是否为「画质(low/medium/high) × 清晰度」二维矩阵结构。"""
    if not presets or not isinstance(presets, dict):
        return False
    quality = _preset_group(presets, "quality")
    resolution = _preset_group(presets, "resolution") or _preset_group(presets, "size")
    if not quality or not resolution:
        return False
    q_ids = set(_enabled_item_ids(quality))
    return {"low", "medium", "high"}.issubset(q_ids)


def _default_image_matrix_pricing(presets: dict[str, Any] | None) -> dict[str, Any]:
    """按画质×清晰度生成空的二维矩阵定价（各档 0，待管理端后台填写）。"""
    quality = _preset_group(presets, "quality")
    res_group_id = "resolution" if _preset_group(presets, "resolution") else "size"
    resolution = _preset_group(presets, res_group_id)
    q_ids = _enabled_item_ids(quality)
    r_ids = _enabled_item_ids(resolution)
    matrix: dict[str, dict[str, int]] = {
        q: {r: 0 for r in r_ids} for q in q_ids
    }
    return {
        "version": 1,
        "baseCost": 0,
        "mode": "image_matrix",
        "minCost": 0,
        "matrixGroupIds": ["quality", res_group_id],
        "matrix": matrix,
    }


def default_pricing_for_model(category: str, presets: dict[str, Any] | None = None) -> dict[str, Any]:
    """按类目（文本/图片/视频/音频）生成默认定价配置，用于同步模型目录时初始化。"""
    base_by_category = {
        "text": 1,
        "image": 5,
        "video": 0,
        "audio": 3,
        "tool": 5,
    }
    base = base_by_category.get(category, 1)

    if category == "video" and _has_preset_group(presets, "duration"):
        pricing: dict[str, Any] = {
            "version": 1,
            "baseCost": 0,
            "mode": "video_per_second",
            "minCost": 0,
            "rateGroupId": "resolution",
            "durationGroupId": "duration",
            "options": {},
        }
        rate_costs: dict[str, int] = {}
        for group in presets.get("groups") or []:
            if group.get("id") != "resolution":
                continue
            for item in group.get("items") or []:
                if item.get("enabled") is False:
                    continue
                iid = item.get("id")
                if not iid:
                    continue
                rid = str(iid).replace("p", "").replace("P", "")
                if rid in ("720", "480"):
                    rate_costs[str(iid)] = 10
                elif rid in ("1080",):
                    rate_costs[str(iid)] = 20
                else:
                    rate_costs[str(iid)] = 3
        if rate_costs:
            pricing["options"] = {"resolution": rate_costs}
        return pricing

    if category == "image":
        # 画质组为 low/medium/high 且存在清晰度组时，采用二维矩阵定价（画质 × 清晰度 9 档独立）
        if _is_matrix_image_presets(presets):
            return _default_image_matrix_pricing(presets)

        pricing = {
            "version": 1,
            "baseCost": 0,
            "mode": "image_by_tier",
            "minCost": 0,
            "tierGroupIds": ["quality", "resolution", "size"],
            "options": {},
        }
        if not presets or not isinstance(presets, dict):
            return pricing

        tier_ids = {"quality", "resolution", "size"}
        option_costs: dict[str, dict[str, int]] = {}
        for group in presets.get("groups") or []:
            gid = group.get("id")
            if not gid or str(gid) not in tier_ids:
                continue
            group_costs: dict[str, int] = {}
            for item in group.get("items") or []:
                if item.get("enabled") is False:
                    continue
                iid = item.get("id")
                if not iid:
                    continue
                label = str(item.get("label") or iid)
                group_costs[str(iid)] = _image_tier_cost(str(gid), str(iid), label)
            if group_costs:
                option_costs[str(gid)] = group_costs
        if option_costs:
            pricing["options"] = option_costs
        return pricing

    pricing = {
        "version": 1,
        "baseCost": base,
        "mode": "additive",
        "minCost": 0,
        "options": {},
    }

    if not presets or not isinstance(presets, dict):
        return pricing

    option_costs: dict[str, dict[str, int]] = {}
    for group in presets.get("groups") or []:
        gid = group.get("id")
        if not gid:
            continue
        if gid == "duration":
            continue
        group_costs: dict[str, int] = {}
        for item in group.get("items") or []:
            if item.get("enabled") is False:
                continue
            iid = item.get("id")
            if not iid:
                continue
            label = str(item.get("label") or iid)
            if category == "image" and gid in ("size", "resolution", "quality"):
                cost = _image_tier_cost(gid, str(iid), label)
            elif gid in ("size", "resolution"):
                cost = _image_tier_cost(gid, str(iid), label)
            elif gid == "quality":
                cost = _image_tier_cost(gid, str(iid), label)
            else:
                cost = 0
            group_costs[str(iid)] = cost
        if group_costs:
            option_costs[str(gid)] = group_costs

    if option_costs:
        pricing["options"] = option_costs
    return pricing


def _option_cost(pricing: dict[str, Any], group_id: str, item_id: str) -> float:
    """从定价配置读取某选项组某选项的算力（缺失/非法为 0；一位小数）。"""
    options = pricing.get("options") or {}
    group = options.get(group_id)
    if not isinstance(group, dict):
        return 0.0
    return normalize_credit_amount(group.get(item_id), default=0)


def _option_cost_from_root(
    options_root: dict[str, Any] | None,
    group_id: str,
    item_id: str,
) -> float:
    """从任意 options 根（含 optionsWithVideoReference）读取单价。"""
    if not isinstance(options_root, dict):
        return 0.0
    group = options_root.get(group_id)
    if not isinstance(group, dict):
        return 0.0
    return normalize_credit_amount(group.get(item_id), default=0)


def _video_rate_for_options(
    pricing: dict[str, Any],
    option_snapshot: dict[str, str],
    rate_group: str,
    selected_rate: str | None,
) -> float:
    """video_per_second：有参考视频时优先用 optionsWithVideoReference。"""
    if not selected_rate:
        return 0.0
    ref_gid = str(pricing.get("refVideoGroupId") or "refVideo")
    if option_snapshot.get(ref_gid) == "with":
        with_opts = pricing.get("optionsWithVideoReference")
        rate = _option_cost_from_root(
            with_opts if isinstance(with_opts, dict) else None,
            rate_group,
            selected_rate,
        )
        if rate > 0:
            return rate
    return _option_cost(pricing, rate_group, selected_rate)


def input_params_has_video_reference(input_params: dict[str, Any] | None) -> bool:
    """从提交 input_params.references 判断是否含参考视频。"""
    if not isinstance(input_params, dict):
        return False
    refs = input_params.get("references")
    if not isinstance(refs, list):
        return False
    for ref in refs:
        if isinstance(ref, dict) and str(ref.get("type") or "").lower() == "video":
            return True
    return False


def apply_ref_video_billing_option(
    model: Model,
    generation_options: dict[str, str] | None,
    input_params: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """多模态模型：按是否接入参考视频写入 refVideo=with|none（供报价/预扣对齐）。"""
    params = _model_params(model)
    pricing = params.get("pricing") if isinstance(params.get("pricing"), dict) else {}
    if not isinstance(pricing.get("optionsWithVideoReference"), dict):
        return generation_options
    presets = params.get("generationPresets")
    presets_dict = presets if isinstance(presets, dict) else None
    group_ids = _preset_group_ids(presets_dict)
    ref_gid = str(pricing.get("refVideoGroupId") or "refVideo")
    if ref_gid not in group_ids:
        return generation_options
    opts = dict(generation_options or {})
    opts[ref_gid] = "with" if input_params_has_video_reference(input_params) else "none"
    return opts


def _count_input_images(input_params: dict[str, Any] | None) -> int:
    """统计提交 references 中的参考图数量（最多按 9 计）。"""
    if not isinstance(input_params, dict):
        return 0
    refs = input_params.get("references")
    if not isinstance(refs, list):
        return 0
    n = 0
    for ref in refs:
        if isinstance(ref, dict) and str(ref.get("type") or "").lower() == "image":
            n += 1
    return max(0, min(9, n))


def _iter_video_refs(input_params: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(input_params, dict):
        return []
    refs = input_params.get("references")
    if not isinstance(refs, list):
        return []
    out: list[dict[str, Any]] = []
    for ref in refs:
        if isinstance(ref, dict) and str(ref.get("type") or "").lower() == "video":
            out.append(ref)
    return out


def _ref_video_duration_raw(ref: dict[str, Any]) -> Any:
    raw = ref.get("durationSec")
    if raw is None:
        raw = ref.get("duration_sec")
    if raw is None:
        raw = ref.get("duration")
    return raw


def _sum_input_video_seconds(input_params: dict[str, Any] | None) -> int:
    """汇总参考视频秒数：优先读 durationSec/duration；缺失时按 5 秒估（上游 2–15s）。"""
    total = 0
    for ref in _iter_video_refs(input_params):
        raw = _ref_video_duration_raw(ref)
        try:
            sec = int(round(float(raw))) if raw is not None else 5
        except (TypeError, ValueError):
            sec = 5
        # 与上游参考视频时长窗口对齐：单条约 2–15 秒
        total += max(2, min(15, sec if sec > 0 else 5))
    return max(0, min(45, total))


def _parse_usage_int(raw: Any, *, lo: int, hi: int) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return max(lo, min(hi, int(float(text))))
    except (TypeError, ValueError):
        return None


def apply_material_usage_billing_option(
    model: Model,
    generation_options: dict[str, str] | None,
    input_params: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """MiniMax-H3 等：对齐 inputVideoSeconds / inputImageCount。

    优先保留前端已写入的正数用量（与 quoteToken 一致）；
    仅在未传或有参考却报 0（漏计）时，才用 references 汇总覆盖。
    """
    params = _model_params(model)
    pricing = params.get("pricing") if isinstance(params.get("pricing"), dict) else {}
    # 注册表/库内开关；按模型名兜底，避免管理员改价丢掉开关后无法计素材用量 / 与前端强制字段错位
    model_name = str(getattr(model, "name", "") or "").strip().lower()
    force_video = model_name.startswith("rh_minimax_hailuo_h3") or model_name == "rh_seedance_25_r2v"
    force_image = model_name.startswith("rh_minimax_hailuo_h3") or model_name.startswith(
        "qwen_image"
    )
    needs_video = bool(pricing.get("billInputVideoSeconds")) or force_video
    needs_image = isinstance(pricing.get("extraImageBilling"), dict) or force_image
    if not needs_video and not needs_image:
        return generation_options
    opts = dict(generation_options or {})
    if needs_video:
        computed = _sum_input_video_seconds(input_params)
        n_vid = len(_iter_video_refs(input_params))
        client_secs = _parse_usage_int(opts.get("inputVideoSeconds"), lo=0, hi=45)
        if client_secs is None:
            opts["inputVideoSeconds"] = str(computed)
        elif n_vid > 0 and client_secs == 0 and computed > 0:
            # 仅当有参考却报 0（漏计）时覆盖；已报正数则保留，避免估时与 durationSec 微差冲掉 quoteToken
            opts["inputVideoSeconds"] = str(computed)
        else:
            opts["inputVideoSeconds"] = str(client_secs if client_secs is not None else computed)
    if needs_image:
        computed_img = _count_input_images(input_params)
        client_img = _parse_usage_int(opts.get("inputImageCount"), lo=0, hi=9)
        if client_img is None:
            opts["inputImageCount"] = str(computed_img)
        elif computed_img > 0 and client_img == 0:
            # 仅漏计（报 0）时覆盖；已报正数保留，避免与 quoteToken 死循环
            opts["inputImageCount"] = str(computed_img)
        else:
            opts["inputImageCount"] = str(client_img if client_img is not None else computed_img)
    return opts


def _merge_billing_usage_snapshot(
    pricing: dict[str, Any],
    option_snapshot: dict[str, str],
    generation_options: dict[str, str] | None,
    *,
    model_name: str | None = None,
) -> dict[str, str]:
    """将非预设组的素材用量字段并入 option_snapshot（报价/预扣用）。"""
    out = dict(option_snapshot)
    incoming = generation_options or {}
    name = str(model_name or "").strip().lower()
    # 与 apply_material_usage_billing_option 同一套模型名兜底，保证 snapshot 键稳定
    merge_video = bool(pricing.get("billInputVideoSeconds")) or name.startswith(
        "rh_minimax_hailuo_h3"
    ) or name == "rh_seedance_25_r2v"
    merge_image = isinstance(pricing.get("extraImageBilling"), dict) or name.startswith(
        "rh_minimax_hailuo_h3"
    ) or name.startswith("qwen_image")
    if merge_video:
        raw = incoming.get("inputVideoSeconds") or out.get("inputVideoSeconds") or "0"
        try:
            secs = max(0, min(45, int(float(raw))))
        except (TypeError, ValueError):
            secs = 0
        out["inputVideoSeconds"] = str(secs)
    if merge_image:
        raw = incoming.get("inputImageCount") or out.get("inputImageCount") or "0"
        try:
            count = max(0, min(9, int(float(raw))))
        except (TypeError, ValueError):
            count = 0
        out["inputImageCount"] = str(count)
    return out


def _item_label(presets: dict[str, Any] | None, group_id: str, item_id: str) -> str:
    """取选项的展示名（找不到时回退为选项 id）。"""
    if not presets:
        return item_id
    for group in presets.get("groups") or []:
        if group.get("id") != group_id:
            continue
        item = _find_item(group, item_id)
        if item and item.get("label"):
            return str(item["label"])
    return item_id


def _preset_group_ids(presets: dict[str, Any] | None) -> set[str]:
    """取预设中所有选项组 id 集合。"""
    if not presets:
        return set()
    return {str(g["id"]) for g in (presets.get("groups") or []) if g.get("id")}


def validate_generation_options(
    presets: dict[str, Any] | None,
    generation_options: dict[str, str] | None,
) -> dict[str, str]:
    """归一化选项 id，并拒绝未知/已禁用的预设选项（保证报价与选项对齐）。"""
    normalized = normalize_option_ids(presets, generation_options)
    if not generation_options or not presets:
        return normalized

    group_ids = _preset_group_ids(presets)
    for group_id, item_id in generation_options.items():
        if group_id not in group_ids:
            continue
        group = next((g for g in (presets.get("groups") or []) if g.get("id") == group_id), None)
        if not group:
            continue
        enabled = [i for i in (group.get("items") or []) if i.get("enabled", True)]
        if not any(str(i.get("id")) == str(item_id) for i in enabled):
            fail(ErrorCode.BAD_REQUEST, message=f"无效的生成选项：{group_id}={item_id}")
    return normalized


def build_quote_token(quote: CreditQuote) -> str:
    """为报价签发 HMAC 凭证（TTL 120s），提交时校验防篡改与过期。"""
    secret = get_settings().jwt_secret
    payload = {
        "model": quote.model,
        "total": quote.total,
        "pricingVersion": quote.pricing_version,
        "optionSnapshot": quote.option_snapshot,
        "exp": int(time.time()) + QUOTE_TOKEN_TTL_S,
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def _payload_int(payload: dict[str, Any], key: str, default: int = -1) -> int:
    """安全地从凭证 payload 读取整数字段。"""
    raw = payload.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _payload_credit(payload: dict[str, Any], key: str, default: float = -1.0) -> float:
    """从凭证 payload 读取算力金额（一位小数）；缺失/非法返回 default。"""
    raw = payload.get(key)
    if raw is None:
        return default
    return normalize_credit_amount(raw, default=default)


def _pricing_changed_fail(quote: CreditQuote, message: str | None = None) -> None:
    """抛出 PRICING_CHANGED（含最新报价 + 新 quoteToken），前端可静默重试一次。"""
    fail(
        ErrorCode.PRICING_CHANGED,
        message=message,
        content={
            **quote.to_dict(),
            "quoteToken": build_quote_token(quote),
        },
    )


def verify_quote_token(token: str, quote: CreditQuote) -> None:
    """校验报价凭证：签名、过期、模型/总额/版本/选项快照须与当前报价一致。"""
    if not token or "." not in token:
        _pricing_changed_fail(quote, "缺少或无效的报价凭证")
    body, sig = token.rsplit(".", 1)
    expected_sig = hmac.new(
        get_settings().jwt_secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        _pricing_changed_fail(quote, "报价凭证无效")

    padded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        _pricing_changed_fail(quote, "报价凭证解析失败")

    if _payload_int(payload, "exp", 0) < int(time.time()):
        _pricing_changed_fail(quote, "报价已过期，请刷新后重试")

    if (
        str(payload.get("model") or "") != quote.model
        or _payload_credit(payload, "total") != normalize_credit_amount(quote.total)
        or _payload_int(payload, "pricingVersion") != quote.pricing_version
        or dict(payload.get("optionSnapshot") or {}) != quote.option_snapshot
    ):
        _pricing_changed_fail(quote, "价格或选项已变更")


def assert_pricing_version(expected: int | None, quote: CreditQuote) -> None:
    """无凭证时的降级校验：期望定价版本与当前不符则报 PRICING_CHANGED。"""
    if expected is None:
        return
    if int(expected) != quote.pricing_version:
        _pricing_changed_fail(quote, "定价版本已更新")


def _reconcile_quote_total(
    total: float,
    breakdown: list[CreditBreakdownItem],
    min_cost: float,
) -> float:
    """当 total 为 0 时按明细/最低价回填，保证总额与明细一致（一位小数）。"""
    if total > 0:
        return normalize_credit_amount(total)
    if not breakdown:
        return normalize_credit_amount(max(min_cost, 0))
    return normalize_credit_amount(max(min_cost, sum(item.cost for item in breakdown)))


def quote_generation_cost(
    model: Model,
    generation_options: dict[str, str] | None = None,
    *,
    strict: bool = True,
    pricing_config: dict[str, Any] | None = None,
) -> CreditQuote:
    """权威计价入口：按模型定价配置 + 归一化选项计算总额与明细。

    支持五种模式：additive（基础+选项）、primary_group（主选项定价）、
    video_per_second（时长×分辨率单价）、image_by_tier（画质/清晰度分档相加）、
    image_matrix（画质 × 清晰度 二维矩阵，9 档独立定价）。
    """
    params = _model_params(model)
    presets = params.get("generationPresets")
    presets_dict = presets if isinstance(presets, dict) else None
    pricing = (
        pricing_config
        if pricing_config is not None
        else extract_pricing_config(params, category=model.category)
    )
    if strict:
        option_snapshot = validate_generation_options(presets_dict, generation_options)
    else:
        option_snapshot = normalize_option_ids(presets_dict, generation_options)
    # 保留输入视频秒数 / 参考图张数等用量字段（不在 presets.groups 内）
    option_snapshot = _merge_billing_usage_snapshot(
        pricing,
        option_snapshot,
        generation_options,
        model_name=str(getattr(model, "name", "") or ""),
    )

    breakdown: list[CreditBreakdownItem] = []
    base = normalize_credit_amount(pricing.get("baseCost"), default=0)
    mode = pricing.get("mode") or "additive"
    min_cost = normalize_credit_amount(pricing.get("minCost"), default=0)

    if mode == "primary_group":
        primary_group = str(pricing.get("primaryGroupId") or "duration")
        selected = option_snapshot.get(primary_group)
        primary_cost = _option_cost(pricing, primary_group, selected) if selected else 0.0
        total = primary_cost if primary_cost > 0 else base
        if selected:
            breakdown.append(
                CreditBreakdownItem(
                    group_id=primary_group,
                    item_id=selected,
                    label=_item_label(presets_dict, primary_group, selected),
                    cost=total,
                )
            )
        elif base > 0:
            breakdown.append(
                CreditBreakdownItem(
                    group_id="base",
                    item_id="base",
                    label="基础",
                    cost=base,
                )
            )
    elif mode == "video_per_second":
        rate_group = str(pricing.get("rateGroupId") or "resolution")
        seconds = _duration_seconds(presets_dict, option_snapshot)
        selected_rate = option_snapshot.get(rate_group)
        rate = _video_rate_for_options(pricing, option_snapshot, rate_group, selected_rate)
        total = normalize_credit_amount(seconds * rate)
        if rate > 0 and seconds > 0:
            res_label = _item_label(presets_dict, rate_group, selected_rate or "")
            ref_gid = str(pricing.get("refVideoGroupId") or "refVideo")
            ref_tag = "·有参考视频" if option_snapshot.get(ref_gid) == "with" else ""
            breakdown.append(
                CreditBreakdownItem(
                    group_id="duration",
                    item_id=str(seconds),
                    label=f"{seconds}s × {res_label}{ref_tag} {rate}/秒",
                    cost=total,
                )
            )
        elif not breakdown:
            breakdown.append(
                CreditBreakdownItem(
                    group_id="base",
                    item_id="base",
                    label="未配置每秒算力",
                    cost=0,
                )
            )
        # MiniMax-H3：输入参考视频秒数按同档清晰度单价另计
        if pricing.get("billInputVideoSeconds") and rate > 0:
            try:
                input_secs = max(0, min(45, int(option_snapshot.get("inputVideoSeconds") or 0)))
            except (TypeError, ValueError):
                input_secs = 0
            if input_secs > 0:
                input_cost = normalize_credit_amount(input_secs * rate)
                total = normalize_credit_amount(total + input_cost)
                res_label = _item_label(presets_dict, rate_group, selected_rate or "")
                breakdown.append(
                    CreditBreakdownItem(
                        group_id="inputVideoSeconds",
                        item_id=str(input_secs),
                        label=f"输入视频 {input_secs}s × {res_label} {rate}/秒",
                        cost=input_cost,
                    )
                )
        # 参考图超额：前 freeCount 张免费，超出按 costPerImage
        eib = pricing.get("extraImageBilling")
        if isinstance(eib, dict):
            try:
                free_count = max(0, int(eib.get("freeCount") or 0))
            except (TypeError, ValueError):
                free_count = 0
            per_image = normalize_credit_amount(eib.get("costPerImage"), default=0)
            try:
                img_count = max(0, min(9, int(option_snapshot.get("inputImageCount") or 0)))
            except (TypeError, ValueError):
                img_count = 0
            extra_n = max(0, img_count - free_count)
            if extra_n > 0 and per_image > 0:
                extra_cost = normalize_credit_amount(extra_n * per_image)
                total = normalize_credit_amount(total + extra_cost)
                breakdown.append(
                    CreditBreakdownItem(
                        group_id="inputImageCount",
                        item_id=str(img_count),
                        label=f"参考图超额 {extra_n} 张 × {per_image}/张（共{img_count}张，前{free_count}张免费）",
                        cost=extra_cost,
                    )
                )
    elif mode == "image_by_tier":
        tier_groups = pricing.get("tierGroupIds") or ["quality", "resolution", "size"]
        if not isinstance(tier_groups, list):
            tier_groups = ["quality", "resolution", "size"]
        total = 0.0
        for group_id in tier_groups:
            gid = str(group_id)
            item_id = option_snapshot.get(gid)
            if not item_id:
                continue
            cost = _option_cost(pricing, gid, item_id)
            if cost <= 0:
                continue
            total = normalize_credit_amount(total + cost)
            breakdown.append(
                CreditBreakdownItem(
                    group_id=gid,
                    item_id=item_id,
                    label=_item_label(presets_dict, gid, item_id),
                    cost=cost,
                )
            )
        if total <= 0 and not breakdown:
            breakdown.append(
                CreditBreakdownItem(
                    group_id="base",
                    item_id="base",
                    label="未配置画质/清晰度算力",
                    cost=0,
                )
            )
    elif mode == "image_matrix":
        # 二维矩阵：按（画质 × 清晰度）唯一定价，9 档互相独立，不做累加
        group_ids = pricing.get("matrixGroupIds") or ["quality", "resolution"]
        if not isinstance(group_ids, list) or len(group_ids) < 2:
            group_ids = ["quality", "resolution"]
        row_group, col_group = str(group_ids[0]), str(group_ids[1])
        row_id = option_snapshot.get(row_group)
        col_id = option_snapshot.get(col_group)
        matrix = pricing.get("matrix") if isinstance(pricing.get("matrix"), dict) else {}
        cost = 0.0
        if row_id and col_id:
            row_map = matrix.get(row_id)
            if isinstance(row_map, dict):
                cost = normalize_credit_amount(row_map.get(col_id), default=0)
        total = cost
        if cost > 0 and row_id and col_id:
            row_label = _item_label(presets_dict, row_group, row_id)
            col_label = _item_label(presets_dict, col_group, col_id)
            breakdown.append(
                CreditBreakdownItem(
                    group_id="matrix",
                    item_id=f"{row_id}|{col_id}",
                    label=f"{row_label} × {col_label}",
                    cost=cost,
                )
            )
        else:
            breakdown.append(
                CreditBreakdownItem(
                    group_id="base",
                    item_id="base",
                    label="未配置画质×清晰度价格",
                    cost=0,
                )
            )
    else:
        option_total = 0.0
        for group_id, item_id in sorted(option_snapshot.items()):
            # 张数走下方乘数，避免与 options.count 单价重复计费
            if group_id in ("count", "n"):
                continue
            cost = _option_cost(pricing, group_id, item_id)
            if cost <= 0:
                continue
            option_total = normalize_credit_amount(option_total + cost)
            breakdown.append(
                CreditBreakdownItem(
                    group_id=group_id,
                    item_id=item_id,
                    label=_item_label(presets_dict, group_id, item_id),
                    cost=cost,
                )
            )
        total = normalize_credit_amount(base + option_total)
        if base > 0 and not breakdown:
            breakdown.insert(
                0,
                CreditBreakdownItem(
                    group_id="base",
                    item_id="base",
                    label="基础",
                    cost=base,
                ),
            )
        elif base > 0 and breakdown:
            breakdown.insert(
                0,
                CreditBreakdownItem(
                    group_id="base",
                    item_id="base",
                    label="基础",
                    cost=base,
                ),
            )

    total = max(min_cost, total)
    total = _reconcile_quote_total(total, breakdown, min_cost)

    # 图片一次生成多张：单价 × 张数（presets api.n）
    category = str(model.category or "").strip().lower()
    image_n = _image_output_count(presets_dict, option_snapshot)
    if image_n > 1 and category in ("image", "tool") and total > 0:
        unit = total
        total = normalize_credit_amount(unit * image_n)
        breakdown.append(
            CreditBreakdownItem(
                group_id="count",
                item_id=str(image_n),
                label=f"生成数量 ×{image_n}",
                cost=normalize_credit_amount(unit * (image_n - 1)),
            )
        )

    # 参考图按张计费（千问等）：加在输出张数乘数之后，避免把输入费乘到 n 张上。
    # video_per_second（如 H3）若已写入 inputImageCount 明细则跳过，防双计。
    if isinstance(pricing.get("extraImageBilling"), dict) and not any(
        item.group_id == "inputImageCount" for item in breakdown
    ):
        eib = pricing["extraImageBilling"]
        try:
            free_count = max(0, int(eib.get("freeCount") or 0))
        except (TypeError, ValueError):
            free_count = 0
        per_image = normalize_credit_amount(eib.get("costPerImage"), default=0)
        try:
            img_count = max(0, min(9, int(option_snapshot.get("inputImageCount") or 0)))
        except (TypeError, ValueError):
            img_count = 0
        bill_n = max(0, img_count - free_count)
        if bill_n > 0 and per_image > 0:
            extra_cost = normalize_credit_amount(bill_n * per_image)
            total = normalize_credit_amount(total + extra_cost)
            free_hint = f"，前{free_count}张免费" if free_count > 0 else ""
            breakdown.append(
                CreditBreakdownItem(
                    group_id="inputImageCount",
                    item_id=str(img_count),
                    label=f"参考图 {bill_n} 张 × {per_image}/张（共{img_count}张{free_hint}）",
                    cost=extra_cost,
                )
            )

    return CreditQuote(
        model=model.name,
        total=normalize_credit_amount(total),
        base=base,
        breakdown=breakdown,
        pricing_version=int(pricing.get("version") or 1),
        option_snapshot=option_snapshot,
        credits_enabled=credits_enabled(),
    )


def apply_quote_to_job(job, quote: CreditQuote) -> None:
    """把报价快照（总额、版本、明细、选项快照）写入生成任务。"""
    job.credit_cost = quote.total
    job.pricing_version = quote.pricing_version
    job.credit_breakdown = [item.to_dict() for item in quote.breakdown]
    params = dict(job.input_params or {})
    params["optionSnapshot"] = quote.option_snapshot
    params["pricingVersion"] = quote.pricing_version
    job.input_params = params
