"""按模型限制同时向上游发起的 API 调用数（并发槽位）。

与 model_submit_lock（按 job 串行）互补：
- model_submit_lock：同一 job 不能被两个 Worker 同时执行
- acquire_model_slot：同一模型全局并发不超过配置上限（如 image=4、video=2）
"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ..core.config import get_settings
from ..models.database import engine
from ..models.job import GenerationJob

ENSURE_SLOTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS model_runtime_slots (
    id CHAR(36) NOT NULL PRIMARY KEY,
    model_key VARCHAR(128) NOT NULL,
    lane VARCHAR(32) NOT NULL DEFAULT '',
    holder_id VARCHAR(128) NOT NULL DEFAULT '',
    job_id BIGINT UNSIGNED NOT NULL,
    expires_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    KEY ix_model_runtime_slots_model_key_expires (model_key, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


class ModelConcurrencyBusy(Exception):
    def __init__(self, model_key: str, limit: int, retry_after_s: float = 5.0) -> None:
        super().__init__(f"model concurrency busy: {model_key} limit={limit}")
        self.model_key = model_key
        self.limit = limit
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class ModelSlotLease:
    lease_id: str
    model_key: str
    lock_name: str


def normalize_model_key(raw: str, *, lane: str = "") -> str:
    s = str(raw or "").strip().lower()
    if not s:
        s = str(lane or "default").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:128] or "default"


def _lock_name(model_key: str) -> str:
    """MySQL GET_LOCK 名称，用于槽位计数变更时的进程间互斥。"""
    return f"model-slot:{model_key}"[:64]


def model_key_for_generation_job(job: GenerationJob) -> str:
    params = job.input_params if isinstance(job.input_params, dict) else {}
    model = str(job.model or params.get("model") or "")
    return normalize_model_key(model or job.job_type or job.lane, lane=job.lane or "")


def default_limit_for_lane(lane: str) -> int:
    settings = get_settings()
    l = str(lane or "").strip().lower()
    if l == "image":
        return max(1, settings.model_concurrency_default_image)
    if l == "video":
        return max(1, settings.model_concurrency_default_video)
    if l == "audio":
        return max(1, settings.model_concurrency_default_audio)
    if l == "text_llm":
        return max(1, settings.model_concurrency_default_text)
    return max(1, settings.model_concurrency_default)


async def ensure_model_runtime_slots_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(text(ENSURE_SLOTS_TABLE_SQL.strip()))


async def resolve_model_concurrency_limit(model_key: str, lane: str) -> int:
    return default_limit_for_lane(lane)


async def release_model_slots_for_job(job_id: str) -> None:
    """Drop stale concurrency leases for a job (e.g. worker restart mid-run)."""
    if not job_id:
        return
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM model_runtime_slots WHERE job_id = :job_id"),
            {"job_id": job_id},
        )


async def acquire_model_slot(
    *,
    model_key: str,
    lane: str,
    holder_id: str,
    job_id: str,
    ttl_s: int = 600,
) -> ModelSlotLease:
    """占用一个模型并发槽位，成功后才允许 Worker 向上游发起 submit/轮询。

    通过 MySQL GET_LOCK + model_runtime_slots 表计数实现；
    槽位满时抛出 ModelConcurrencyBusy，Worker 延迟重试而非直接失败。
    ttl_s 默认 600s：槽位租约过期时间，防止 Worker 崩溃后槽位永久占用。
    """
    key = normalize_model_key(model_key, lane=lane)
    limit = await resolve_model_concurrency_limit(key, lane)
    now_ts = now_cst_naive()
    lease_id = str(uuid.uuid4())
    lock_name = _lock_name(key)
    async with engine.begin() as conn:
        # 跨连接互斥：同一 model_key 的槽位增减必须串行
        locked = (
            await conn.execute(
                text("SELECT GET_LOCK(:lock_name, 10)"),
                {"lock_name": lock_name},
            )
        ).scalar()
        if locked != 1:
            settings = get_settings()
            raise ModelConcurrencyBusy(
                key,
                limit,
                retry_after_s=float(settings.model_concurrency_retry_after_s),
            )
        try:
            # 清理过期槽位及本 job 的历史残留（如 Worker 重启）
            await conn.execute(
                text("DELETE FROM model_runtime_slots WHERE expires_at <= :now_ts"),
                {"now_ts": now_ts},
            )
            await conn.execute(
                text("DELETE FROM model_runtime_slots WHERE job_id = :job_id"),
                {"job_id": job_id},
            )
            used = int(
                (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) FROM model_runtime_slots "
                            "WHERE model_key = :model_key AND expires_at > :now_ts"
                        ),
                        {"model_key": key, "now_ts": now_ts},
                    )
                ).scalar()
                or 0
            )
            if used >= limit:
                settings = get_settings()
                raise ModelConcurrencyBusy(
                    key,
                    limit,
                    retry_after_s=float(settings.model_concurrency_retry_after_s),
                )
            # 登记本 job 占用的槽位
            await conn.execute(
                text(
                    """
                    INSERT INTO model_runtime_slots(
                        id, model_key, lane, holder_id, job_id, expires_at, created_at, updated_at
                    )
                    VALUES(
                        :id, :model_key, :lane, :holder_id, :job_id,
                        :expires_at, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": lease_id,
                    "model_key": key,
                    "lane": lane[:32],
                    "holder_id": holder_id[:128],
                    "job_id": job_id,
                    "expires_at": now_ts + timedelta(seconds=max(30, int(ttl_s))),
                    "created_at": now_ts,
                    "updated_at": now_ts,
                },
            )
        finally:
            await conn.execute(
                text("SELECT RELEASE_LOCK(:lock_name)"),
                {"lock_name": lock_name},
            )
    return ModelSlotLease(lease_id=lease_id, model_key=key, lock_name=lock_name)


async def release_model_slot(lease: ModelSlotLease | None) -> None:
    """任务结束（成功/失败）后释放模型并发槽位。"""
    if not lease:
        return
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM model_runtime_slots WHERE id = :id"),
            {"id": lease.lease_id},
        )
