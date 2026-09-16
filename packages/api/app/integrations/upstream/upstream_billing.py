"""从上游服务商 API 响应体中提取计费用量信息。"""

from __future__ import annotations

from ...core.datetime_util import cst_iso_now, now_cst_naive
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class UpstreamBillingInfo:
    """上游计费快照：从服务商响应解析出的用量金额与类型。"""

    amount: int
    kind: str
    raw: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None

    def to_trace_dict(self) -> dict[str, Any]:
        """序列化为任务追溯 JSON 中的上游计费块。"""
        return {
            "amount": self.amount,
            "kind": self.kind,
            "provider": self.provider,
            "raw": self.raw,
            "capturedAt": cst_iso_now(),
        }


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0:
            return None
        return int(round(value))
    if isinstance(value, str) and value.strip():
        try:
            num = float(value.strip())
        except ValueError:
            return None
        if num < 0:
            return None
        return int(round(num))
    return None


def _first_int(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key in data:
            parsed = _as_int(data.get(key))
            if parsed is not None:
                return parsed
    return None


def _usage_dict(data: dict[str, Any]) -> dict[str, Any]:
    usage = data.get("usage")
    if isinstance(usage, dict):
        return usage
    out = data.get("output")
    if isinstance(out, dict):
        nested = out.get("usage")
        if isinstance(nested, dict):
            return nested
    return {}


def _extract_token_billing(data: dict[str, Any]) -> UpstreamBillingInfo | None:
    usage = _usage_dict(data)
    if not usage:
        return None
    total = _first_int(
        usage,
        "total_tokens",
        "totalTokens",
        "total_token",
        "totalToken",
    )
    if total is None:
        prompt = _first_int(usage, "prompt_tokens", "input_tokens", "inputTokens") or 0
        completion = _first_int(usage, "completion_tokens", "output_tokens", "outputTokens") or 0
        if prompt or completion:
            total = prompt + completion
    if total is None:
        return None
    return UpstreamBillingInfo(amount=total, kind="tokens", raw={"usage": usage})


def _extract_credit_billing(data: dict[str, Any]) -> UpstreamBillingInfo | None:
    candidates: list[tuple[str, dict[str, Any]]] = [( "", data)]
    for nested_key in ("output", "data", "result", "billing", "bill", "cost_detail", "costDetail"):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            candidates.append((nested_key, nested))

    credit_keys = (
        "credit",
        "credits",
        "credit_cost",
        "creditCost",
        "consume_credit",
        "consumeCredit",
        "consumed_credit",
        "consumedCredit",
        "consumed_credits",
        "consumedCredits",
        "points",
        "rh_coins",
        "rhCoins",
        "coins",
        "coin",
        "quota",
        "deduct_credit",
        "deductCredit",
    )
    for _, block in candidates:
        amount = _first_int(block, *credit_keys)
        if amount is not None:
            raw = {k: block.get(k) for k in credit_keys if k in block}
            return UpstreamBillingInfo(amount=amount, kind="credits", raw=raw or dict(block))

    return None


def _extract_money_billing(data: dict[str, Any]) -> UpstreamBillingInfo | None:
    candidates: list[dict[str, Any]] = [data]
    for nested_key in ("output", "data", "result", "billing", "bill", "cost_detail", "costDetail"):
        nested = data.get(nested_key)
        if isinstance(nested, dict):
            candidates.append(nested)

    cny_keys = ("cost_cny", "costCny", "amount_cny", "amountCny", "fee_cny", "feeCny", "bill_amount", "billAmount")
    usd_keys = ("cost_usd", "costUsd", "amount_usd", "amountUsd", "fee_usd", "feeUsd", "used_limit", "usedLimit")

    for block in candidates:
        cny = _first_int(block, *cny_keys)
        if cny is not None:
            return UpstreamBillingInfo(amount=cny, kind="cny_fen", raw={k: block.get(k) for k in cny_keys if k in block})

        for key in ("cost", "fee", "amount", "price", "total_cost", "totalCost", "charge", "charged"):
            if key not in block:
                continue
            raw_val = block.get(key)
            unit = str(block.get("currency") or block.get("currency_code") or block.get("currencyCode") or "").upper()
            if unit in ("CNY", "RMB", "¥"):
                parsed = _as_int(raw_val)
                if parsed is not None:
                    return UpstreamBillingInfo(
                        amount=parsed if parsed > 100 else parsed * 100,
                        kind="cny_fen",
                        raw={key: raw_val, "currency": unit},
                    )
            if unit in ("USD", "$"):
                parsed = _as_int(raw_val)
                if parsed is not None:
                    return UpstreamBillingInfo(
                        amount=parsed if parsed > 1000 else int(round(float(raw_val) * 1_000_000)),
                        kind="usd_micro",
                        raw={key: raw_val, "currency": unit},
                    )

        usd = _first_int(block, *usd_keys)
        if usd is not None:
            return UpstreamBillingInfo(amount=usd, kind="usd_micro", raw={k: block.get(k) for k in usd_keys if k in block})

    return None


def _extract_dashscope_media_billing(data: dict[str, Any]) -> UpstreamBillingInfo | None:
    usage = _usage_dict(data)
    if not usage:
        return None

    image_count = _first_int(usage, "image_count", "imageCount")
    if image_count is not None:
        return UpstreamBillingInfo(amount=image_count, kind="image_count", raw={"usage": usage})

    video_duration = _first_int(usage, "video_duration", "videoDuration", "duration")
    if video_duration is not None:
        return UpstreamBillingInfo(amount=video_duration, kind="video_seconds", raw={"usage": usage})

    return None


def extract_upstream_billing(data: Any, *, provider: str | None = None) -> UpstreamBillingInfo | None:
    """尽力从上游 JSON 响应解析可计费用量（积分、Token、金额等）。"""
    if not isinstance(data, dict):
        return None

    for extractor in (
        _extract_credit_billing,
        _extract_dashscope_media_billing,
        _extract_token_billing,
        _extract_money_billing,
    ):
        info = extractor(data)
        if info is not None:
            info.provider = provider
            return info
    return None


__all__ = ["UpstreamBillingInfo", "extract_upstream_billing"]
