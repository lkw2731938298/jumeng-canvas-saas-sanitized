"""爆款复刻 · 切镜异步任务状态（Redis）。

POST 立即返回 taskId，后台用独立 DB session 跑 ffmpeg；
前端轮询 GET，避免长视频同步堵死 API worker。
Redis 不可用时由 API 层回退为同步执行（功能可用，非算力锁路径）。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ..common.utils.redis_client import get_redis
from ..core.datetime_util import cst_iso_now

logger = logging.getLogger(__name__)

TASK_TTL_SEC = 3600
_KEY_PREFIX = "viral_remake:extract:"


def _key(task_id: str) -> str:
    return f"{_KEY_PREFIX}{task_id}"


def new_extract_task_id() -> str:
    return uuid.uuid4().hex


async def save_extract_task(task_id: str, payload: dict[str, Any]) -> bool:
    """写入任务状态；成功返回 True，Redis 不可用返回 False。"""
    redis = await get_redis()
    if not redis:
        return False
    body = {**payload, "updatedAt": cst_iso_now()}
    try:
        await redis.setex(_key(task_id), TASK_TTL_SEC, json.dumps(body, ensure_ascii=False))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("viral_remake task save failed: %s", exc)
        return False


async def load_extract_task(task_id: str) -> dict[str, Any] | None:
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
        logger.debug("viral_remake task load failed: %s", exc)
        return None


async def run_extract_task_background(
    *,
    task_id: str,
    project_id: str,
    video_asset_id: str,
    user_id: int | None = None,
    strip_audio: bool = False,
) -> None:
    """后台切镜：独立 session，结果写 Redis。"""
    from ..models.database import async_session
    from .viral_remake import extract_and_register_shot_keyframes

    prev = await load_extract_task(task_id) or {}
    owner_id = user_id if user_id is not None else prev.get("userId")

    async def _persist(extra: dict[str, Any]) -> None:
        payload: dict[str, Any] = {
            "taskId": task_id,
            "projectId": project_id,
            "videoAssetId": video_asset_id,
            "stripAudio": bool(strip_audio),
            **extra,
        }
        if owner_id is not None:
            payload["userId"] = owner_id
        await save_extract_task(task_id, payload)

    await _persist({"status": "running", "message": "正在切镜并提取关键帧…"})
    try:
        async with async_session() as db:
            try:
                content = await extract_and_register_shot_keyframes(
                    db,
                    project_id=project_id,
                    video_asset_id=video_asset_id,
                    strip_audio=bool(strip_audio),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        await _persist(
            {
                "status": "succeeded",
                "message": f"已切出 {content.get('shotCount', 0)} 个镜头",
                "result": content,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("viral_remake extract task %s failed", task_id)
        from ..core.errors import AppError

        msg = exc.message if isinstance(exc, AppError) else str(exc) or "切镜失败"
        await _persist({"status": "failed", "message": msg, "error": msg})
