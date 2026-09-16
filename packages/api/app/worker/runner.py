"""
Job worker runner — picks up pending generation jobs and processes them concurrently.
Canvas media jobs (image/video/audio) and legacy ComfyUI flow jobs.
"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
import asyncio
import contextlib
import logging
import os
import random
import socket
import uuid
from ..core.entity_ids import parse_entity_id
from datetime import datetime

from sqlalchemy import text

from app.core.config import get_settings
from app.models.database import async_session, engine
from app.models.job import GenerationJob
from app.services.media_job_processor import is_canvas_media_job, process_media_generation_job
from app.services.text_job_processor import is_canvas_text_job, process_text_generation_job
from app.core.job_status import JobStatus
from app.services.generation_jobs import (
    apply_execution_timeout_park,
    job_is_execution_timeout_parked,
    mark_job_pending,
    park_job_on_execution_timeout,
)
from app.services.model_concurrency import (
    ModelConcurrencyBusy,
    acquire_model_slot,
    model_key_for_generation_job,
    release_model_slot,
    release_model_slots_for_job,
)
from app.services.restart_job_recovery import (
    admin_parked_sql_clause,
    apply_attempts_exhausted_park,
    revert_worker_claim_conflict,
)
from app.services.worker_job_lock import release_worker_job_lock, try_acquire_worker_job_lock

logger = logging.getLogger(__name__)

_worker_id = f"{socket.gethostname()}:{os.getpid()}"
_active_job_ids: set[str] = set()
_SQL_STATUS = {
    "pending": JobStatus.PENDING.value,
    "running": JobStatus.RUNNING.value,
    "polling": JobStatus.POLLING.value,
}


async def process_job_real(job: GenerationJob) -> None:
    """Process via real ComfyUI."""
    from app.integrations.comfyui.serializer import flow_to_comfy
    from app.integrations.comfyui.client import ComfyUIClient

    comfy = ComfyUIClient()

    try:
        input_data = job.input_params or {}
        nodes = input_data.get("nodes", [])
        edges = input_data.get("edges", [])

        prompt = flow_to_comfy(nodes, edges) if nodes else {"1": {"class_type": "KSampler", "inputs": {}}}
        result = await comfy.queue_prompt(prompt)
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"No prompt_id: {result}")

        job.upstream_job_id = prompt_id
        job.provider = job.provider or "comfyui"
        job.provider_job_id = prompt_id
        job.processing_id = prompt_id
        job.status = JobStatus.POLLING.value
        await _save_job(job)

        history = await comfy.submit_and_wait(prompt, poll_interval=1.0, max_wait_s=600.0)
        if history is None:
            raise TimeoutError("ComfyUI timeout")

        outputs = []
        for _nid, nd in history.get("outputs", {}).items():
            for img in nd.get("images", []):
                outputs.append({"type": "image", "filename": img["filename"]})

        job.output_assets = outputs
        job.status = JobStatus.SUCCEEDED.value
        job.progress_pct = 100

    except TimeoutError as e:
        logger.error("Job %s timed out, parking as running: %s", job.id, e)
        apply_execution_timeout_park(job, error_message=str(e)[:2000])
    except Exception as e:
        logger.error("Job %s failed: %s", job.id, e)
        job.attempt_count = (job.attempt_count or 0) + 1
        job.status = (
            JobStatus.PENDING.value
            if job.attempt_count < (job.max_attempts or 3)
            else JobStatus.FAILED.value
        )
        job.error_message = str(e) if job.status == JobStatus.FAILED.value else None

    finally:
        if not job_is_execution_timeout_parked(job):
            job.completed_at = now_cst_naive()
        await _save_job(job)
        await comfy.close()


async def process_job_mock(job: GenerationJob) -> None:
    """Simulate generation locally — no external AI needed."""
    settings = get_settings()
    delay = random.uniform(settings.ai_mock_min_delay_s, settings.ai_mock_max_delay_s)
    steps = 4
    for i in range(steps):
        job.progress_pct = int((i + 1) / steps * 100)
        await _save_job(job)
        await asyncio.sleep(delay / steps)

    if random.random() < settings.ai_mock_fail_rate:
        job.status = JobStatus.FAILED.value
        job.error_message = "Mock random failure"
    else:
        job.status = JobStatus.SUCCEEDED.value
        job.output_assets = [{
            "type": "image",
            "filename": f"mock_output_{str(job.id)[:8]}.png",
            "url": f"https://picsum.photos/seed/{str(job.id)[:8]}/512/512",
        }]

    job.completed_at = now_cst_naive()
    await _save_job(job)


async def _save_job(job: GenerationJob) -> None:
    async with async_session() as session:
        session.add(job)
        await session.commit()


def _job_needs_model_slot(job: GenerationJob) -> bool:
    return is_canvas_media_job(job) or is_canvas_text_job(job)


async def _defer_generation_job(job_id: str, *, message: str, delay_s: float) -> None:
    """Return a claimed job to pending when model concurrency is saturated."""
    await release_model_slots_for_job(job_id)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE generation_jobs
                SET status = :pending,
                    started_at = NULL,
                    worker_claim_id = NULL,
                    attempt_count = GREATEST(attempt_count - 1, 0),
                    error_message = :message
                WHERE id = :job_id
                  AND status = :running
                """
            ),
            {
                **_SQL_STATUS,
                "job_id": job_id,
                "message": str(message or "")[:2000],
            },
        )
    if delay_s > 0:
        await asyncio.sleep(delay_s)


async def _revert_duplicate_claim(job_id: str, claim_token: str) -> None:
    """撤销认领：有 provider_task_id 则查上游；无 id 有锁则停车；不清 Redis 锁。"""
    await revert_worker_claim_conflict(
        job_id,
        claim_token,
        note="Worker 执行锁冲突或重复认领",
    )


async def _claim_generation_job(lane: str) -> dict | None:
    claim_token = uuid.uuid4().hex
    now_ts = now_cst_naive()
    parked_filter = admin_parked_sql_clause()

    async with engine.begin() as conn:
        picked = (
            await conn.execute(
                text(
                    f"""
                    SELECT id
                    FROM generation_jobs
                    WHERE status IN (:pending, :polling)
                      AND lane = :lane
                      AND attempt_count < max_attempts
                      AND {parked_filter}
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"lane": lane, **_SQL_STATUS},
            )
        ).first()
        if not picked:
            return None
        job_id = str(picked[0])
        await conn.execute(
            text(
                """
                UPDATE generation_jobs
                SET status = :running,
                    started_at = :now_ts,
                    worker_claim_id = :claim_token,
                    attempt_count = attempt_count + 1,
                    error_message = NULL
                WHERE id = :job_id
                  AND status IN (:pending, :polling)
                """
            ),
            {
                **_SQL_STATUS,
                "job_id": job_id,
                "now_ts": now_ts,
                "claim_token": claim_token,
            },
        )
        row = (
            await conn.execute(
                text(
                    """
                    SELECT CAST(id AS CHAR(36)) AS id, lane, job_type, model, worker_claim_id
                    FROM generation_jobs
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
        ).mappings().first()
    if not row or str(row.get("worker_claim_id") or "") != claim_token:
        return None
    payload = dict(row)
    payload["claim_token"] = claim_token
    return payload


async def _job_claim_still_valid(
    session,
    job_id: str,
    claim_token: str,
) -> GenerationJob | None:
    job = await session.get(GenerationJob, parse_entity_id(job_id))
    if not job or job.status != JobStatus.RUNNING.value:
        return None
    if str(job.worker_claim_id or "") != claim_token:
        return None
    return job


async def _process_job_in_session(session, job: GenerationJob) -> None:
    settings = get_settings()
    timeout_s = max(60.0, float(settings.worker_job_timeout_s))

    async def _run() -> None:
        if is_canvas_media_job(job):
            await process_media_generation_job(session, job)
            return
        if is_canvas_text_job(job):
            await process_text_generation_job(session, job)
            return

        if settings.ai_mock_enabled:
            await session.commit()
            await process_job_mock(job)
        else:
            await session.commit()
            await process_job_real(job)

    try:
        await asyncio.wait_for(_run(), timeout=timeout_s)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"任务执行超时（>{int(timeout_s)}s）") from exc


async def _run_claimed_generation_job(job_id: str, claim_token: str) -> None:
    lease = None
    redis_lock_token: str | None = None
    try:
        settings = get_settings()
        lock_ttl = max(1800, int(settings.worker_job_timeout_s) + 300)
        # Worker 调度层执行锁：防止多实例同时跑同一 job（业务层还有 model_submit_lock）
        redis_lock_token = await try_acquire_worker_job_lock(job_id, ttl_sec=lock_ttl)
        if redis_lock_token is None:
            logger.info("[worker] skip job_id=%s — execute lock held elsewhere", job_id)
            await _revert_duplicate_claim(job_id, claim_token)
            return

        async with async_session() as session:
            job = await _job_claim_still_valid(session, job_id, claim_token)
            if not job:
                return

            if _job_needs_model_slot(job):
                await session.commit()
                # 模型并发槽位：全局限制同一模型同时向上游 submit 的数量
                lease = await acquire_model_slot(
                    model_key=model_key_for_generation_job(job),
                    lane=str(job.lane or ""),
                    holder_id=_worker_id,
                    job_id=job_id,
                )
                job = await _job_claim_still_valid(session, job_id, claim_token)
                if not job:
                    return

            await _process_job_in_session(session, job)

    except asyncio.CancelledError:
        raise
    except ModelConcurrencyBusy as exc:
        logger.info(
            "[worker] model concurrency deferred job_id=%s model_key=%s limit=%s",
            job_id,
            exc.model_key,
            exc.limit,
        )
        await _defer_generation_job(
            job_id,
            message=f"MODEL_CONCURRENCY_BUSY:{exc.model_key}:limit={exc.limit}",
            delay_s=exc.retry_after_s,
        )
    except TimeoutError as exc:
        logger.error("[worker] job %s timed out, parking as running: %s", job_id, exc)
        async with async_session() as session:
            job = await session.get(GenerationJob, parse_entity_id(job_id))
            if not job:
                return
            if str(job.worker_claim_id or "") != claim_token:
                return
            await park_job_on_execution_timeout(session, job, error_message=str(exc)[:2000])
            await session.commit()
    except Exception as exc:
        logger.exception("[worker] job %s failed: %s", job_id, exc)
        async with async_session() as session:
            job = await session.get(GenerationJob, parse_entity_id(job_id))
            if not job:
                return
            if str(job.worker_claim_id or "") != claim_token:
                return
            retriable = int(job.attempt_count or 0) < int(job.max_attempts or 3)
            if retriable:
                await mark_job_pending(session, job, error_message=str(exc)[:2000])
            else:
                apply_attempts_exhausted_park(
                    job,
                    error_message=f"{str(exc)[:1500]}（任务重试次数已用尽，请联系管理员处理）"[:2000],
                )
                job.worker_claim_id = None
                job.started_at = None
            await session.commit()
    finally:
        _active_job_ids.discard(job_id)
        await release_model_slot(lease)
        if redis_lock_token is not None:
            await release_worker_job_lock(job_id, redis_lock_token)


async def _reconcile_credits_periodically() -> None:
    from app.services.credit_reconcile_loop import run_credit_reconcile_loop

    await run_credit_reconcile_loop()


async def _run_stale_job_recovery_loop() -> None:
    """Recover long-running generation jobs and sync upstream status periodically."""
    from app.services.job_status_sync import sync_active_generation_jobs

    settings = get_settings()
    interval_s = 300.0
    while True:
        try:
            await asyncio.sleep(interval_s)
            async with async_session() as session:
                summary = await sync_active_generation_jobs(
                    session,
                    limit=100,
                    include_recovery=True,
                )
                await session.commit()
                synced = len(summary.get("upstreamSynced") or [])
                recovered = (
                    int(summary.get("interruptedRecovered") or 0)
                    + int(summary.get("staleRecovered") or 0)
                    + int(summary.get("exhaustedFailed") or 0)
                )
                if synced or recovered:
                    logger.info(
                        "[worker] Job status sync: recovered=%s upstream_synced=%s",
                        recovered,
                        synced,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Stale job recovery loop error: %s", exc)


async def run_worker() -> None:
    settings = get_settings()
    lanes = [lane.strip() for lane in settings.worker_lanes.split(",") if lane.strip()]
    mode = "mock" if settings.ai_mock_enabled else "real"
    parallelism = max(1, settings.worker_parallelism or settings.worker_burst_limit)
    idle_sleep_s = max(0.1, settings.worker_idle_sleep_s)
    burst_sleep_s = max(0.01, settings.worker_burst_sleep_s)

    from app.services.model_concurrency import ensure_model_runtime_slots_table

    await ensure_model_runtime_slots_table()

    from app.services.restart_job_recovery import recover_duplicate_claim_jobs
    from app.services.stale_jobs import recover_interrupted_jobs_on_startup

    async with async_session() as session:
        from app.services.credential_service import warm_runtime_llm_keys
        from app.services.model_catalog_runtime import warm_runtime_model_specs
        from app.core.llm_keys import refresh_llm_keys_cache

        await warm_runtime_llm_keys(session)
        refresh_llm_keys_cache()
        await warm_runtime_model_specs(session)
        from app.services.prompt_platform_runtime import warm_prompt_platform_caches

        await warm_prompt_platform_caches()
        recovered = await recover_interrupted_jobs_on_startup(session)
        duplicate = await recover_duplicate_claim_jobs(session)
        await session.commit()
        if recovered:
            logger.warning("[worker] Recovered %s interrupted running job(s) on startup", recovered)
        if duplicate:
            logger.warning("[worker] Reconciled %s DUPLICATE claim job(s) on startup", duplicate)

    from app.integrations.oss.service import get_oss

    if not settings.canvas_storage_local_only:
        oss = get_oss()
        if not oss.bucket:
            logger.error(
                "[worker] CANVAS_STORAGE_LOCAL_ONLY=false but OSS is not configured — "
                "media generation will fail on upload. Check OSS_* in worker.env and oss2."
            )
        else:
            logger.info(
                "[worker] OSS storage ready bucket=%s prefix=%s",
                settings.oss_bucket,
                settings.oss_object_prefix,
            )

    active: set[asyncio.Task[None]] = set()
    if settings.credit_reconcile_enabled:
        asyncio.create_task(_reconcile_credits_periodically(), name="credit-reconcile")
    asyncio.create_task(_run_stale_job_recovery_loop(), name="stale-job-recovery")
    lane_index = 0
    logger.info(
        "[worker] Starting (%s mode) worker_id=%s lanes=%s parallelism=%s stale_lock_s=%s",
        mode,
        _worker_id,
        lanes,
        parallelism,
        settings.worker_stale_lock_s,
    )

    while True:
        claimed = 0
        try:
            while len(active) < parallelism and lanes:
                row = None
                for _ in range(len(lanes)):
                    lane = lanes[lane_index % len(lanes)]
                    lane_index += 1
                    row = await _claim_generation_job(lane)
                    if row:
                        break
                if not row:
                    break
                job_id = str(row["id"])
                claim_token = str(row["claim_token"])
                if job_id in _active_job_ids:
                    await _revert_duplicate_claim(job_id, claim_token)
                    continue
                _active_job_ids.add(job_id)
                claimed += 1
                task = asyncio.create_task(
                    _run_claimed_generation_job(job_id, claim_token),
                    name=f"generation-job:{row.get('job_type')}:{job_id}",
                )
                active.add(task)
                task.add_done_callback(active.discard)
                await asyncio.sleep(burst_sleep_s)
        except Exception as exc:
            logger.error("[worker] Loop error: %s", exc)

        if active:
            done, _pending = await asyncio.wait(
                active,
                timeout=burst_sleep_s if claimed > 0 else min(idle_sleep_s, 1.0),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                with contextlib.suppress(Exception):
                    task.result()
        elif claimed <= 0:
            await asyncio.sleep(idle_sleep_s)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_worker())
