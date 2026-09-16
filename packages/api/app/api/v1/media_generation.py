"""媒体（图片/视频/音频）生成接口：提交生成任务，含算力预扣、幂等复用与同步/异步执行。"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...core.credit_amount import normalize_credit_amount
from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import AppError, fail
from ...integrations.upstream.errors import UpstreamError
from ...models.database import get_db
from ...models.job import GenerationJob
from ...models.user import User
from ...core.job_status import JobStatus
from ...services.cache import check_rate_limit
from ...services.credit_flow import commit_for_job, credit_commit_succeeded, fail_job_and_refund
from ...services.generation_jobs import mark_job_failed, mark_job_running, mark_job_succeeded, media_output_asset
from ...services.generation_call_logs import (
    flush_upstream_call_logs_from_trace,
    job_response_payload,
    log_generation_response,
    log_generation_submit,
    reset_upstream_call_log_cursor,
)
from ...services.generation_submit import submit_node_generation
from ...services.job_trace import capture_trace_after_upstream
from ...services.media_job_processor import execute_media_job
from ...services.project_access import require_project_access
from ...services.project_scope import normalize_project_id
from .text_generation import ReferenceItem

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = {"image", "video", "audio", "tool"}


class MediaGenerateRequest(BaseModel):
    """媒体生成请求体：项目/节点定位、模型、提示词、参考素材与算力报价凭证。"""

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(..., alias="projectId")
    node_id: str = Field(..., alias="nodeId")
    workflow_id: Optional[str] = Field(None, alias="workflowId")
    category: str
    prompt: str
    model: str
    references: list[ReferenceItem] = Field(default_factory=list)
    source_url: Optional[str] = Field(None, alias="sourceUrl")
    generation_options: dict[str, str] = Field(default_factory=dict, alias="generationOptions")
    visual_style_id: Optional[str] = Field(None, alias="visualStyleId")
    asset_subcategory: Optional[str] = Field(None, alias="assetSubcategory")
    asset_title: Optional[str] = Field(None, alias="assetTitle")
    quote_token: Optional[str] = Field(None, alias="quoteToken")
    expected_pricing_version: Optional[int] = Field(None, alias="expectedPricingVersion")
    submit_source: Optional[str] = Field(None, alias="submitSource")
    # 画布顶栏工具（多角度/打光/全景等）：走平台固定算力价
    canvas_tool: Optional[str] = Field(None, alias="canvasTool")
    # 画面编辑：源视频素材 ID（优先 OSS 内网读时长，避免签名 URL 直探失败）
    source_asset_id: Optional[str] = Field(None, alias="sourceAssetId")


class MediaGenerateResponse(BaseModel):
    """媒体生成响应：任务 ID、状态、结果 URL/资产 ID 与本次消耗算力。"""

    model_config = ConfigDict(populate_by_name=True)

    job_id: int = Field(..., alias="jobId")
    status: str
    result_url: Optional[str] = Field(None, alias="resultUrl")
    asset_id: Optional[str] = Field(None, alias="assetId")
    # 多张结果时含全部产物（首张与 assetId 一致，其余供前端新建节点卡片）
    output_assets: Optional[list] = Field(None, alias="outputAssets")
    message: Optional[str] = None
    request_id: Optional[str] = Field(None, alias="requestId")
    # 算力一位小数：不可用 int（1.6 会 ValidationError）
    credit_cost: float = Field(0, alias="creditCost")


def _exception_detail(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.message
    detail = getattr(exc, "detail", None)
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        message = detail.get("message")
        if message:
            return str(message)
        return str(detail)
    return str(exc)


async def _fail_job(db: AsyncSession, job: GenerationJob, exc: Exception) -> None:
    await fail_job_and_refund(db, job, _exception_detail(exc))
    await db.commit()


def _job_ref(job: GenerationJob) -> str:
    return f"任务 #{job.id}"


def _media_request_payload(body: MediaGenerateRequest, *, prompt: str) -> dict:
    return {
        "prompt": prompt,
        "category": body.category,
        "references": [r.model_dump(by_alias=True) for r in body.references],
        "sourceUrl": body.source_url,
        "generationOptions": body.generation_options,
        "visualStyleId": body.visual_style_id,
        "assetSubcategory": body.asset_subcategory,
        "assetTitle": body.asset_title,
        "submitSource": body.submit_source,
    }


async def _log_submit_failure(
    db: AsyncSession,
    *,
    body: MediaGenerateRequest,
    prompt: str,
    actor_user_id: int,
    exc: AppError,
) -> None:
    await log_generation_submit(
        db,
        job=None,
        actor_user_id=actor_user_id,
        billing_user_id=actor_user_id,
        category=body.category,
        model=body.model,
        project_id=body.project_id,
        node_id=body.node_id,
        workflow_id=body.workflow_id,
        request_payload=_media_request_payload(body, prompt=prompt),
        outcome="failure",
        submit_source=body.submit_source,
        error_message=exc.message,
    )
    await db.commit()


async def _log_http_response(
    db: AsyncSession,
    job: GenerationJob,
    *,
    outcome: str = "success",
    extra: dict | None = None,
    error_message: str | None = None,
) -> None:
    await log_generation_response(
        db,
        job=job,
        outcome=outcome,
        response_payload=job_response_payload(job, extra=extra),
        error_message=error_message,
    )
    await db.commit()


def _media_response(job: GenerationJob, **kwargs) -> MediaGenerateResponse:
    output_assets = kwargs.get("output_assets")
    if output_assets is None and isinstance(job.output_assets, list):
        output_assets = job.output_assets
    return MediaGenerateResponse(
        jobId=job.id,
        status=kwargs.get("status", job.status),
        resultUrl=kwargs.get("result_url"),
        assetId=kwargs.get("asset_id"),
        outputAssets=output_assets,
        message=kwargs.get("message"),
        requestId=job.request_id,
        creditCost=normalize_credit_amount(job.credit_cost, default=0),
    )


@router.post("/generate", response_model=MediaGenerateResponse)
async def generate_media(
    body: MediaGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """提交媒体生成任务：校验项目权限与限流，预扣算力后同步或异步执行生成。"""
    settings = get_settings()
    prompt = body.prompt.strip()
    if not prompt:
        fail(ErrorCode.CONTENT_REQUIRED, message="请填写生成内容")
    if body.category not in ALLOWED_CATEGORIES:
        fail(ErrorCode.BAD_REQUEST, message="无效的生成类别")
    try:
        normalize_project_id(body.project_id)
    except ValueError:
        fail(ErrorCode.INVALID_PROJECT_ID)

    await require_project_access(db, current_user, body.project_id)
    from ...services.storage_quota import assert_storage_quota_for_project

    # 画面编辑（主体修改/替换）：强制源片 ≤N 秒，超限须先剪辑
    tool_for_duration = (body.canvas_tool or "").strip()
    if tool_for_duration:
        from ...services.video_frame_edit import (
            UPSTREAM_FRAME_EDIT_TOOLS,
            assert_frame_edit_source_duration,
        )

        if tool_for_duration in UPSTREAM_FRAME_EDIT_TOOLS:
            await assert_frame_edit_source_duration(
                db,
                project_id=body.project_id,
                video_asset_id=body.source_asset_id,
                source_url=body.source_url,
            )

    # 生成产物会写入 OSS；已超配额时在预扣算力前拦截
    await assert_storage_quota_for_project(db, body.project_id, 1)
    allowed = await check_rate_limit(
        f"generate:media:{current_user.id}",
        limit=60,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    from ...services.model_catalog_cache import get_cached_model_by_name
    from ...services.runtime_catalog_refresh import ensure_runtime_catalog_fresh

    await ensure_runtime_catalog_fresh(db)

    # 后台配置为权威：画布/分镜功能按后台配置解析主/副模型，覆盖前端传入模型
    tool_id = (body.canvas_tool or "").strip()
    effective_model_name = body.model
    resolved_secondary: Optional[str] = None
    catalog_model = None
    if tool_id:
        from ...services.canvas_tool_models import (
            CANVAS_TOOL_MODEL_IDS,
            media_request_has_image_source,
            resolve_canvas_tool_generation_model,
            t2i_counterpart_model_name,
        )

        if tool_id in CANVAS_TOOL_MODEL_IDS:
            resolution = await resolve_canvas_tool_generation_model(
                db, tool_id, requested_model=body.model
            )
            if resolution is not None:
                catalog_model = resolution.catalog_model
                effective_model_name = resolution.model_name
                resolved_secondary = resolution.secondary_name
                # 无参考图时后台图生图主模型会打空 edit：改走对应文生图
                if not media_request_has_image_source(body.source_url, list(body.references)):
                    t2i_name = t2i_counterpart_model_name(effective_model_name)
                    if t2i_name:
                        t2i_model = await get_cached_model_by_name(
                            db, t2i_name, category=body.category, available_only=False
                        )
                        if t2i_model:
                            catalog_model = t2i_model
                            effective_model_name = t2i_name
                    if resolved_secondary:
                        sec_t2i = t2i_counterpart_model_name(resolved_secondary)
                        if sec_t2i:
                            sec_model = await get_cached_model_by_name(
                                db, sec_t2i, category=body.category, available_only=False
                            )
                            if sec_model:
                                resolved_secondary = sec_t2i

    if catalog_model is None:
        # 后台模型开关仅用于隐藏用户在前端可选的模型；模型本身始终可用（便于副模型兜底），
        # 故此处按 available_only=False 解析，只要模型已接入即可用于生成。
        catalog_model = await get_cached_model_by_name(
            db,
            body.model,
            category=body.category,
            available_only=False,
        )
    if not catalog_model:
        fail(ErrorCode.MODEL_UNAVAILABLE, content={"model": body.model})

    try:
        job, _quote, is_duplicate = await submit_node_generation(
            db,
            user=current_user,
            catalog_model=catalog_model,
            project_id=body.project_id,
            node_id=body.node_id,
            category=body.category,
            model_name=effective_model_name,
            workflow_id=body.workflow_id,
            input_params={
                "prompt": prompt,
                "category": body.category,
                "references": [r.model_dump(by_alias=True) for r in body.references],
                "sourceUrl": body.source_url,
                "generationOptions": body.generation_options,
                "visualStyleId": body.visual_style_id,
                "assetSubcategory": body.asset_subcategory,
                "assetTitle": body.asset_title,
                **({"canvasTool": tool_id} if tool_id else {}),
                # 后台配置的副模型（主模型上游失败时自动切换的兜底模型）
                **(
                    {"canvasToolSecondaryModel": resolved_secondary}
                    if resolved_secondary
                    else {}
                ),
            },
            generation_options=body.generation_options,
            idempotency_key=idempotency_key,
            quote_token=body.quote_token,
            expected_pricing_version=body.expected_pricing_version,
            submit_source=body.submit_source,
        )
        await db.commit()
    except AppError as exc:
        await db.rollback()
        try:
            await _log_submit_failure(
                db,
                body=body,
                prompt=prompt,
                actor_user_id=int(current_user.id),
                exc=exc,
            )
        except Exception:
            await db.rollback()
        raise

    job_id = job.id

    if is_duplicate:
        if job.status == JobStatus.SUCCEEDED.value:
            asset_id = job.asset_id
            result_url = None
            if isinstance(job.output_assets, list) and job.output_assets:
                first = job.output_assets[0]
                if isinstance(first, dict):
                    result_url = first.get("url")
                    asset_id = asset_id or first.get("id") or first.get("assetId")
            await _log_http_response(
                db,
                job,
                extra={"message": "任务已完成（幂等复用）"},
            )
            return _media_response(
                job,
                status="succeeded",
                result_url=result_url,
                asset_id=str(asset_id) if asset_id else None,
                message="任务已完成（幂等复用）",
            )
        if job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
            await _log_http_response(
                db,
                job,
                extra={"message": "任务已存在（幂等复用）"},
            )
            return _media_response(job, status=job.status, message="任务已存在（幂等复用）")

    if job.status == JobStatus.AWAITING_APPROVAL.value:
        await _log_http_response(
            db,
            job,
            extra={"message": "已提交审批，等待项目创建者确认"},
        )
        return _media_response(
            job,
            status=JobStatus.AWAITING_APPROVAL.value,
            message="已提交审批，等待项目创建者确认",
        )

    try:
        if settings.media_async_enabled:
            return _media_response(job, status="pending", message="任务已入队，正在后台生成")

        await mark_job_running(db, job)
        await db.commit()

        reset_upstream_call_log_cursor()
        try:
            outcome = await execute_media_job(db, job, catalog_model)
        except AppError:
            raise
        except Exception as exc:
            fail(ErrorCode.GENERATION_FAILED, message=str(exc), http_status=502)

        output = [media_output_asset(outcome.record, outcome.asset_category)]
        for extra in outcome.extra_records:
            output.append(media_output_asset(extra, outcome.asset_category))
        capture_trace_after_upstream(job, model_id=outcome.inference)
        await flush_upstream_call_logs_from_trace(db, job, model_id=outcome.inference)
        await commit_for_job(job, db)
        if not credit_commit_succeeded(job):
            fail(ErrorCode.GENERATION_FAILED, message="算力结算失败，请联系管理员")
        await mark_job_succeeded(db, job, output)
        await db.commit()

        return _media_response(
            job,
            status="succeeded",
            result_url=outcome.record["fileUrl"],
            asset_id=outcome.record["id"],
            output_assets=output,
            message=outcome.message,
        )
    except AppError as exc:
        await _fail_job(db, job, exc)
        if str(job.id) not in exc.message:
            fail(
                exc.code,
                message=f"{exc.message}（{_job_ref(job)}）",
                content=exc.content,
                http_status=exc.http_status,
            )
        raise
    except UpstreamError as exc:
        await _fail_job(db, job, exc)
        detail = f"{exc.message}（{_job_ref(job)}）"
        fail(ErrorCode.UPSTREAM_ERROR, message=detail)
    except Exception as exc:
        await _fail_job(db, job, exc)
        detail = f"{exc}（{_job_ref(job)}）"
        fail(ErrorCode.GENERATION_FAILED, message=detail, http_status=502)
