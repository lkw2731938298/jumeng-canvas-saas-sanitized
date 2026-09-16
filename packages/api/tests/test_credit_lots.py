"""Tests for credit lot allocation priority."""

from datetime import datetime, timedelta
from uuid import uuid4

from app.core.credit_types import (
    CREDIT_TYPE_ACTIVITY,
    CREDIT_TYPE_GENERAL,
    CREDIT_TYPE_MODEL_SPECIFIC,
    DEFAULT_CONSUME_PRIORITY,
    normalize_consume_priority,
)
from app.core.datetime_util import now_cst_naive
from app.models.credit_lot import CreditLot
from app.services.credit_lots import allocate_credits


def _lot(credit_type: str, amount: int, *, model: str | None = None, days: int | None = 30):
    now = now_cst_naive()
    return CreditLot(
        id=uuid4(),
        user_id=uuid4(),
        credit_type=credit_type,
        model_name=model,
        amount_initial=amount,
        amount_remaining=amount,
        expires_at=None if days is None else now + timedelta(days=days),
        source="test",
        status="active",
        created_at=now,
        updated_at=now,
    )


def test_normalize_consume_priority_fills_missing():
    assert normalize_consume_priority(["general", "activity"]) == [
        "general",
        "activity",
        "model_specific",
        "subscription",
    ]


def test_allocate_respects_user_priority():
    lots = [
        _lot(CREDIT_TYPE_GENERAL, 100, days=None),
        _lot(CREDIT_TYPE_ACTIVITY, 50),
    ]
    priority = ["activity", "model_specific", "subscription", "general"]
    result = allocate_credits(lots, amount=30, model_name="flux", priority=priority)
    assert result is not None
    assert sum(a.amount for a in result) == 30
    assert result[0].lot_id == lots[1].id


def test_model_specific_only_for_matching_model():
    lots = [
        _lot(CREDIT_TYPE_MODEL_SPECIFIC, 40, model="flux-pro"),
        _lot(CREDIT_TYPE_GENERAL, 100, days=None),
    ]
    result = allocate_credits(
        lots,
        amount=10,
        model_name="flux-pro",
        priority=list(DEFAULT_CONSUME_PRIORITY),
    )
    assert result is not None
    assert result[0].lot_id == lots[0].id

    result_other = allocate_credits(
        lots,
        amount=10,
        model_name="other-model",
        priority=list(DEFAULT_CONSUME_PRIORITY),
    )
    assert result_other is not None
    assert result_other[0].lot_id == lots[1].id


def test_allocate_insufficient_returns_none():
    lots = [_lot(CREDIT_TYPE_ACTIVITY, 5)]
    assert allocate_credits(lots, amount=10, model_name=None, priority=list(DEFAULT_CONSUME_PRIORITY)) is None


def test_lot_is_usable_compares_naive_cst_expiry():
    now = now_cst_naive()
    expired = _lot(CREDIT_TYPE_ACTIVITY, 10, days=-1)
    assert allocate_credits([expired], amount=1, model_name=None, priority=list(DEFAULT_CONSUME_PRIORITY)) is None
    active = _lot(CREDIT_TYPE_ACTIVITY, 10, days=1)
    result = allocate_credits([active], amount=1, model_name=None, priority=list(DEFAULT_CONSUME_PRIORITY))
    assert result is not None
