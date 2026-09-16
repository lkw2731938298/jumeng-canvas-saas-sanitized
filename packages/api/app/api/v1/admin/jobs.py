from ....core.datetime_util import now_cst_naive
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_JOBS
from ....core.entity_ids import require_entity_id
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.job import GenerationJob
from ....models.project import Project
from ....models.user import User
from ....schemas.admin import (
    AdminJobActionIn,
    AdminJobActionOut,
    AdminJobActionResultOut,
    AdminJobSyncActiveOut,
    AdminJobAssetOut,
    AdminJobDetailOut,
    AdminJobListOut,
    AdminJobOut,
)
from ....services.job_admin_actions import execute_admin_job_action, list_admin_job_actions
from ....services.job_status_sync import sync_active_generation_jobs
from ....services.admin_jobs import (
    build_admin_job_row,
    build_admin_job_rows,
    export_admin_jobs_csv,
    load_models,
    load_projects,
    load_users,
    parse_admin_job_filters,
    apply_admin_job_filters,
)
from ....services.generation_jobs import resolve_verified_job_assets_batch

router = APIRouter()


def _project_asset_out(record: dict, project_id: str) -> AdminJobAssetOut:
    return AdminJobAssetOut(
        id=str(record["id"]),
        project_id=project_id,
        title=str(record.get("title") or "未命名"),
        category=str(record.get("category") or ""),
        file_url=record.get("fileUrl"),
        thumbnail_url=record.get("thumbnailUrl"),
        file_type=record.get("fileType"),
        file_size=record.get("fileSize"),
        created_at=record.get("createdAt"),
    )


async def _job_out(
    db: AsyncSession,
    job: GenerationJob,
    *,
    projects: dict[str, Project],
    users: dict[str, User],
    models: dict,
) -> AdminJobOut:
    row = await build_admin_job_row(db, job, projects=projects, users=users, models=models)
    return AdminJobOut(**row)


async def _job_detail_out(
    db: AsyncSession,
    job: GenerationJob,
    *,
    projects: dict[str, Project],
    users: dict[str, User],
    models: dict,
) -> AdminJobDetailOut:
    # Admin read path: resolve assets from DB only (no per-request OSS scan).
    asset_map = await resolve_verified_job_assets_batch(db, [job], sync_oss=False)
    verified_asset_id, asset_record = asset_map.get(str(job.id), (None, None))
    row = await build_admin_job_row(
        db,
        job,
        projects=projects,
        users=users,
        models=models,
        verified_asset_id=verified_asset_id,
        asset_resolved=True,
    )
    base = AdminJobOut(**row)
    project_id = base.project_id or ""
    project_asset = _project_asset_out(asset_record, project_id) if asset_record and project_id else None
    actions = await list_admin_job_actions(db, job.id)
    return AdminJobDetailOut(
        **base.model_dump(),
        input_params=job.input_params if isinstance(job.input_params, dict) else {},
        output_assets=job.output_assets if isinstance(job.output_assets, list) else [],
        result_text=job.result_text,
        trace_json=job.trace_json if isinstance(job.trace_json, dict) else {},
        project_asset=project_asset,
        admin_actions=[
            AdminJobActionOut(
                id=str(row.id),
                job_id=str(row.job_id),
                operator_id=str(row.operator_id),
                action=row.action,
                before_status=row.before_status,
                after_status=row.after_status,
                before_credit_status=row.before_credit_status,
                after_credit_status=row.after_credit_status,
                note=row.note,
                payload=row.payload if isinstance(row.payload, dict) else {},
                created_at=row.created_at,
            )
            for row in actions
        ],
    )


@router.get("/jobs", response_model=AdminJobListOut)
async def list_admin_jobs(
    status: Optional[str] = Query(None),
    lane: Optional[str] = Query(None),
    anomaly_reason: Optional[str] = Query(None, alias="anomalyReason"),
    has_admin_action: bool = Query(False, alias="hasAdminAction"),
    user_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    project_search: Optional[str] = Query(None),
    created_from: Optional[str] = Query(None),
    created_to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_JOBS)),
):
    filters = parse_admin_job_filters(
        status=status,
        lane=lane,
        anomaly_reason=anomaly_reason,
        has_admin_action=has_admin_action,
        user_id=user_id,
        project_id=project_id,
        job_id=job_id,
        project_search=project_search,
        created_from=created_from,
        created_to=created_to,
    )

    query = select(GenerationJob).options(defer(GenerationJob.result_text))
    count_query = select(func.count(GenerationJob.id))
    query, count_query = apply_admin_job_filters(query, count_query, filters)

    total = (await db.execute(count_query)).scalar_one()
    offset = (page - 1) * page_size
    result = await db.execute(
        query.order_by(GenerationJob.id.desc(), GenerationJob.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    jobs = result.scalars().all()

    projects = await load_projects(db, jobs)
    users = await load_users(db, jobs)
    models = await load_models(db, jobs)

    rows = await build_admin_job_rows(
        db,
        jobs,
        projects=projects,
        users=users,
        models=models,
        sync_oss=False,
    )
    items = [AdminJobOut(**row) for row in rows]

    return AdminJobListOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/jobs/sync-active", response_model=AdminJobSyncActiveOut)
async def sync_admin_active_jobs(
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_JOBS)),
):
    summary = await sync_active_generation_jobs(db, limit=limit, include_recovery=True)
    await db.commit()
    return AdminJobSyncActiveOut(**summary)


@router.post("/jobs/{job_id}/actions", response_model=AdminJobActionResultOut)
async def admin_job_action(
    job_id: str,
    body: AdminJobActionIn,
    db: AsyncSession = Depends(get_db),
    operator: User = Depends(require_permission(PERM_JOBS)),
):
    job_id_int = require_entity_id(job_id, message="任务 ID 无效")

    result = await execute_admin_job_action(
        db,
        job_id=job_id_int,
        operator=operator,
        action=body.action,
        note=body.note,
        payload=body.payload,
    )
    await db.commit()
    return AdminJobActionResultOut(**result)


@router.get("/jobs/export")
async def export_admin_jobs(
    status: Optional[str] = Query(None),
    lane: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    project_search: Optional[str] = Query(None),
    created_from: Optional[str] = Query(None),
    created_to: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_JOBS)),
):
    filters = parse_admin_job_filters(
        status=status,
        lane=lane,
        user_id=user_id,
        project_id=project_id,
        job_id=job_id,
        project_search=project_search,
        created_from=created_from,
        created_to=created_to,
    )
    csv_text, total = await export_admin_jobs_csv(db, filters)
    stamp = now_cst_naive().strftime("%Y%m%d_%H%M%S")
    filename = f"generation_jobs_{stamp}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Total": str(total),
        },
    )


@router.get("/jobs/{job_id}", response_model=AdminJobDetailOut)
async def get_admin_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_JOBS)),
):
    job_id_int = require_entity_id(job_id, message="任务 ID 无效")

    result = await db.execute(select(GenerationJob).filter(GenerationJob.id == job_id_int))
    job = result.scalar_one_or_none()
    if not job:
        fail(ErrorCode.JOB_NOT_FOUND)

    projects = await load_projects(db, [job])
    users = await load_users(db, [job])
    models = await load_models(db, [job])
    return await _job_detail_out(db, job, projects=projects, users=users, models=models)
