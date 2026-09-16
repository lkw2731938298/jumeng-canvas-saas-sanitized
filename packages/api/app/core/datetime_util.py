"""东八区（UTC+8）时间工具 — MySQL naive 存墙钟，API 序列化 +08:00 ISO。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# 中国标准时间（无夏令时，等价于 Asia/Shanghai）
CST = timezone(timedelta(hours=8))
# 历史数据迁移前：库内 naive 曾按 UTC 墙钟写入；读旧行时可临时开启 +8h 校正
_LEGACY_NAIVE_WAS_UTC = False


def now_cst_naive() -> datetime:
    """当前东八区时刻，去掉 tzinfo，供 MySQL DATETIME 写入/比较。"""
    return datetime.now(CST).replace(tzinfo=None)


def now_cst_aware() -> datetime:
    """当前东八区 aware datetime。"""
    return datetime.now(CST)


def as_cst_aware(dt: datetime | None) -> datetime | None:
    """MySQL naive 按东八区墙钟解释；aware 值转到东八区。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        if _LEGACY_NAIVE_WAS_UTC:
            dt = dt.replace(tzinfo=timezone.utc).astimezone(CST)
        else:
            dt = dt.replace(tzinfo=CST)
    else:
        dt = dt.astimezone(CST)
    return dt


def to_cst_iso(dt: datetime | None, *, timespec: str = "seconds") -> str | None:
    """序列化为带 +08:00 的 ISO 8601。"""
    aware = as_cst_aware(dt)
    if aware is None:
        return None
    return aware.isoformat(timespec=timespec)


def cst_iso_now(*, timespec: str = "seconds") -> str:
    """当前时刻的 +08:00 ISO 字符串（trace_json / 资产索引等）。"""
    return now_cst_aware().isoformat(timespec=timespec)


def cst_iso(dt: datetime | None, *, timespec: str = "seconds") -> str | None:
    return to_cst_iso(dt, timespec=timespec)


def parse_query_datetime(value: str | None) -> datetime | None:
    """解析筛选参数为东八区 naive（与 DATETIME 列比较）。"""
    if not value or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Invalid datetime") from exc
    aware = as_cst_aware(parsed)
    return aware.replace(tzinfo=None) if aware else None


def shift_naive_hours(dt: datetime, hours: int) -> datetime:
    """运维迁移：naive 时间批量平移（如 UTC→CST +8）。"""
    return dt + timedelta(hours=hours)


# --- 兼容旧名（逐步删除）---
as_utc_aware = as_cst_aware
utc_now_naive = now_cst_naive
to_utc_iso = to_cst_iso
