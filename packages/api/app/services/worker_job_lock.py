"""Worker 执行 generation job 时的 Redis 互斥锁（Token 锁，尽力而为）。

与 model_submit 标记锁分工：
- 本锁（lock:worker:execute:{jobId}）：Worker 调度层防并发执行
- claim_job_upstream_submit：业务层防双 POST
"""

from __future__ import annotations

from ..common.utils.redis_token_mutex import release_token_mutex, try_acquire_token_mutex


def worker_execute_lock_key(job_id: str | int) -> str:
    return f"lock:worker:execute:{job_id}"


async def try_acquire_worker_job_lock(job_id: str, *, ttl_sec: int = 1800) -> str | None:
    return await try_acquire_token_mutex(
        worker_execute_lock_key(job_id),
        ttl_sec=ttl_sec,
    )


async def release_worker_job_lock(job_id: str, token: str) -> None:
    await release_token_mutex(worker_execute_lock_key(job_id), token)


async def is_worker_job_lock_held(job_id: str | int) -> bool:
    """判断 Worker 是否仍在执行该任务（自动 sync 据此跳过，避免双下载双入库）。"""
    from ..common.utils.redis_client import get_redis

    client = await get_redis()
    if client is None:
        return False
    try:
        return bool(await client.exists(worker_execute_lock_key(job_id)))
    except Exception:
        return False
