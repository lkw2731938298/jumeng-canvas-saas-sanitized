"""stale_jobs / 重试用尽停车：纯内存 + AsyncMock，不连真实数据库。"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.datetime_util import now_cst_naive
from app.core.job_status import JobStatus
from app.models.job import GenerationJob
from app.services.restart_job_recovery import (
    ATTEMPTS_EXHAUSTED_MESSAGE,
    apply_attempts_exhausted_park,
    job_is_upstream_awaiting_admin,
    park_exhausted_job,
    provider_task_id_from_job,
)
from app.services.stale_jobs import (
    _requeue_or_fail,
    recover_exhausted_pending_jobs,
    recover_exhausted_running_jobs,
)


def test_requeue_exhausted_parks_not_fails():
    job = GenerationJob(
        provider_task_id="task-1",
        attempt_count=3,
        max_attempts=3,
        status=JobStatus.RUNNING.value,
        credit_status="reserved",
    )
    outcome = _requeue_or_fail(job, message="stale", now=now_cst_naive())
    assert outcome == "parked"
    assert job.status == JobStatus.POLLING.value
    assert job.credit_status == "reserved"
    assert job_is_upstream_awaiting_admin(job)
    trace = job.trace_json or {}
    assert trace.get("upstreamAwaitingAdmin", {}).get("reason") == "attempts_exhausted"


def test_requeue_with_retries_left_sets_polling_when_task_id():
    job = GenerationJob(
        provider_task_id="task-1",
        attempt_count=1,
        max_attempts=3,
        status=JobStatus.RUNNING.value,
    )
    outcome = _requeue_or_fail(job, message="stale", now=now_cst_naive())
    assert outcome == "requeued"
    assert job.status == JobStatus.POLLING.value


def test_requeue_with_retries_left_sets_pending_without_task_id():
    job = GenerationJob(attempt_count=1, max_attempts=3, status=JobStatus.RUNNING.value)
    outcome = _requeue_or_fail(job, message="stale", now=now_cst_naive())
    assert outcome == "requeued"
    assert job.status == JobStatus.PENDING.value


def test_apply_attempts_exhausted_park_message():
    job = GenerationJob(status=JobStatus.RUNNING.value, attempt_count=3, max_attempts=3)
    apply_attempts_exhausted_park(job)
    assert ATTEMPTS_EXHAUSTED_MESSAGE in (job.error_message or "")
    assert job_is_upstream_awaiting_admin(job)


@pytest.mark.asyncio
async def test_park_exhausted_job_syncs_upstream_when_task_id():
    job = GenerationJob(id=9, provider_task_id="up-1", status=JobStatus.PENDING.value)
    db = AsyncMock()
    with patch(
        "app.services.restart_job_recovery._action_sync_upstream",
        new_callable=AsyncMock,
    ) as sync:
        await park_exhausted_job(db, job, note="test")
    sync.assert_awaited_once()
    assert job_is_upstream_awaiting_admin(job)
    assert provider_task_id_from_job(job) == "up-1"


@pytest.mark.asyncio
async def test_recover_exhausted_pending_jobs_parks_without_refund():
    job = GenerationJob(
        id=10,
        status=JobStatus.PENDING.value,
        attempt_count=3,
        max_attempts=3,
        credit_status="reserved",
    )
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [job]
    db.execute = AsyncMock(return_value=result_mock)

    with patch(
        "app.services.stale_jobs.park_exhausted_job",
        new_callable=AsyncMock,
    ) as park_mock:
        count = await recover_exhausted_pending_jobs(db)

    assert count == 1
    park_mock.assert_awaited_once()
    assert job.credit_status == "reserved"


@pytest.mark.asyncio
async def test_recover_exhausted_running_jobs_skips_already_parked():
    parked_job = GenerationJob(
        id=11,
        status=JobStatus.RUNNING.value,
        attempt_count=3,
        max_attempts=3,
        started_at=now_cst_naive() - timedelta(hours=2),
    )
    apply_attempts_exhausted_park(parked_job)

    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = [parked_job]
    db.execute = AsyncMock(return_value=result_mock)

    with patch(
        "app.services.stale_jobs.park_exhausted_job",
        new_callable=AsyncMock,
    ) as park_mock:
        count = await recover_exhausted_running_jobs(db, stale_after_minutes=35)

    assert count == 0
    park_mock.assert_not_called()
