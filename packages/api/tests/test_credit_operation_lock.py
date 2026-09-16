"""credit_operation_lock 标记锁键与 TTL 常量单测。"""

from app.services.credit_operation_lock import (
    CREDIT_ADMIN_ADJUST_LOCK_TTL_SEC,
    CREDIT_RECHARGE_LOCK_TTL_SEC,
    CREDIT_REFUND_LOCK_TTL_SEC,
    CREDIT_SUBSCRIPTION_LOCK_TTL_SEC,
    GENERATION_SUBMIT_LOCK_TTL_SEC,
    credit_admin_adjust_lock_key,
    credit_recharge_lock_key,
    credit_refund_lock_key,
    credit_subscription_lock_key,
    generation_submit_lock_key,
)


def test_lock_key_formats():
    assert credit_recharge_lock_key(1) == "lock:credit:recharge:1"
    assert credit_subscription_lock_key(2) == "lock:subscription:2"
    assert credit_admin_adjust_lock_key(3) == "lock:credit:adjust:3"
    assert generation_submit_lock_key(4) == "lock:generation:4"
    assert credit_refund_lock_key(5) == "lock:credit:refund:5"


def test_ttl_constants_match_spec():
    assert CREDIT_RECHARGE_LOCK_TTL_SEC == 5
    assert CREDIT_SUBSCRIPTION_LOCK_TTL_SEC == 60
    assert CREDIT_ADMIN_ADJUST_LOCK_TTL_SEC == 600
    assert GENERATION_SUBMIT_LOCK_TTL_SEC == 7 * 24 * 3600
    assert CREDIT_REFUND_LOCK_TTL_SEC == 7 * 24 * 3600
