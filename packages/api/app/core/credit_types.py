"""Credit lot type constants and user consumption priority."""

from __future__ import annotations

CREDIT_TYPE_ACTIVITY = "activity"
CREDIT_TYPE_MODEL_SPECIFIC = "model_specific"
CREDIT_TYPE_SUBSCRIPTION = "subscription"
CREDIT_TYPE_GENERAL = "general"

ALL_CREDIT_TYPES: tuple[str, ...] = (
    CREDIT_TYPE_MODEL_SPECIFIC,
    CREDIT_TYPE_ACTIVITY,
    CREDIT_TYPE_SUBSCRIPTION,
    CREDIT_TYPE_GENERAL,
)

DEFAULT_CONSUME_PRIORITY: list[str] = list(ALL_CREDIT_TYPES)

CREDIT_TYPE_LABELS: dict[str, str] = {
    CREDIT_TYPE_ACTIVITY: "活动算力",
    CREDIT_TYPE_MODEL_SPECIFIC: "模型专用算力",
    CREDIT_TYPE_SUBSCRIPTION: "会员订阅算力",
    CREDIT_TYPE_GENERAL: "通用算力",
}


def normalize_consume_priority(raw: list[str] | None) -> list[str]:
    if not raw:
        return list(DEFAULT_CONSUME_PRIORITY)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in raw:
        if item in ALL_CREDIT_TYPES and item not in seen:
            ordered.append(item)
            seen.add(item)
    for item in ALL_CREDIT_TYPES:
        if item not in seen:
            ordered.append(item)
    return ordered


def is_valid_consume_priority(raw: list[str]) -> bool:
    return normalize_consume_priority(raw) == raw and set(raw) == set(ALL_CREDIT_TYPES)
