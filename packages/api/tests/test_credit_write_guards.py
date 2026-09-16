"""§8.11 算力写操作防重复：reference_key 对齐、标记锁、幂等键解析。"""

from __future__ import annotations

from app.services.credit_transactions import (
    job_consume_reference_key,
    job_refund_reference_key,
    requeue_suffix_from_idempotency_key,
)


def test_requeue_suffix_from_idempotency_key():
    assert requeue_suffix_from_idempotency_key("canvas-job-42") is None
    assert requeue_suffix_from_idempotency_key("canvas-job-42-rq1") == "rq1"
    assert requeue_suffix_from_idempotency_key("canvas-job-42-rq3") == "rq3"


def test_job_consume_reference_key_first_reserve():
    assert job_consume_reference_key(99) == "job-consume-99"
    assert job_consume_reference_key(99, idempotency_key="canvas-job-99") == "job-consume-99"


def test_job_consume_reference_key_requeue_aligned():
    key = "canvas-job-99-rq2"
    assert job_consume_reference_key(99, idempotency_key=key) == "job-consume-99-rq2"


def test_job_refund_reference_key_distinct_from_consume():
    idem = "canvas-job-10-rq1"
    consume = job_consume_reference_key(10, idempotency_key=idem)
    refund = job_refund_reference_key(10, idempotency_key=idem)
    assert consume == "job-consume-10-rq1"
    assert refund == "job-refund-10-rq1"
    assert consume != refund


def test_requeue_consume_keys_do_not_collide():
    """重队列多次预扣的扣费流水 reference_key 互不冲突。"""
    keys = {
        job_consume_reference_key(5, idempotency_key=f"canvas-job-5-rq{n}")
        for n in (1, 2, 3)
    }
    assert keys == {
        "job-consume-5-rq1",
        "job-consume-5-rq2",
        "job-consume-5-rq3",
    }


def test_consume_and_refund_same_rq_no_unique_collision():
    """同一次预扣的扣费与退流水 reference_key 不同，满足三列 UNIQUE。"""
    idem = "canvas-job-7-rq2"
    assert (
        job_consume_reference_key(7, idempotency_key=idem),
        job_refund_reference_key(7, idempotency_key=idem),
    ) == ("job-consume-7-rq2", "job-refund-7-rq2")
