"""爆款复刻 API：切镜关键帧（异步任务 + 轮询；多模态拉片走 text/generate）。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail, ok
from ...models.database import get_db
from ...models.user import User
from ...services.cache import check_rate_limit
from ...services.project_access import require_project_access
from ...services.viral_remake import extract_and_register_shot_keyframes
from ...services.viral_remake_tasks import (
    load_extract_task,
    new_extract_task_id,
    run_extract_task_background,
    save_extract_task,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ExtractShotsBody(BaseModel):
    """从已上传的参考视频切镜并注册关键帧。"""

    project_id: str = Field(..., alias="projectId")
    video_asset_id: str = Field(..., alias="videoAssetId")
    # true=强制同步（调试）；默认异步
    sync: bool = False
    # 出海本地化：运镜参考片段去音轨，避免原片对白污染成片语音
    strip_audio: bool = Field(False, alias="stripAudio")

    model_config = {"populate_by_name": True}


@router.post("/extract-shots")
async def extract_shots(
    body: ExtractShotsBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """启动切镜任务：优先异步（Redis 状态）；Redis 不可用或 sync=true 时同步执行。"""
    project_id = (body.project_id or "").strip()
    video_asset_id = (body.video_asset_id or "").strip()
    strip_audio = bool(body.strip_audio)
    if not project_id or not video_asset_id:
        fail(ErrorCode.BAD_REQUEST, message="缺少 projectId 或 videoAssetId")

    # 签名为 (db, user, project_id)；参数顺序反了会把 User 当成 projectId →「项目 ID 无效」
    await require_project_access(db, current_user, project_id)

    allowed = await check_rate_limit(
        f"viral_remake:extract:{current_user.id}",
        limit=8,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED, message="切镜请求过于频繁，请稍后再试")

    # 同步路径：调试或 Redis 不可用
    if body.sync:
        content = await extract_and_register_shot_keyframes(
            db,
            project_id=project_id,
            video_asset_id=video_asset_id,
            strip_audio=strip_audio,
        )
        return ok(
            {
                "taskId": "",
                "status": "succeeded",
                "mode": "sync",
                "result": content,
                "shotCount": content.get("shotCount", 0),
                "shots": content.get("shots", []),
                "videoAssetId": video_asset_id,
            }
        )

    task_id = new_extract_task_id()
    seeded = await save_extract_task(
        task_id,
        {
            "taskId": task_id,
            "status": "pending",
            "projectId": project_id,
            "videoAssetId": video_asset_id,
            "stripAudio": strip_audio,
            "message": "切镜任务已排队…",
            "userId": int(current_user.id),
        },
    )
    if not seeded:
        # Redis 不可用：同步降级，保证功能可用（非算力锁路径）
        logger.warning("viral_remake extract: Redis unavailable, sync fallback")
        content = await extract_and_register_shot_keyframes(
            db,
            project_id=project_id,
            video_asset_id=video_asset_id,
            strip_audio=strip_audio,
        )
        return ok(
            {
                "taskId": "",
                "status": "succeeded",
                "mode": "sync_fallback",
                "result": content,
                "shotCount": content.get("shotCount", 0),
                "shots": content.get("shots", []),
                "videoAssetId": video_asset_id,
            }
        )

    asyncio.create_task(
        run_extract_task_background(
            task_id=task_id,
            project_id=project_id,
            video_asset_id=video_asset_id,
            user_id=int(current_user.id),
            strip_audio=strip_audio,
        ),
        name=f"viral-remake-extract-{task_id[:8]}",
    )
    return ok(
        {
            "taskId": task_id,
            "status": "pending",
            "mode": "async",
            "message": "切镜任务已启动，请轮询状态",
            "videoAssetId": video_asset_id,
        }
    )


@router.get("/extract-shots/{task_id}")
async def get_extract_shots_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """轮询切镜任务状态。"""
    tid = (task_id or "").strip()
    if not tid:
        fail(ErrorCode.BAD_REQUEST, message="缺少 taskId")

    state = await load_extract_task(tid)
    if not state:
        fail(ErrorCode.NOT_FOUND, message="切镜任务不存在或已过期")

    project_id = str(state.get("projectId") or "").strip()
    if project_id:
        await require_project_access(db, current_user, project_id)

    owner = state.get("userId")
    if owner is not None and int(owner) != int(current_user.id):
        # 仅任务创建者可查（防枚举）
        fail(ErrorCode.NOT_FOUND, message="切镜任务不存在或已过期")

    result = state.get("result") if isinstance(state.get("result"), dict) else None
    return ok(
        {
            "taskId": tid,
            "status": str(state.get("status") or "pending"),
            "message": str(state.get("message") or ""),
            "error": state.get("error"),
            "result": result,
            "shotCount": (result or {}).get("shotCount") if result else None,
            "shots": (result or {}).get("shots") if result else None,
            "videoAssetId": state.get("videoAssetId"),
        }
    )
