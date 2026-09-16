"""provider_task_id submit 后立即 commit 的检查点测试（不连真实 DB）。"""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.job_status import JobStatus
from app.integrations.upstream.trace_context import UpstreamTraceSnapshot
from app.models.job import GenerationJob
from app.services.media_job_processor import persist_partial_upstream_trace


@pytest.mark.asyncio
async def test_persist_partial_upstream_trace_commits_when_task_id_present():
    job = GenerationJob(id=10500, status=JobStatus.RUNNING.value)
    db = AsyncMock()
    snap = UpstreamTraceSnapshot(provider="dashscope", provider_task_id="task-abc-123")

    with patch("app.services.media_job_processor.peek_upstream_trace", return_value=snap):
        with patch(
            "app.services.media_job_processor.seal_job_upstream_submit",
            new_callable=AsyncMock,
        ) as seal_mock:
            with patch(
                "app.services.media_job_processor.flush_upstream_call_logs_from_trace",
                new_callable=AsyncMock,
            ):
                await persist_partial_upstream_trace(db, job, model_name="happyhorse_r2v")

    assert job.provider_task_id == "task-abc-123"
    assert job.status == JobStatus.POLLING.value
    seal_mock.assert_awaited_once_with(10500)
    db.flush.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_persist_partial_upstream_trace_flushes_only_without_task_id():
    job = GenerationJob(id=10501, status=JobStatus.RUNNING.value)
    db = AsyncMock()
    snap = UpstreamTraceSnapshot(provider="dashscope", provider_request_id="req-only")

    with patch("app.services.media_job_processor.peek_upstream_trace", return_value=snap):
        with patch(
            "app.services.media_job_processor.seal_job_upstream_submit",
            new_callable=AsyncMock,
        ) as seal_mock:
            with patch(
                "app.services.media_job_processor.flush_upstream_call_logs_from_trace",
                new_callable=AsyncMock,
            ):
                await persist_partial_upstream_trace(db, job, model_name="happyhorse_r2v")

    assert job.provider_request_id == "req-only"
    assert job.status == JobStatus.RUNNING.value
    seal_mock.assert_not_called()
    db.flush.assert_awaited_once()
    db.commit.assert_not_called()
