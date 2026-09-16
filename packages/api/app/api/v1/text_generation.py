"""文本生成接口：调用 LLM 生成提示词/文案，支持多模态参考，含算力预扣与幂等复用。"""

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import get_settings
from ...core.credit_amount import normalize_credit_amount
from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import AppError, fail
from ...core.job_status import JobStatus
from ...services.cache import check_rate_limit
from ...services.project_access import require_project_access
from ...services.project_scope import project_storage_folder
from ...integrations.llm.chat import (
    build_vision_user_content,
    supports_vision,
)
from ...models.database import get_db
from ...models.job import GenerationJob, Model
from ...models.user import User
from ...services.credit_flow import commit_for_job, credit_commit_succeeded, fail_job_and_refund
from ...services.generation_jobs import (
    mark_job_failed,
    mark_job_running,
    mark_job_succeeded,
    text_output_asset,
)
from ...services.generation_call_logs import (
    job_response_payload,
    log_generation_response,
    log_generation_submit,
)
from ...services.generation_submit import submit_node_generation
from ...services.job_trace import capture_trace_after_upstream
from ...services.prompt_config import get_tool
from ...services.prompt_render import (
    build_text_gen_user_content,
    get_text_gen_system_prompt,
)
from ...services.text_generation_limits import with_hidden_output_limit
from ...services.reference_image import resolve_reference_for_ark, resolve_reference_url
from ...services.reference_video import resolve_video_reference_for_multimodal

router = APIRouter()
logger = logging.getLogger(__name__)

DEFAULT_TEXT_SYSTEM = (
    "你是聚梦画布中的文本助手。根据用户的提示词与参考素材，输出可直接用于 AI 绘画/视频生成的精炼中文提示词。"
    "只输出结果正文，不要解释。"
)


class ReferenceItem(BaseModel):
    """生成参考素材项：来源节点、类型（文本/图片/视频）及其内容或 URL。"""

    model_config = ConfigDict(populate_by_name=True)

    node_id: str = Field(..., alias="nodeId")
    type: str
    label: Optional[str] = None
    content: Optional[str] = None
    url: Optional[str] = None
    # 参考视频时长（秒）：供 MiniMax-H3 等 inputVideoSeconds 计费与报价凭证对齐
    duration_sec: Optional[float] = Field(None, alias="durationSec")


class TextGenerateRequest(BaseModel):
    """文本生成请求体：项目/节点定位、模型、输入内容、参考素材与算力报价凭证。"""

    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(..., alias="projectId")
    node_id: str = Field(..., alias="nodeId")
    workflow_id: Optional[str] = Field(None, alias="workflowId")
    content: str
    model: str
    text_prompt_kind: Optional[str] = Field(None, alias="textPromptKind")
    references: list[ReferenceItem] = Field(default_factory=list)
    quote_token: Optional[str] = Field(None, alias="quoteToken")
    expected_pricing_version: Optional[int] = Field(None, alias="expectedPricingVersion")
    submit_source: Optional[str] = Field(None, alias="submitSource")
    # 分镜表等固定算力工具
    canvas_tool: Optional[str] = Field(None, alias="canvasTool")


class TextGenerateResponse(BaseModel):
    """文本生成响应：任务 ID、状态、生成文本内容与本次消耗算力。"""

    model_config = ConfigDict(populate_by_name=True)

    job_id: int = Field(..., alias="jobId")
    status: str
    content: str
    model: str
    message: Optional[str] = None
    request_id: Optional[str] = Field(None, alias="requestId")
    # 算力一位小数：不可用 int
    credit_cost: float = Field(0, alias="creditCost")


def _reference_blocks(references: list[ReferenceItem]) -> list[str]:
    blocks: list[str] = []
    for ref in references:
        label = ref.label or ref.node_id
        if ref.type == "text" and ref.content:
            blocks.append(f"[参考文本「{label}」]\n{ref.content}")
        elif ref.type == "image":
            blocks.append(f"[参考图片「{label}」] 见附图")
        elif ref.type == "video":
            blocks.append(f"[参考视频「{label}」] 见附视频（或关键帧附图）")
        elif ref.url:
            blocks.append(f"[参考{ref.type}「{label}」] {ref.url}")
    return blocks


def _resolve_image_reference_urls(
    references: list[ReferenceItem],
    *,
    project_id: str,
    storage_folder: str | None = None,
) -> list[str]:
    urls: list[str] = []
    for ref in references:
        if ref.type != "image" or not ref.url:
            continue
        try:
            urls.append(
                resolve_reference_url(
                    ref.url,
                    project_id=project_id,
                    storage_folder=storage_folder,
                )
            )
        except ValueError as exc:
            logger.warning("Skip image reference %s: %s", ref.label or ref.node_id, exc)
    return urls


def _resolve_video_references(
    references: list[ReferenceItem],
    *,
    project_id: str,
    storage_folder: str | None = None,
) -> tuple[list[str], list[str]]:
    """Returns (video_urls, frame_image_urls for vision fallback)."""
    video_urls: list[str] = []
    frame_images: list[str] = []
    for ref in references:
        if ref.type != "video" or not ref.url:
            continue
        try:
            video_url, frame_uri = resolve_video_reference_for_multimodal(
                ref.url, project_id=project_id, storage_folder=storage_folder
            )
            if video_url:
                video_urls.append(video_url)
            if frame_uri:
                frame_images.append(frame_uri)
        except ValueError as exc:
            logger.warning("Skip video reference %s: %s", ref.label or ref.node_id, exc)
    return video_urls, frame_images


def _attach_multimodal(
    system: str,
    user_text: str,
    *,
    image_urls: list[str],
    video_urls: list[str],
) -> tuple[str, str | list[dict]]:
    if image_urls or video_urls:
        return system, build_vision_user_content(
            user_text, image_urls=image_urls, video_urls=video_urls
        )
    return system, user_text


def _build_user_prompt(prompt: str, references: list[ReferenceItem]) -> str:
    """Shared reference wrapper for text and media generation."""
    blocks = _reference_blocks(references)
    if not blocks:
        return prompt
    return "\n\n".join(blocks) + f"\n\n请根据以上参考完成：\n{prompt}"


def _build_media_user_prompt(prompt: str, references: list[ReferenceItem]) -> str:
    """Media upstream APIs take image/video via URLs; only inline text references in prompt."""
    text_refs = [r for r in references if r.type == "text" and (r.content or "").strip()]
    return _build_user_prompt(prompt, text_refs)


def media_reference_urls(references: list[ReferenceItem]) -> list[str]:
    """提取 image/video URL（兼容旧逻辑）；新代码优先用 media_reference_urls_typed。"""
    urls: list[str] = []
    for ref in references:
        if ref.type not in ("image", "video"):
            continue
        url = str(ref.url or "").strip()
        if url:
            urls.append(url)
    return urls


def media_reference_urls_typed(
    references: list[ReferenceItem],
) -> tuple[list[str], list[str], list[str]]:
    """按参考类型拆分 URL，避免扁平列表后只能靠扩展名猜类型。"""
    images: list[str] = []
    videos: list[str] = []
    audios: list[str] = []
    for ref in references:
        url = str(ref.url or "").strip()
        if not url:
            continue
        if ref.type == "image":
            images.append(url)
        elif ref.type == "video":
            videos.append(url)
        elif ref.type == "audio":
            audios.append(url)
    return images, videos, audios


def media_file_urls(references: list[ReferenceItem]) -> list[str]:
    """文档参考 URL（PDF/Word 等），供万相 3.0 media.type=file。"""
    urls: list[str] = []
    for ref in references:
        if ref.type != "file":
            continue
        url = str(ref.url or "").strip()
        if url:
            urls.append(url)
    return urls


def media_link_urls(references: list[ReferenceItem]) -> list[str]:
    """网页链接参考，供万相 3.0 media.type=link。"""
    urls: list[str] = []
    for ref in references:
        if ref.type != "link":
            continue
        url = str(ref.url or "").strip()
        if url:
            urls.append(url)
    return urls


def _resolve_text_gen_tool(kind: str | None) -> dict | None:
    normalized = (kind or "").strip()
    if not normalized:
        return None
    tool = get_tool(normalized)
    if not isinstance(tool, dict) or tool.get("kind") != "text_gen":
        return None
    if normalized == "text_subject":
        merged = dict(tool)
        role_tool = get_tool("text_subject_role")
        if isinstance(role_tool, dict):
            role_rule = str(role_tool.get("roleRule") or role_tool.get("role_rule") or "").strip()
            if role_rule:
                merged["roleRule"] = role_rule
        return merged
    return tool


def _build_text_messages(
    prompt: str,
    references: list[ReferenceItem],
    text_prompt_kind: str | None,
    *,
    project_id: str,
    storage_folder: str | None = None,
    canvas_tool: str | None = None,
) -> tuple[str, str | list[dict]]:
    tool_cfg = _resolve_text_gen_tool(text_prompt_kind)
    image_urls = _resolve_image_reference_urls(
        references, project_id=project_id, storage_folder=storage_folder
    )
    video_urls, video_frame_images = _resolve_video_references(
        references, project_id=project_id, storage_folder=storage_folder
    )
    image_urls = image_urls + video_frame_images

    ref_blocks = _reference_blocks(references)
    if ref_blocks:
        if image_urls and video_urls:
            suffix = "请根据以上参考（含附图与附视频）完成下列任务。"
        elif image_urls:
            suffix = "请根据以上参考（含附图）完成下列任务。"
        elif video_urls:
            suffix = "请根据以上参考（含附视频）完成下列任务。"
        else:
            suffix = "请根据以上参考完成下列任务。"
        ref_blocks = ref_blocks + [suffix]

    if tool_cfg:
        system = get_text_gen_system_prompt(tool_cfg, DEFAULT_TEXT_SYSTEM)
        user_text = build_text_gen_user_content(
            tool_cfg,
            {"content": prompt, "referenceBlocks": ref_blocks},
        )
    elif ref_blocks:
        system = DEFAULT_TEXT_SYSTEM
        user_text = "\n\n".join(ref_blocks)
        if prompt:
            user_text += f"\n\n请根据以上参考完成：\n{prompt}"
    else:
        system = DEFAULT_TEXT_SYSTEM
        if video_urls and not image_urls:
            user_text = prompt or (
                "请根据附视频生成可用于 AI 绘画/视频生成的精炼中文提示词，只输出正文。"
            )
        else:
            user_text = prompt or "请根据附图生成可用于 AI 绘画的精炼中文提示词，只输出正文。"
        # 文本节点卡片：系统提示中隐性约束生成不超过 5000 字
        system = with_hidden_output_limit(
            system, text_prompt_kind, canvas_tool=canvas_tool
        )
        return _attach_multimodal(
            system, user_text, image_urls=image_urls, video_urls=video_urls
        )

    system = with_hidden_output_limit(
        system, text_prompt_kind, canvas_tool=canvas_tool
    )
    return _attach_multimodal(
        system, user_text, image_urls=image_urls, video_urls=video_urls
    )


def _text_request_payload(body: TextGenerateRequest, *, prompt: str) -> dict:
    return {
        "content": prompt,
        "textPromptKind": body.text_prompt_kind,
        "references": [r.model_dump(by_alias=True) for r in body.references],
        "submitSource": body.submit_source,
    }


async def _log_text_submit_failure(
    db: AsyncSession,
    *,
    body: TextGenerateRequest,
    prompt: str,
    actor_user_id: int,
    exc: AppError,
) -> None:
    await log_generation_submit(
        db,
        job=None,
        actor_user_id=actor_user_id,
        billing_user_id=actor_user_id,
        category="text",
        model=body.model,
        project_id=body.project_id,
        node_id=body.node_id,
        workflow_id=body.workflow_id,
        request_payload=_text_request_payload(body, prompt=prompt),
        outcome="failure",
        submit_source=body.submit_source,
        error_message=exc.message,
    )
    await db.commit()


async def _log_text_http_response(
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


async def _fail_job(db: AsyncSession, job: GenerationJob, exc: Exception) -> None:
    if isinstance(exc, AppError):
        detail = exc.message
    elif isinstance(exc, Exception):
        detail = str(exc)
    else:
        detail = "生成失败"
    await fail_job_and_refund(db, job, detail)
    await db.commit()


@router.post("/generate", response_model=TextGenerateResponse)
async def generate_text(
    body: TextGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """提交文本生成任务：校验权限与多模态模型能力，预扣算力后同步或异步调用 LLM。"""
    project = await require_project_access(db, current_user, body.project_id)
    storage_folder = project_storage_folder(project)
    allowed = await check_rate_limit(
        f"generate:text:{current_user.id}",
        limit=60,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    from ...services.prompt_platform_runtime import ensure_prompt_platform_fresh
    from ...services.runtime_catalog_refresh import ensure_runtime_catalog_fresh

    await ensure_runtime_catalog_fresh(db)
    await ensure_prompt_platform_fresh()

    prompt = body.content.strip()
    image_urls = _resolve_image_reference_urls(
        body.references, project_id=body.project_id, storage_folder=storage_folder
    )
    video_urls, video_frame_images = _resolve_video_references(
        body.references, project_id=body.project_id, storage_folder=storage_folder
    )
    image_urls = image_urls + video_frame_images
    has_video_ref = any(r.type == "video" and r.url for r in body.references)

    if not prompt and not image_urls and not video_urls:
        if has_video_ref:
            fail(ErrorCode.VIDEO_REF_INACCESSIBLE)
        fail(ErrorCode.CONTENT_REQUIRED)

    from ...services.model_catalog_cache import get_cached_model_by_name
    from ...services.credential_service import resolve_model_configured_status
    from ...services.runtime_catalog_refresh import ensure_runtime_catalog_fresh

    await ensure_runtime_catalog_fresh(db)

    # 后台配置为权威：分镜/文本功能按后台配置解析主/副模型，覆盖前端传入模型
    tool_id = (body.canvas_tool or "").strip()
    effective_model_name = body.model
    resolved_secondary: Optional[str] = None
    catalog_model: Optional[Model] = None
    if tool_id:
        from ...services.canvas_tool_models import (
            CANVAS_TOOL_MODEL_IDS,
            resolve_canvas_tool_generation_model,
        )

        if tool_id in CANVAS_TOOL_MODEL_IDS:
            resolution = await resolve_canvas_tool_generation_model(
                db, tool_id, requested_model=body.model
            )
            if resolution is not None:
                catalog_model = resolution.catalog_model
                effective_model_name = resolution.model_name
                resolved_secondary = resolution.secondary_name

    if (image_urls or video_urls) and not supports_vision(effective_model_name):
        fail(ErrorCode.MULTIMODAL_MODEL_REQUIRED)

    if catalog_model is None:
        # 后台模型开关仅隐藏前端可选模型；模型始终可用（便于副模型兜底），故 available_only=False。
        catalog_model = await get_cached_model_by_name(
            db,
            body.model,
            category="text",
            available_only=False,
        )
    if not catalog_model:
        fail(ErrorCode.MODEL_UNAVAILABLE, content={"model": body.model})

    if not await resolve_model_configured_status(db, effective_model_name):
        fail(
            ErrorCode.NOT_CONFIGURED,
            message=f"模型 {effective_model_name} 未配置（请在管理后台配置供应商密钥）",
        )

    try:
        job, _quote, _is_duplicate = await submit_node_generation(
            db,
            user=current_user,
            catalog_model=catalog_model,
            project_id=body.project_id,
            node_id=body.node_id,
            category="text",
            model_name=effective_model_name,
            workflow_id=body.workflow_id,
            input_params={
                "content": prompt,
                **({"textPromptKind": body.text_prompt_kind} if body.text_prompt_kind else {}),
                "references": [r.model_dump(by_alias=True) for r in body.references],
                **({"canvasTool": tool_id} if tool_id else {}),
                # 后台配置的副模型（主模型上游失败时自动切换的兜底模型）
                **(
                    {"canvasToolSecondaryModel": resolved_secondary}
                    if resolved_secondary
                    else {}
                ),
            },
            generation_options=None,
            idempotency_key=idempotency_key,
            quote_token=body.quote_token,
            expected_pricing_version=body.expected_pricing_version,
            submit_source=body.submit_source,
        )
        await db.commit()
    except AppError as exc:
        await db.rollback()
        try:
            await _log_text_submit_failure(
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

    if _is_duplicate:
        await _log_text_http_response(db, job, extra={"message": "幂等复用"})
        if job.status == JobStatus.SUCCEEDED.value:
            return TextGenerateResponse(
                job_id=job_id,
                status="succeeded",
                content=job.result_text or "",
                model=body.model,
                message="任务已完成（幂等复用）",
                request_id=job.request_id,
                credit_cost=normalize_credit_amount(job.credit_cost, default=0),
            )
        if job.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
            return TextGenerateResponse(
                job_id=job_id,
                status=job.status,
                content="",
                model=body.model,
                message="任务已存在（幂等复用）",
                request_id=job.request_id,
                credit_cost=normalize_credit_amount(job.credit_cost, default=0),
            )

    if job.status == JobStatus.AWAITING_APPROVAL.value:
        await _log_text_http_response(
            db,
            job,
            extra={"message": "已提交审批，等待项目创建者确认"},
        )
        return TextGenerateResponse(
            job_id=job_id,
            status=JobStatus.AWAITING_APPROVAL.value,
            content="",
            model=body.model,
            message="已提交审批，等待项目创建者确认",
            request_id=job.request_id,
            credit_cost=normalize_credit_amount(job.credit_cost, default=0),
        )

    settings = get_settings()
    if settings.text_async_enabled:
        return TextGenerateResponse(
            job_id=job_id,
            status="pending",
            content="",
            model=body.model,
            message="任务已入队，正在后台生成",
            request_id=job.request_id,
            credit_cost=normalize_credit_amount(job.credit_cost, default=0),
        )

    try:
        await mark_job_running(db, job)
        await db.commit()

        from ...services.text_job_processor import execute_text_job_with_failover

        generated, used_model = await execute_text_job_with_failover(db, job, catalog_model)

        output = [text_output_asset(body.node_id, generated, used_model)]
        capture_trace_after_upstream(job, model_id=used_model)
        await commit_for_job(job, db)
        if not credit_commit_succeeded(job):
            fail(ErrorCode.GENERATION_FAILED, message="算力结算失败，请联系管理员")
        await mark_job_succeeded(db, job, output, result_text=generated)
        await db.commit()

        return TextGenerateResponse(
            job_id=job_id,
            status="succeeded",
            content=generated,
            model=used_model,
            request_id=job.request_id,
            credit_cost=normalize_credit_amount(job.credit_cost, default=0),
        )
    except ValueError as exc:
        await _fail_job(db, job, exc)
        fail(ErrorCode.BAD_REQUEST, message=str(exc))
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        logger.error("LLM HTTP error: %s", detail)
        await _fail_job(db, job, exc)
        fail(ErrorCode.LLM_REQUEST_FAILED, message=f"LLM 请求失败: {detail}")
    except AppError as exc:
        await _fail_job(db, job, exc)
        job_ref = f"任务 #{job.id}"
        if job_ref not in exc.message:
            fail(
                exc.code,
                message=f"{exc.message}（{job_ref}）",
                content=exc.content,
                http_status=exc.http_status,
            )
        raise
    except Exception as exc:
        await _fail_job(db, job, exc)
        fail(ErrorCode.LLM_REQUEST_FAILED, message=f"LLM 请求失败: {exc}")
