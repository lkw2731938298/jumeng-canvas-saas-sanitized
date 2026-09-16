"""Tests for Asia/Shanghai datetime helpers."""

from datetime import datetime, timezone

from app.core.datetime_util import as_cst_aware, cst_iso_now, now_cst_naive, parse_query_datetime, to_cst_iso


def test_now_cst_naive_has_no_tzinfo():
    dt = now_cst_naive()
    assert dt.tzinfo is None


def test_to_cst_iso_from_naive():
    dt = datetime(2026, 7, 3, 11, 30, 0)
    assert to_cst_iso(dt) == "2026-07-03T11:30:00+08:00"


def test_parse_query_datetime_cst_day_start():
    parsed = parse_query_datetime("2026-07-03T00:00:00+08:00")
    assert parsed == datetime(2026, 7, 3, 0, 0, 0)


def test_as_cst_aware_from_utc_aware():
    aware_utc = datetime(2026, 7, 3, 3, 30, 0, tzinfo=timezone.utc)
    cst = as_cst_aware(aware_utc)
    assert cst.hour == 11
    assert cst.minute == 30


def test_cst_iso_now_has_offset():
    iso = cst_iso_now()
    assert iso.endswith("+08:00")
