"""Cross-dialect column types (MySQL 8.0 primary target)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import JSON
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.types import TypeDecorator


def json_sanitize(value: Any) -> Any:
    """递归把 Decimal 等转为 JSON 可序列化类型（算力一位小数后必做）。"""
    if isinstance(value, Decimal):
        # 与 normalize_credit_amount 一致：最多一位小数的 float
        return float(value)
    if isinstance(value, dict):
        return {str(k): json_sanitize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_sanitize(v) for v in value]
    return value


class JsonCol(TypeDecorator):
    """MySQL JSON：写入前清洗 Decimal，避免 Object of type Decimal is not JSON serializable。"""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return json_sanitize(value)


# Unsigned BIGINT for primary keys and foreign keys.
BigIntPK = BIGINT(unsigned=True)
BigIntFK = BIGINT(unsigned=True)
