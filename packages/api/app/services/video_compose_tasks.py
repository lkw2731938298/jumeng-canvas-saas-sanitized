"""完整剪辑 · 异步拼接任务状态（Redis）。

POST 立即返回 taskId，后台用独立 DB session 跑 ffmpeg；
前端轮询 GET 拿 progress/result，避免长片同步堵死 API。
Redis 不可用时由 API 层回退为同步执行。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ..common.utils.redis_client import get_redis
from ..core.datetime_util import cst_iso_now

logger = logging.getLogger(__name__)

TASK_TTL_SEC = 7200  # 成片可能较长，保留 2 小时
_KEY_PREFIX = "video_compose:task:"


def _key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


def new_compose_task_id() -> str:
    return uuid.uuid4().hex


async def save_compose_task(task_id: str, payload: dict[str, Any]) -> bool:
    """写入任务状态；成功返回 True，Redis 不可用返回 False。"""
    redis = await get_redis()
    if not redis:
        return False
    body = {**payload, "updatedAt": cst_iso_now()}
    try:
        await redis.setex(_key(task_id), TASK_TTL_SEC, json.dumps(body, ensure_ascii=False))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_compose task save failed: %s", exc)
        return False


async def load_compose_task(task_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    if not redis:
        return None
    try:
        raw = await redis.get(_key(task_id))
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("video_compose task load failed: %s", exc)
        return None


async def run_compose_task_background(
    *,
    task_id: str,
    project_id: str,
    clips: list[dict[str, Any]],
    audio_clips: list[dict[str, Any]] | None,
    title: str | None,
    user_id: int | None = None,
) -> None:
    """后台拼接：独立 session，进度与结果写 Redis。"""
    from ..models.database import async_session
    from .video_compose import compose_project_video_assets

    prev = await load_compose_task(task_id) or {}
    owner_id = user_id if user_id is not None else prev.get("userId")

    async def _persist(extra: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "taskId": task_id,
            "projectId": project_id,
            **extra,
        }
        if owner_id is not None:
            payload["userId"] = owner_id
        await save_compose_task(task_id, payload)

    async def on_progress(pct: float, message: str) -> None:
        await _persist(
            {
                "status": "running",
                "progress": round(max(0.0, min(100.0, float(pct))), 1),
                "message": message,
            }
        )

    await _persist({"status": "running", "progress": 1, "message": "开始拼接…"})
    try:
        async with async_session() as db:
            try:
                content = await compose_project_video_assets(
                    db,
                    project_id=project_id,
                    clips=clips,
                    audio_clips=audio_clips,
                    title=title,
                    on_progress=on_progress,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        asset = content.get("asset") if isinstance(content, dict) else None
        await _persist(
            {
                "status": "succeeded",
                "progress": 100,
                "message": "拼接完成",
                "result": {
                    "asset": asset if isinstance(asset, dict) else {},
                    "durationSec": content.get("durationSec"),
                    "clipCount": content.get("clipCount"),
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("video_compose task %s failed", task_id)
        from ..core.errors import AppError

        msg = exc.message if isinstance(exc, AppError) else str(exc) or "拼接失败"
        await _persist(
            {
                "status": "failed",
                "progress": 0,
                "message": msg,
                "error": msg,
            }
        )
