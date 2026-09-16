"""Tests for upstream billing extraction."""

from app.integrations.upstream.upstream_billing import extract_upstream_billing


def test_extract_token_usage_from_chat_completion():
    info = extract_upstream_billing(
        {
            "id": "chatcmpl-1",
            "usage": {"prompt_tokens": 22, "completion_tokens": 18, "total_tokens": 40},
        },
        provider="doubao",
    )
    assert info is not None
    assert info.amount == 40
    assert info.kind == "tokens"
    assert info.provider == "doubao"


def test_extract_credit_billing_from_nested_output():
    info = extract_upstream_billing(
        {
            "output": {
                "credit_cost": 12,
            }
        },
        provider="vidu",
    )
    assert info is not None
    assert info.amount == 12
    assert info.kind == "credits"


def test_extract_runninghub_style_money_field():
    info = extract_upstream_billing(
        {
            "status": "SUCCESS",
            "cost": 0.12,
            "currency": "USD",
        },
        provider="runninghub",
    )
    assert info is not None
    assert info.kind == "usd_micro"
    assert info.amount == 120_000


def test_extract_returns_none_without_billing():
    assert extract_upstream_billing({"status": "SUCCESS"}) is None
