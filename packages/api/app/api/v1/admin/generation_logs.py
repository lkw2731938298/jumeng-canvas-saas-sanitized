from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.datetime_util import parse_query_datetime, to_cst_iso
from ....core.deps import require_permission
from ....core.admin_permissions import PERM_GENERATION_LOGS
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....services.admin_generation_logs import (
    build_admin_generation_log_rows,
    list_admin_generation_logs,
    parse_admin_generation_log_filters,
    purge_generation_call_logs_before,
)

router = APIRouter()


def _parse_filter_datetime(value: str | None):
    if not value:
        return None
    try:
        return parse_query_datetime(value)
    except ValueError:
        fail(ErrorCode.BAD_REQUEST, message="时间筛选参数无效")


class AdminGenerationLogOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    job_id: Optional[str] = Field(None, alias="jobId")
    phase: str
    submit_source: Optional[str] = Field(None, alias="submitSource")
    lane: Optional[str] = None
    category: str
    project_id: Optional[str] = Field(None, alias="projectId")
    project_title: Optional[str] = Field(None, alias="projectTitle")
    node_id: Optional[str] = Field(None, alias="nodeId")
    workflow_id: Optional[str] = Field(None, alias="workflowId")
    actor_user_id: Optional[str] = Field(None, alias="actorUserId")
    actor_display_name: Optional[str] = Field(None, alias="actorDisplayName")
    billing_user_id: Optional[str] = Field(None, alias="billingUserId")
    billing_display_name: Optional[str] = Field(None, alias="billingDisplayName")
    model: str
    outcome: str
    request_payload: Optional[dict] = Field(None, alias="requestPayload")
    response_payload: Optional[dict] = Field(None, alias="responsePayload")
    error_message: Optional[str] = Field(None, alias="errorMessage")
    idempotency_key: Optional[str] = Field(None, alias="idempotencyKey")
    dedupe_key: Optional[str] = Field(None, alias="dedupeKey")
    created_at: datetime = Field(..., alias="createdAt")

    @field_serializer("created_at", when_used="json")
    def _serialize_dt(self, value: datetime) -> str | None:
        return to_cst_iso(value)


class AdminGenerationLogListOut(BaseModel):
    items: list[AdminGenerationLogOut]
    total: int
    page: int
    page_size: int = Field(..., alias="pageSize")


class AdminGenerationLogPurgeOut(BaseModel):
    deleted: int
    before: datetime

    @field_serializer("before", when_used="json")
    def _serialize_before(self, value: datetime) -> str | None:
        return to_cst_iso(value)


@router.get("/generation-logs", response_model=AdminGenerationLogListOut)
async def list_generation_call_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    job_id: Optional[int] = Query(None, alias="jobId"),
    project_id: Optional[str] = Query(None, alias="projectId"),
    actor_user_id: Optional[str] = Query(None, alias="actorUserId"),
    model: Optional[str] = None,
    category: Optional[str] = None,
    phase: Optional[str] = None,
    submit_source: Optional[str] = Query(None, alias="submitSource"),
    outcome: Optional[str] = None,
    created_from: Optional[str] = Query(None, alias="createdFrom"),
    created_to: Optional[str] = Query(None, alias="createdTo"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_permission(PERM_GENERATION_LOGS)),
):
    filters = parse_admin_generation_log_filters(
        page=page,
        page_size=page_size,
        job_id=job_id,
        project_id=project_id,
        actor_user_id=actor_user_id,
        model=model,
        category=category,
        phase=phase,
        submit_source=submit_source,
        outcome=outcome,
        created_from=_parse_filter_datetime(created_from),
        created_to=_parse_filter_datetime(created_to),
    )
    rows, total = await list_admin_generation_logs(db, filters)
    items = await build_admin_generation_log_rows(db, rows)
    return AdminGenerationLogListOut(
        items=[AdminGenerationLogOut(**item) for item in items],
        total=total,
        page=filters.page,
        pageSize=filters.page_size,
    )


@router.delete("/generation-logs", response_model=AdminGenerationLogPurgeOut)
async def purge_generation_call_logs(
    before: str = Query(..., description="删除此时间之前的日志（东八区 ISO 或日期）"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_permission(PERM_GENERATION_LOGS)),
):
    cutoff = _parse_filter_datetime(before)
    if cutoff is None:
        fail(ErrorCode.BAD_REQUEST, message="before 时间参数无效")
    deleted = await purge_generation_call_logs_before(db, cutoff)
    await db.commit()
    return AdminGenerationLogPurgeOut(deleted=deleted, before=cutoff)
