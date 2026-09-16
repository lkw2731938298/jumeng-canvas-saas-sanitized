from unittest.mock import AsyncMock, MagicMock

import pytest

from app.integrations.upstream.trace_context import note_upstream, take_upstream_trace
from app.models.job import GenerationJob
from app.services.generation_call_logs import (
    flush_upstream_call_logs_from_trace,
    log_upstream_api_call,
    reset_upstream_call_log_cursor,
)


@pytest.fixture(autouse=True)
def _clear_upstream_trace():
    take_upstream_trace()
    reset_upstream_call_log_cursor()
    yield
    take_upstream_trace()
    reset_upstream_call_log_cursor()


def _job(**kwargs) -> GenerationJob:
    defaults = {
        "id": 101,
        "model": "wan27_i2v",
        "lane": "video",
        "project_id": 1,
        "node_id": "node-1",
        "user_id": 2,
        "actor_user_id": 2,
        "attempt_count": 2,
        "input_params": {"category": "video"},
        "provider": "dashscope",
    }
    defaults.update(kwargs)
    return GenerationJob(**defaults)


@pytest.mark.asyncio
async def test_log_upstream_api_call_appends_without_dedupe():
    db = AsyncMock()
    job = _job()

    row1 = await log_upstream_api_call(db, job, action="video_submit", outcome="pending")
    row2 = await log_upstream_api_call(db, job, action="poll_resume", outcome="pending")

    assert row1.phase == "upstream"
    assert row2.phase == "upstream"
    assert db.add.call_count == 2
    assert db.flush.await_count == 2


@pytest.mark.asyncio
async def test_flush_upstream_call_logs_from_trace_only_new_events():
    db = AsyncMock()
    job = _job()

    note_upstream(provider="dashscope", provider_task_id="task-1", event="submit")
    rows_first = await flush_upstream_call_logs_from_trace(db, job, model_id="wan27_i2v")
    assert len(rows_first) == 1
    assert rows_first[0].request_payload["action"] == "submit"

    note_upstream(event="submit_response", detail={"requestId": "req-1"})
    rows_second = await flush_upstream_call_logs_from_trace(db, job, model_id="wan27_i2v")
    assert len(rows_second) == 1
    assert rows_second[0].request_payload["action"] == "submit_response"
    assert db.add.call_count == 2


@pytest.mark.asyncio
async def test_reset_upstream_call_log_cursor_replays_events_on_retry():
    db = AsyncMock()
    job = _job(attempt_count=1)

    note_upstream(provider="vidu", provider_task_id="task-a", event="submit")
    await flush_upstream_call_logs_from_trace(db, job, model_id="vidu_q2_i2v")
    assert db.add.call_count == 1

    reset_upstream_call_log_cursor()
    take_upstream_trace()
    job.attempt_count = 2
    note_upstream(provider="vidu", provider_task_id="task-a", event="poll_resume")
    rows = await flush_upstream_call_logs_from_trace(db, job, model_id="vidu_q2_i2v")
    assert len(rows) == 1
    assert rows[0].request_payload["action"] == "poll_resume"
    assert rows[0].request_payload["attemptCount"] == 2
