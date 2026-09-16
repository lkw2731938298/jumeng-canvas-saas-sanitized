"""Admin job list field helpers: video duration and upstream credits."""

from datetime import datetime

from app.models.job import GenerationJob, Model
from app.services.admin_jobs import (
    _format_cn_datetime,
    job_execution_seconds,
    job_video_duration_seconds,
    upstream_credits_cost,
)


def _video_job(**kwargs) -> GenerationJob:
    defaults = {
        "lane": "video",
        "input_params": {"optionSnapshot": {"duration": "10"}},
        "trace_json": {},
    }
    defaults.update(kwargs)
    return GenerationJob(**defaults)


def test_format_cn_datetime_from_cst_naive():
    dt = datetime(2026, 7, 2, 17, 53, 59)
    assert _format_cn_datetime(dt) == "2026-07-02 17:53:59"


def test_job_video_duration_from_option_snapshot():
    job = _video_job()
    assert job_video_duration_seconds(job, None) == 10


def test_job_video_duration_from_upstream_billing():
    job = _video_job(
        input_params={},
        trace_json={"billing": {"kind": "video_seconds", "amount": 8}},
    )
    assert job_video_duration_seconds(job, None) == 8


def test_job_video_duration_empty_for_image_lane():
    job = GenerationJob(lane="image", input_params={"optionSnapshot": {"duration": "10"}})
    assert job_video_duration_seconds(job, None) is None


def test_upstream_credits_only_when_kind_is_credits():
    job = GenerationJob(
        upstream_credit_cost=999,
        trace_json={"billing": {"kind": "tokens", "amount": 999}},
    )
    assert upstream_credits_cost(job) is None

    job2 = GenerationJob(
        upstream_credit_cost=42,
        trace_json={"billing": {"kind": "credits", "amount": 42}},
    )
    assert upstream_credits_cost(job2) == 42


def test_job_execution_seconds():
    started = datetime(2026, 1, 1, 12, 0, 0)
    completed = datetime(2026, 1, 1, 12, 0, 15)
    job = GenerationJob(started_at=started, completed_at=completed)
    assert job_execution_seconds(job) == 15
