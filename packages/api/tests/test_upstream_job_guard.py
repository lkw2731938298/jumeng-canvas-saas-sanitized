"""兼容旧文件名：上游停车 / stale 恢复相关单元测试（不连真实 DB）。"""

from app.core.datetime_util import now_cst_naive
from app.core.job_status import JobStatus
from app.models.job import GenerationJob
from app.services.restart_job_recovery import (
    apply_upstream_awaiting_admin_park,
    job_is_upstream_awaiting_admin,
    provider_task_id_from_job,
)
from app.services.stale_jobs import _requeue_or_fail


def test_job_has_upstream_task_id():
    job = GenerationJob(provider_task_id="task-1")
    assert provider_task_id_from_job(job) == "task-1"


def test_requeue_exhausted_with_task_id_parks():
    job = GenerationJob(
        provider_task_id="task-1",
        attempt_count=3,
        max_attempts=3,
        status=JobStatus.RUNNING.value,
    )
    outcome = _requeue_or_fail(job, message="restart", now=now_cst_naive())
    assert outcome == "parked"
    assert job.status == JobStatus.POLLING.value
    assert job_is_upstream_awaiting_admin(job)


def test_requeue_with_task_id_sets_polling():
    job = GenerationJob(
        provider_task_id="task-1",
        attempt_count=1,
        max_attempts=3,
        status=JobStatus.RUNNING.value,
    )
    outcome = _requeue_or_fail(job, message="restart", now=now_cst_naive())
    assert outcome == "requeued"
    assert job.status == JobStatus.POLLING.value
    assert job.worker_claim_id is None


def test_requeue_without_task_id_sets_pending():
    job = GenerationJob(attempt_count=1, max_attempts=3, status=JobStatus.RUNNING.value)
    outcome = _requeue_or_fail(job, message="restart", now=now_cst_naive())
    assert outcome == "requeued"
    assert job.status == JobStatus.PENDING.value


def test_upstream_awaiting_admin_park():
    job = GenerationJob(provider_task_id="t-9", status=JobStatus.RUNNING.value)
    apply_upstream_awaiting_admin_park(job, error_message="wait admin", reason="poll_timeout")
    assert job.status == JobStatus.POLLING.value
    assert job_is_upstream_awaiting_admin(job)
