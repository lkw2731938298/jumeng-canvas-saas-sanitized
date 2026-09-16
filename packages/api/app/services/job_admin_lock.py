"""Redis mutex for admin job interventions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from ..core.error_codes import ErrorCode
from .redis_mutex import redis_mutex


def job_admin_lock_key(job_id: str) -> str:
    return f"lock:admin:job:{job_id}"


@asynccontextmanager
async def job_admin_lock(job_id: str, *, ttl_sec: int = 60) -> AsyncIterator[None]:
    async with redis_mutex(
        job_admin_lock_key(job_id),
        ttl_sec=ttl_sec,
        busy_code=ErrorCode.JOB_ADMIN_IN_PROGRESS,
    ):
        yield
