"""Generation job ID allocation via Redis sequence (发号器)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .redis_sequence import (
    SEQUENCE_GENERATION_JOB,
    bootstrap_sequence,
    next_sequence,
)

JOB_ID_START = 10001


async def _max_job_id(db: AsyncSession) -> int:
    result = await db.execute(text("SELECT COALESCE(MAX(id), 0) FROM generation_jobs"))
    val = result.scalar()
    return int(val or 0)


async def ensure_job_id_sequence_seeded(db: AsyncSession) -> None:
    """Align Redis counter with DB max so new job IDs never collide."""
    db_max = await _max_job_id(db)
    floor = max(db_max, JOB_ID_START - 1)
    await bootstrap_sequence(SEQUENCE_GENERATION_JOB, floor)


async def allocate_generation_job_id(db: AsyncSession) -> int:
    await ensure_job_id_sequence_seeded(db)
    seq = await next_sequence(SEQUENCE_GENERATION_JOB)
    if seq < JOB_ID_START:
        await bootstrap_sequence(SEQUENCE_GENERATION_JOB, JOB_ID_START - 1)
        seq = await next_sequence(SEQUENCE_GENERATION_JOB)
    return seq
