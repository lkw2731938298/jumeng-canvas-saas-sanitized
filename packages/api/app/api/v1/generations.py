"""生成任务查询接口：按节点获取最新任务、按任务 ID 查询状态及列出用户任务。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.entity_ids import require_entity_id
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...models.database import get_db
from ...models.job import GenerationJob
from ...models.user import User
from ...core.job_status import JobStatus
from ...schemas.api import JobStatusOut

from ...services.generation_jobs import extract_asset_id, resolve_verified_asset_ids_batch
from ...services.project_access import require_project_access

router = APIRouter()


def _user_facing_status(job: GenerationJob) -> str:
    """将面向用户的任务状态归一化：异常状态仅管理员可见，用户仍显示进行中。"""
    if job.status == JobStatus.ABNORMAL.value:
        return JobStatus.RUNNING.value
    return job.status


def _job_status_out(job: GenerationJob, *, verified_asset_id: Optional[str] = None) -> JobStatusOut:
    output_assets = job.output_assets if isinstance(job.output_assets, list) else []
    result_url = None
    for item in output_assets:
        if isinstance(item, dict) and item.get("url"):
            result_url = str(item["url"])
            break

    asset_id = verified_asset_id or extract_asset_id(output_assets)

    return JobStatusOut(
        id=job.id,
        status=_user_facing_status(job),
        asset_id=asset_id,
        output_assets=output_assets,
        error_message=job.error_message,
        request_id=job.request_id,
        scene=job.scene,
        provider=job.provider,
        provider_task_id=job.provider_task_id,
        result_url=result_url,
        result_text=job.result_text,
    )


@router.get("/latest/by-node", response_model=JobStatusOut)
async def get_latest_job_for_node(
    project_id: str = Query(..., alias="projectId"),
    node_id: str = Query(..., alias="nodeId"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户在指定项目节点上的最新一条生成任务状态。"""
    project_id_int = require_entity_id(project_id, message="项目 ID 无效")
    await require_project_access(db, current_user, project_id)

    result = await db.execute(
        select(GenerationJob)
        .filter(
            GenerationJob.user_id == current_user.id,
            GenerationJob.project_id == project_id_int,
            GenerationJob.node_id == node_id,
        )
        .order_by(GenerationJob.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if not job:
        fail(ErrorCode.JOB_NOT_FOUND)

    verified = await resolve_verified_asset_ids_batch(db, [job])
    return _job_status_out(job, verified_asset_id=verified.get(str(job.id)))


@router.get("/{job_id}", response_model=JobStatusOut)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按任务 ID 查询当前用户的单个生成任务状态。"""
    job_id_int = require_entity_id(job_id, message="任务 ID 无效")

    result = await db.execute(
        select(GenerationJob).filter(GenerationJob.id == job_id_int, GenerationJob.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        fail(ErrorCode.JOB_NOT_FOUND)

    verified = await resolve_verified_asset_ids_batch(db, [job])
    return _job_status_out(job, verified_asset_id=verified.get(str(job.id)))


@router.get("")
async def list_jobs(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """列出当前用户最近的生成任务（最多 50 条，按创建时间倒序）。"""
    result = await db.execute(
        select(GenerationJob).filter(GenerationJob.user_id == current_user.id).order_by(GenerationJob.created_at.desc()).limit(50)
    )
    jobs = result.scalars().all()
    verified_map = await resolve_verified_asset_ids_batch(db, jobs)
    return [_job_status_out(job, verified_asset_id=verified_map.get(str(job.id))) for job in jobs]
