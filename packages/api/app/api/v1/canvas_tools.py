"""画布本地工具扣费：不经上游模型，仅预扣并立即结算固定算力。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.credit_amount import normalize_credit_amount
from ...core.error_codes import ErrorCode
from ...core.errors import AppError, fail
from ...core.job_status import JobStatus
from ...models.database import get_db
from ...models.user import User
from ...services.cache import check_rate_limit
from ...services.credit_flow import commit_for_job, credit_commit_succeeded
from ...services.generation_call_logs import job_response_payload, log_generation_response
from ...services.generation_jobs import mark_job_running, mark_job_succeeded
from ...services.generation_submit import submit_node_generation
from ...services.project_access import require_project_access
from ...services.project_scope import normalize_project_id

router = APIRouter()
logger = logging.getLogger(__name__)

# 仅允许本地执行的画布工具（不走上游 POST）
# 本地处理、不上游：宫格切分 / 抠图（浏览器去背景）/ 智能抠像；主体消除改走视频上游
_LOCAL_CANVAS_TOOLS = frozenset({"grid_split", "cutout", "video_smart_matting"})


class LocalCanvasToolConsumeRequest(BaseModel):
    """本地画布工具扣费请求。"""

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(..., alias="projectId")
    node_id: str = Field(..., alias="nodeId")
    workflow_id: Optional[str] = Field(None, alias="workflowId")
    model: str
    canvas_tool: str = Field(..., alias="canvasTool")
    quote_token: Optional[str] = Field(None, alias="quoteToken")
    expected_pricing_version: Optional[int] = Field(None, alias="expectedPricingVersion")
    rows: Optional[int] = None
    cols: Optional[int] = None
    submit_source: Optional[str] = Field(None, alias="submitSource")
    # 视频本地工具（智能抠像）计费秒数：写入 generationOptions / input_params
    input_video_seconds: Optional[int] = Field(None, alias="inputVideoSeconds")
    output_video_seconds: Optional[int] = Field(None, alias="outputVideoSeconds")
    duration_sec: Optional[int] = Field(None, alias="durationSec")


class LocalCanvasToolConsumeResponse(BaseModel):
    """本地画布工具扣费响应。"""

    model_config = ConfigDict(populate_by_name=True)

    job_id: int = Field(..., alias="jobId")
    status: str
    message: Optional[str] = None
    request_id: Optional[str] = Field(None, alias="requestId")
    # 算力一位小数：不可用 int
    credit_cost: float = Field(0, alias="creditCost")


class CanvasToolModelItem(BaseModel):
    """单个画布/分镜功能的主副模型（前端用于报价与提交时对齐权威模型）。"""

    model_config = ConfigDict(populate_by_name=True)

    tool_id: str = Field(..., alias="toolId")
    category: str = "image"
    primary: str = ""
    secondary: str = ""


class CanvasToolModelsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    version: int
    tools: dict[str, CanvasToolModelItem] = Field(default_factory=dict)


@router.get("/models", response_model=CanvasToolModelsResponse)
async def get_canvas_tool_models_public(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """前端读取各画布/分镜功能后台配置的主副模型（后台配置为权威）。"""
    from ...services.canvas_tool_models import canvas_tool_models_public_items, get_canvas_tool_models

    cfg = await get_canvas_tool_models(db)
    tools: dict[str, CanvasToolModelItem] = {}
    for row in canvas_tool_models_public_items(cfg):
        tools[row["toolId"]] = CanvasToolModelItem(
            toolId=row["toolId"],
            category=str(row.get("category") or "image"),
            primary=str(row.get("primary") or ""),
            secondary=str(row.get("secondary") or ""),
        )
    return CanvasToolModelsResponse(version=int(cfg["version"]), tools=tools)


@router.post("/local-consume", response_model=LocalCanvasToolConsumeResponse)
async def consume_local_canvas_tool(
    body: LocalCanvasToolConsumeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """扣画布工具固定算力并立即成功结单（宫格切分等本地处理，不上游）。"""
    tool_id = (body.canvas_tool or "").strip()
    if tool_id not in _LOCAL_CANVAS_TOOLS:
        fail(ErrorCode.BAD_REQUEST, message=f"该工具不支持本地扣费：{tool_id}")

    try:
        normalize_project_id(body.project_id)
    except ValueError:
        fail(ErrorCode.INVALID_PROJECT_ID)

    await require_project_access(db, current_user, body.project_id)

    allowed = await check_rate_limit(
        f"canvas-tool:local:{current_user.id}",
        limit=60,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    from ...services.model_catalog_cache import get_cached_model_by_name
    from ...services.runtime_catalog_refresh import ensure_runtime_catalog_fresh

    await ensure_runtime_catalog_fresh(db)

    # 按工具目录类别解析主模型（图片/视频/音频均可）；模型名供固定算力与模型专用算力匹配。
    # 后台模型开关仅隐藏前端可选模型，模型始终可用，故 available_only=False。
    from ...services.canvas_tool_models import canvas_tool_model_category

    tool_category = canvas_tool_model_category(tool_id) or "image"
    catalog_model = await get_cached_model_by_name(
        db,
        body.model,
        category=tool_category,
        available_only=False,
    )
    if not catalog_model and tool_category != "image":
        # 兼容：历史配置仍可能传图片模型名
        catalog_model = await get_cached_model_by_name(
            db,
            body.model,
            category="image",
            available_only=False,
        )
    if not catalog_model:
        catalog_model = await get_cached_model_by_name(
            db,
            body.model,
            category="tool",
            available_only=False,
        )
    if not catalog_model:
        fail(ErrorCode.MODEL_UNAVAILABLE, content={"model": body.model})

    rows = body.rows if body.rows is not None else None
    cols = body.cols if body.cols is not None else None
    if rows is not None and (rows < 1 or rows > 5):
        fail(ErrorCode.BAD_REQUEST, message="行数须在 1～5")
    if cols is not None and (cols < 1 or cols > 5):
        fail(ErrorCode.BAD_REQUEST, message="列数须在 1～5")

    # 视频本地工具：把时长写入 generationOptions，与报价 quoteToken 对齐
    tool_generation_options: dict[str, str] = {}
    if body.input_video_seconds is not None:
        tool_generation_options["inputVideoSeconds"] = str(int(body.input_video_seconds))
    if body.output_video_seconds is not None:
        tool_generation_options["outputVideoSeconds"] = str(int(body.output_video_seconds))
    bill_duration = body.duration_sec
    if bill_duration is None and body.input_video_seconds is not None:
        bill_duration = body.input_video_seconds
    if bill_duration is not None and "inputVideoSeconds" not in tool_generation_options:
        tool_generation_options["inputVideoSeconds"] = str(int(bill_duration))
    if bill_duration is not None and "outputVideoSeconds" not in tool_generation_options:
        tool_generation_options["outputVideoSeconds"] = str(int(bill_duration))

    job_category = tool_category if tool_category in ("image", "video", "audio", "tool") else "image"
    try:
        job, _quote, is_duplicate = await submit_node_generation(
            db,
            user=current_user,
            catalog_model=catalog_model,
            project_id=body.project_id,
            node_id=body.node_id,
            category=job_category,
            model_name=body.model,
            workflow_id=body.workflow_id,
            input_params={
                "prompt": f"本地工具:{tool_id}",
                "category": job_category,
                "canvasTool": tool_id,
                "localTool": True,
                **({"rows": rows} if rows is not None else {}),
                **({"cols": cols} if cols is not None else {}),
                **({"durationSec": bill_duration} if bill_duration is not None else {}),
                **(
                    {"generationOptions": tool_generation_options}
                    if tool_generation_options
                    else {}
                ),
            },
            generation_options=tool_generation_options,
            idempotency_key=idempotency_key,
            quote_token=body.quote_token,
            expected_pricing_version=body.expected_pricing_version,
            submit_source=body.submit_source or "manual",
        )

        if job.status == JobStatus.AWAITING_APPROVAL.value:
            await db.commit()
            return LocalCanvasToolConsumeResponse(
                jobId=job.id,
                status=JobStatus.AWAITING_APPROVAL.value,
                message="已提交审批，等待项目创建者确认",
                requestId=job.request_id,
                creditCost=normalize_credit_amount(job.credit_cost, default=0),
            )

        # 同一事务内立即结单，避免 Worker 认领 pending
        if not is_duplicate or job.status not in (
            JobStatus.SUCCEEDED.value,
            JobStatus.AWAITING_APPROVAL.value,
        ):
            await mark_job_running(db, job)
            await commit_for_job(job, db)
            if not credit_commit_succeeded(job):
                fail(ErrorCode.INTERNAL_ERROR, message="算力结算失败，请稍后重试")
            await mark_job_succeeded(
                db,
                job,
                [
                    {
                        "kind": "local_canvas_tool",
                        "canvasTool": tool_id,
                        "rows": rows,
                        "cols": cols,
                    }
                ],
            )

        await log_generation_response(
            db,
            job=job,
            outcome="success",
            response_payload=job_response_payload(
                job,
                extra={"message": "本地工具已扣费", "localTool": tool_id},
            ),
        )
        await db.commit()
    except AppError:
        await db.rollback()
        raise
    except Exception:
        logger.exception("local canvas tool consume failed")
        await db.rollback()
        fail(ErrorCode.INTERNAL_ERROR, message="本地工具扣费失败")

    return LocalCanvasToolConsumeResponse(
        jobId=job.id,
        status="succeeded",
        message="已扣除算力",
        requestId=job.request_id,
        creditCost=normalize_credit_amount(job.credit_cost, default=0),
    )
