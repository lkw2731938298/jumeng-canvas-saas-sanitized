from unittest.mock import AsyncMock, patch

import pytest

from app.core.job_status import JobStatus
from app.models.job import GenerationJob
from app.services.restart_job_recovery import (
    RESTART_LOCK_WITHOUT_TASK_MESSAGE,
    apply_upstream_awaiting_admin_park,
    job_is_upstream_awaiting_admin,
    provider_task_id_from_job,
    reconcile_job_after_restart,
)


def test_provider_task_id_only_reads_column():
    job = GenerationJob(provider_task_id="task-a", upstream_job_id="legacy")
    assert provider_task_id_from_job(job) == "task-a"


@pytest.mark.asyncio
async def test_reconcile_with_task_id_sets_polling_and_syncs():
    job = GenerationJob(id=1, provider_task_id="task-9", status=JobStatus.RUNNING.value)
    db = AsyncMock()
    with patch(
        "app.services.restart_job_recovery._action_sync_upstream",
        new_callable=AsyncMock,
    ) as sync:
        outcome = await reconcile_job_after_restart(db, job, note="test")
    assert outcome == "synced"
    assert job.status == JobStatus.POLLING.value
    assert job.error_message is None
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_reconcile_no_task_id_with_lock_parks():
    job = GenerationJob(id=2, status=JobStatus.RUNNING.value)
    db = AsyncMock()
    with patch(
        "app.services.restart_job_recovery.restart_uncertain_lock_active",
        new_callable=AsyncMock,
        return_value=True,
    ):
        outcome = await reconcile_job_after_restart(db, job, note="test")
    assert outcome == "parked"
    assert job.status == JobStatus.POLLING.value
    assert job_is_upstream_awaiting_admin(job)
    assert RESTART_LOCK_WITHOUT_TASK_MESSAGE in (job.error_message or "")


@pytest.mark.asyncio
async def test_reconcile_no_task_id_no_lock_requeues():
    job = GenerationJob(id=3, status=JobStatus.RUNNING.value, attempt_count=1, max_attempts=3)
    db = AsyncMock()
    with patch(
        "app.services.restart_job_recovery.restart_uncertain_lock_active",
        new_callable=AsyncMock,
        return_value=False,
    ):
        outcome = await reconcile_job_after_restart(db, job, note="test")
    assert outcome == "requeued"
    assert job.status == JobStatus.PENDING.value


@pytest.mark.asyncio
async def test_reconcile_attempts_exhausted_parks():
    job = GenerationJob(
        id=5,
        status=JobStatus.RUNNING.value,
        attempt_count=3,
        max_attempts=3,
    )
    db = AsyncMock()
    with patch(
        "app.services.restart_job_recovery.restart_uncertain_lock_active",
        new_callable=AsyncMock,
        return_value=False,
    ):
        outcome = await reconcile_job_after_restart(db, job, note="test")
    assert outcome == "parked"
    assert job_is_upstream_awaiting_admin(job)
    trace = job.trace_json or {}
    assert trace.get("upstreamAwaitingAdmin", {}).get("reason") == "attempts_exhausted"


def test_upstream_admin_park_sets_trace():
    job = GenerationJob(id=4, status=JobStatus.RUNNING.value)
    apply_upstream_awaiting_admin_park(
        job,
        error_message="wait",
        reason="restart_lock_without_task_id",
    )
    assert job.status == JobStatus.POLLING.value
    assert job_is_upstream_awaiting_admin(job)
