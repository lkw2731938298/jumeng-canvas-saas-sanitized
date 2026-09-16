"""JobStatus enum tests."""

from app.core.job_status import JobStatus


def test_job_status_values():
    assert JobStatus.PENDING.value == "pending"
    assert JobStatus.RUNNING.value == "running"
    assert JobStatus.ABNORMAL.value == "abnormal"


def test_terminal_and_in_flight():
    assert JobStatus.SUCCEEDED in JobStatus.terminal()
    assert JobStatus.RUNNING in JobStatus.in_flight()
