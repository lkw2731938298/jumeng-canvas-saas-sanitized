"""Unit tests for credit_flow job settlement helpers."""

from types import SimpleNamespace

from app.core.credit_status import CreditStatus
from app.services.credit_flow import credit_commit_succeeded, job_has_upstream_in_flight


def test_job_has_upstream_in_flight_detects_provider_ids():
    job = SimpleNamespace(
        provider_task_id="task-1",
        provider_job_id=None,
        processing_id=None,
        upstream_job_id=None,
    )
    assert job_has_upstream_in_flight(job) is True


def test_job_has_upstream_in_flight_false_when_empty():
    job = SimpleNamespace(
        provider_task_id=None,
        provider_job_id="",
        processing_id=None,
        upstream_job_id=None,
    )
    assert job_has_upstream_in_flight(job) is False


def test_credit_commit_succeeded_when_committed():
    job = SimpleNamespace(credit_cost=10, credit_status=CreditStatus.COMMITTED.value)
    assert credit_commit_succeeded(job) is True


def test_credit_commit_succeeded_false_when_commit_pending():
    job = SimpleNamespace(credit_cost=10, credit_status=CreditStatus.COMMIT_PENDING.value)
    assert credit_commit_succeeded(job) is False
