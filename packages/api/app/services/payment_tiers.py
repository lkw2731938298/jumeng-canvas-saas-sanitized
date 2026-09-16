"""充值档位权威配置（MySQL platform_settings.recharge_tiers，无 DB 时用内置默认）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error_codes import ErrorCode
from ..core.errors import fail

# 内置默认档位（算力点 → 支付金额分）
DEFAULT_RECHARGE_TIERS: list[dict[str, Any]] = [
    {"creditsAmount": 100, "payAmountFen": 1_000, "sortOrder": 10, "isActive": True},
    {"creditsAmount": 200, "payAmountFen": 2_000, "sortOrder": 20, "isActive": True},
    {"creditsAmount": 500, "payAmountFen": 4_900, "sortOrder": 30, "isActive": True},
    {"creditsAmount": 1000, "payAmountFen": 9_900, "sortOrder": 40, "isActive": True},
    {"creditsAmount": 2000, "payAmountFen": 19_900, "sortOrder": 50, "isActive": True},
]

MAX_RECHARGE_TIERS = 20


def normalize_recharge_tier(item: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化单条充值档位。"""
    credits = int(item.get("creditsAmount") or item.get("credits_amount") or 0)
    pay_fen = int(item.get("payAmountFen") or item.get("pay_amount_fen") or 0)
    sort_order = int(item.get("sortOrder") if item.get("sortOrder") is not None else item.get("sort_order") or 0)
    is_active = bool(item.get("isActive") if "isActive" in item else item.get("is_active", True))
    if credits < 1:
        fail(ErrorCode.INVALID_RECHARGE_AMOUNT, message="算力点数须大于 0")
    if pay_fen < 1:
        fail(ErrorCode.INVALID_RECHARGE_AMOUNT, message="支付金额（分）须大于 0")
    return {
        "creditsAmount": credits,
        "payAmountFen": pay_fen,
        "sortOrder": sort_order,
        "isActive": is_active,
    }


def normalize_recharge_tiers(raw: list[Any] | None) -> list[dict[str, Any]]:
    """校验档位列表：去重 creditsAmount、最多 MAX 条。"""
    if not raw:
        return [dict(t) for t in DEFAULT_RECHARGE_TIERS]
    if len(raw) > MAX_RECHARGE_TIERS:
        fail(ErrorCode.INVALID_RECHARGE_AMOUNT, message=f"充值档位最多 {MAX_RECHARGE_TIERS} 条")
    normalized = [normalize_recharge_tier(dict(item)) for item in raw if isinstance(item, dict)]
    if not normalized:
        return [dict(t) for t in DEFAULT_RECHARGE_TIERS]
    seen: set[int] = set()
    for tier in normalized:
        if tier["creditsAmount"] in seen:
            fail(ErrorCode.INVALID_RECHARGE_AMOUNT, message=f"算力档位 {tier['creditsAmount']} 重复")
        seen.add(tier["creditsAmount"])
    normalized.sort(key=lambda t: (t["sortOrder"], t["creditsAmount"]))
    return normalized


async def load_recharge_tiers(db: AsyncSession) -> list[dict[str, Any]]:
    """从 platform_settings 读取充值档位；空则返回默认。"""
    from .platform_settings import get_recharge_tiers_raw

    raw = await get_recharge_tiers_raw(db)
    return normalize_recharge_tiers(raw)


def active_recharge_tiers(tiers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """仅返回启用的档位，按 sortOrder 排序。"""
    return sorted(
        [t for t in tiers if t.get("isActive", True)],
        key=lambda t: (t.get("sortOrder", 0), t.get("creditsAmount", 0)),
    )


async def get_active_recharge_amounts(db: AsyncSession) -> tuple[int, ...]:
    tiers = await load_recharge_tiers(db)
    return tuple(t["creditsAmount"] for t in active_recharge_tiers(tiers))


async def get_recharge_prices_fen_map(db: AsyncSession) -> dict[int, int]:
    tiers = await load_recharge_tiers(db)
    return {t["creditsAmount"]: t["payAmountFen"] for t in active_recharge_tiers(tiers)}


async def resolve_pay_amount_fen(db: AsyncSession, credits_amount: int) -> int:
    """按档位取支付金额（分）；非法档位抛 INVALID_RECHARGE_AMOUNT。"""
    price_map = await get_recharge_prices_fen_map(db)
    pay_fen = price_map.get(credits_amount)
    if pay_fen is None:
        fail(
            ErrorCode.INVALID_RECHARGE_AMOUNT,
            content={"allowedAmounts": list(price_map.keys())},
        )
    return pay_fen


async def assert_valid_recharge_amount(db: AsyncSession, credits_amount: int) -> None:
    await resolve_pay_amount_fen(db, credits_amount)
