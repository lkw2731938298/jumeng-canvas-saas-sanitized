"""Tests for one-submit-per-job upstream resume."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.job import GenerationJob
from app.services.upstream_job_resume import (
    clear_upstream_task_ids,
    poll_upstream_result_url,
    upstream_task_id_from_job,
)


def test_upstream_task_id_prefers_provider_task_id():
    job = GenerationJob(provider_task_id="task-a", upstream_job_id="task-b")
    assert upstream_task_id_from_job(job) == "task-a"


def test_upstream_task_id_falls_back_to_upstream_job_id():
    job = GenerationJob(upstream_job_id="task-b")
    assert upstream_task_id_from_job(job) == "task-b"


def test_clear_upstream_task_ids():
    job = GenerationJob(
        provider_task_id="t1",
        upstream_job_id="t2",
        provider_job_id="t3",
        processing_id="t4",
    )
    clear_upstream_task_ids(job)
    assert job.provider_task_id is None
    assert job.upstream_job_id is None
    assert job.provider_job_id is None
    assert job.processing_id is None


@pytest.mark.asyncio
async def test_poll_upstream_result_url_uses_dashscope_for_happyhorse():
    job = GenerationJob(provider="dashscope", provider_task_id="abc-123")
    with patch(
        "app.services.upstream_job_resume.poll_dashscope_task",
        new_callable=AsyncMock,
        return_value="https://example.com/out.mp4",
    ) as poll_mock:
        url = await poll_upstream_result_url(job, model_id="happyhorse_r2v")
    assert url == "https://example.com/out.mp4"
    poll_mock.assert_awaited_once_with("abc-123", creds=poll_mock.await_args.kwargs["creds"])


@pytest.mark.asyncio
async def test_execute_media_job_resumes_when_provider_task_id_present():
    from app.services.media_job_processor import execute_media_job

    job = MagicMock()
    job.id = 10105
    job.status = "pending"
    job.provider_task_id = "dash-task-1"
    job.upstream_job_id = None
    job.provider_job_id = None
    job.processing_id = None
    job.input_params = {
        "projectId": "22",
        "prompt": "test prompt",
        "category": "video",
    }
    job.asset_id = None
    job.output_assets = []

    catalog_model = MagicMock()
    catalog_model.name = "happyhorse_r2v"
    catalog_model.category = "video"
    catalog_model.display_name = "HappyHorse"

    resume_result = MagicMock()
    resume_result.record = {"id": "3005", "fileUrl": "https://x/a.mp4", "projectId": "22", "title": "t"}
    resume_result.asset_category = "video"
    resume_result.message = "synced"
    resume_result.inference = "happyhorse_r2v"

    db = AsyncMock()

    with patch(
        "app.services.media_job_processor.is_job_upstream_submitted",
        new_callable=AsyncMock,
        return_value=False,
    ):
        with patch(
            "app.services.media_job_processor.claim_job_upstream_submit",
            new_callable=AsyncMock,
            return_value=True,
        ):
            with patch(
                "app.services.media_job_processor.log_upstream_api_call",
                new_callable=AsyncMock,
            ):
                with patch(
                    "app.services.media_job_processor._load_existing_job_outcome",
                    new_callable=AsyncMock,
                    return_value=None,
                ):
                    with patch(
                        "app.services.media_job_processor._resume_media_job_from_upstream",
                        new_callable=AsyncMock,
                        return_value=resume_result,
                    ) as resume_mock:
                        with patch(
                            "app.services.media_job_processor.provider_generate_video",
                            new_callable=AsyncMock,
                        ) as submit_mock:
                            outcome = await execute_media_job(db, job, catalog_model)

    resume_mock.assert_awaited_once()
    submit_mock.assert_not_called()
    assert outcome is resume_result
