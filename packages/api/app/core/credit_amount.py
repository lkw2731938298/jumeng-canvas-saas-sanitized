"""算力金额规范化：统一保留一位小数（非负）。"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# 算力对外精度：小数点后一位
CREDIT_QUANTUM = Decimal("0.1")


def normalize_credit_amount(raw: Any, *, default: float = 0.0) -> float:
    """将任意输入规范为 >=0 且最多一位小数的 float。"""
    if raw is None or raw == "":
        return float(default)
    try:
        d = Decimal(str(raw).strip())
    except Exception:
        return float(default)
    if d.is_nan() or d.is_infinite():
        return float(default)
    if d < 0:
        d = Decimal("0")
    d = d.quantize(CREDIT_QUANTUM, rounding=ROUND_HALF_UP)
    return float(d)


def normalize_credit_delta(raw: Any, *, default: float = 0.0) -> float:
    """流水变动额：保留符号，绝对值最多一位小数。"""
    if raw is None or raw == "":
        return float(default)
    try:
        d = Decimal(str(raw).strip())
    except Exception:
        return float(default)
    if d.is_nan() or d.is_infinite():
        return float(default)
    sign = Decimal("-1") if d < 0 else Decimal("1")
    mag = abs(d).quantize(CREDIT_QUANTUM, rounding=ROUND_HALF_UP)
    return float(sign * mag)
