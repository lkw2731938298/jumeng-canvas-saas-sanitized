"""画布文本/LLM 生成任务执行（Worker 异步处理）。"""

from __future__ import annotations

import logging
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import require_entity_id
from ..core.llm_keys import is_model_configured
from ..integrations.llm.chat import chat_completion
from ..integrations.providers.stubs import ProviderNotImplementedError
from ..integrations.upstream.errors import UpstreamError
from ..models.job import GenerationJob, Model
from ..models.project import Project
from ..services.project_scope import project_storage_folder
from ..services.credit_flow import commit_for_job, credit_commit_succeeded, fail_job_and_refund
from ..services.credit_operation_lock import model_submit_lock
from ..services.generation_call_logs import (
    flush_upstream_call_logs_from_trace,
    log_upstream_api_call,
    reset_upstream_call_log_cursor,
)
from ..services.generation_jobs import mark_job_failed, mark_job_succeeded, text_output_asset
from ..services.job_trace import capture_trace_after_upstream
from ..services.model_channels import (
    DEFAULT_FAILOVER_ERROR_CODES,
    append_channel_event,
    error_code_from_exc,
)
from ..services.text_generation_limits import clip_job_text_output
from ..api.v1.text_generation import ReferenceItem, _build_text_messages

logger = logging.getLogger(__name__)


def is_canvas_text_job(job: GenerationJob) -> bool:
    """判断是否为画布节点发起的文本 LLM 任务。"""
    return bool(job.node_id) and job.job_type == "text_llm"


async def execute_text_job(
    db: AsyncSession,
    job: GenerationJob,
    catalog_model: Model,
    *,
    model_override: str | None = None,
) -> str:
    """执行文本生成：加 model_submit_lock 后调用上游 LLM，返回生成文本。"""
    from .runtime_catalog_refresh import ensure_runtime_catalog_fresh

    await ensure_runtime_catalog_fresh(db)
    from .prompt_platform_runtime import ensure_prompt_platform_fresh

    await ensure_prompt_platform_fresh()
    async with model_submit_lock(int(job.id)):
        return await _execute_text_job_locked(db, job, catalog_model, model_override=model_override)


def _text_secondary_model(job: GenerationJob, primary: str) -> str | None:
    """从 input_params 取后台配置的副模型（与主模型不同才生效）。"""
    params = job.input_params or {}
    secondary = str(
        params.get("canvasToolSecondaryModel")
        or params.get("canvas_tool_secondary_model")
        or ""
    ).strip()
    if secondary and secondary != primary:
        return secondary
    return None


async def execute_text_job_with_failover(
    db: AsyncSession,
    job: GenerationJob,
    catalog_model: Model,
) -> tuple[str, str]:
    """执行文本生成并支持主→副模型自动兜底，返回 (生成文本, 实际使用模型名)。

    主模型上游失败（可切换错误码）且配置了副模型时，自动切换副模型重试一次。
    文本生成为同步请求-响应，无 provider_task_id，切换不会造成上游双任务。
    """
    from .ai_mock_generation import build_mock_text_content, is_ai_mock_enabled, mock_delay
    from .runtime_catalog_refresh import ensure_runtime_catalog_fresh

    # 测试服无上游密钥时：直接返回可解析的分镜 JSON，不调 LLM
    if is_ai_mock_enabled():
        await mock_delay()
        model_name = job.model or catalog_model.name
        text = clip_job_text_output(job, build_mock_text_content(job))
        logger.info("ai_mock text job_id=%s kind=%s", job.id, (job.input_params or {}).get("textPromptKind"))
        await log_upstream_api_call(
            db,
            job,
            action="ai_mock",
            outcome="success",
            provider="ai_mock",
            detail={"modelId": model_name},
        )
        return text, str(model_name)

    await ensure_runtime_catalog_fresh(db)
    from .prompt_platform_runtime import ensure_prompt_platform_fresh

    await ensure_prompt_platform_fresh()

    params = job.input_params or {}
    primary = job.model or str(params.get("model") or catalog_model.name)
    candidates: list[str] = [primary]
    secondary = _text_secondary_model(job, primary)
    if secondary:
        candidates.append(secondary)

    last_exc: BaseException | None = None
    async with model_submit_lock(int(job.id)):
        for idx, model_name in enumerate(candidates):
            has_next = idx + 1 < len(candidates)
            try:
                generated = clip_job_text_output(
                    job,
                    await _execute_text_job_locked(
                        db, job, catalog_model, model_override=model_name
                    ),
                )
                if idx > 0:
                    append_channel_event(
                        job,
                        event="channel_failover_success",
                        detail={"effectiveModel": model_name},
                    )
                return generated, model_name
            except (
                httpx.HTTPStatusError,
                UpstreamError,
                ProviderNotImplementedError,
                ValueError,
            ) as exc:
                last_exc = exc
                code = error_code_from_exc(exc)
                if has_next and code in set(DEFAULT_FAILOVER_ERROR_CODES):
                    append_channel_event(
                        job,
                        event="channel_failover",
                        detail={
                            "fromModel": model_name,
                            "toModel": candidates[idx + 1],
                            "errorCode": code,
                            "errorMessage": str(exc)[:500],
                        },
                    )
                    logger.warning(
                        "Text job %s model %s failed (%s), trying %s",
                        job.id,
                        model_name,
                        code,
                        candidates[idx + 1],
                    )
                    continue
                raise

    if last_exc is not None:
        raise last_exc
    raise ValueError("文本任务无可用模型")


async def _execute_text_job_locked(
    db: AsyncSession,
    job: GenerationJob,
    catalog_model: Model,
    *,
    model_override: str | None = None,
) -> str:
    """文本生成核心：组装 messages、调用 chat_completion、返回结果。"""
    params = job.input_params or {}
    prompt = str(params.get("content") or "").strip()
    text_prompt_kind = params.get("textPromptKind") or params.get("text_prompt_kind")
    canvas_tool = params.get("canvasTool") or params.get("canvas_tool")
    project_id = str(params.get("projectId") or params.get("project_id") or job.project_id or "")
    node_id = str(job.node_id or params.get("nodeId") or params.get("node_id") or "")

    references: list[ReferenceItem] = []
    for entry in params.get("references") or []:
        if isinstance(entry, dict):
            references.append(ReferenceItem.model_validate(entry))

    model_name = (model_override or "").strip() or job.model or str(params.get("model") or catalog_model.name)
    if not is_model_configured(model_name):
        raise ValueError(f"Model '{model_name}' is not configured (configure provider credentials in admin)")

    storage_folder: str | None = None
    if project_id:
        pid = require_entity_id(project_id)
        if pid is not None:
            result = await db.execute(select(Project).filter(Project.id == pid))
            project = result.scalar_one_or_none()
            if project:
                storage_folder = project_storage_folder(project)

    system, user_content = _build_text_messages(
        prompt,
        references,
        str(text_prompt_kind) if text_prompt_kind else None,
        project_id=project_id,
        storage_folder=storage_folder,
        canvas_tool=str(canvas_tool) if canvas_tool else None,
    )

    max_out = (
        8192
        if text_prompt_kind
        in (
            "storyboard_table",
            "storyboard_from_video",
            "storyboard_from_image",
            "storyboard_overseas_localize",
        )
        else 4096
    )
    # 爆款拉片 / 图片分镜 / 一键出海等多图多模态：上游推理常超 120s，单独放宽读超时
    _kind = str(text_prompt_kind or "")
    chat_timeout_s = (
        600.0
        if _kind
        in ("storyboard_from_video", "storyboard_from_image", "storyboard_overseas_localize")
        else 180.0
        if _kind == "storyboard_table"
        else 120.0
    )
    await log_upstream_api_call(
        db,
        job,
        action="text_submit",
        outcome="pending",
        provider=job.provider,
        detail={"modelId": model_name},
    )
    generated = await chat_completion(
        model_name,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        max_tokens=max_out,
        timeout_s=chat_timeout_s,
    )
    # 文本节点卡片：生成结果隐性截断至 5000 字
    return clip_job_text_output(job, generated)


async def process_text_generation_job(db: AsyncSession, job: GenerationJob) -> None:
    """Worker 入口：执行文本任务、结算算力、标记成功或失败退款。"""
    model_name = job.model or (job.input_params or {}).get("model")
    if not model_name:
        await fail_job_and_refund(db, job, "Missing model on generation job", force=True)
        await db.commit()
        return

    from .model_catalog_cache import get_cached_model_by_name

    # 后台模型开关仅隐藏前端可选模型；模型始终可用（含副模型兜底），故 available_only=False。
    catalog_model = await get_cached_model_by_name(
        db,
        model_name,
        category="text",
        available_only=False,
    )
    if not catalog_model:
        await fail_job_and_refund(db, job, f"Model '{model_name}' is not available", force=True)
        await db.commit()
        return

    node_id = str(job.node_id or "")
    reset_upstream_call_log_cursor()
    try:
        generated, model_name = await execute_text_job_with_failover(db, job, catalog_model)
        output = [text_output_asset(node_id, generated, model_name)]
        capture_trace_after_upstream(job, model_id=model_name)
        await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
        await commit_for_job(job, db)
        if not credit_commit_succeeded(job):
            await mark_job_failed(
                db,
                job,
                "算力结算失败，请联系管理员",
                error_code="CREDIT_COMMIT_FAILED",
            )
            await db.commit()
            return
        await mark_job_succeeded(db, job, output, result_text=generated)
        await db.commit()
    except httpx.TimeoutException as exc:
        # httpx.ReadTimeout 的 str(exc) 常为空，需写明中文原因供前端展示
        capture_trace_after_upstream(job, model_id=model_name)
        await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
        detail = (
            f"LLM 响应超时（{type(exc).__name__}），多图拉片耗时较长，请稍后重试"
        )
        await log_upstream_api_call(
            db,
            job,
            action="worker_execute",
            outcome="failure",
            provider=job.provider,
            error_message=detail,
        )
        await fail_job_and_refund(db, job, detail)
        await db.commit()
    except httpx.HTTPStatusError as exc:
        capture_trace_after_upstream(job, model_id=model_name)
        await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
        detail = exc.response.text[:500] if exc.response is not None else str(exc)
        await log_upstream_api_call(
            db,
            job,
            action="worker_execute",
            outcome="failure",
            provider=job.provider,
            error_message=f"LLM 请求失败: {detail}",
        )
        await fail_job_and_refund(db, job, f"LLM 请求失败: {detail}")
        await db.commit()
    except Exception as exc:
        capture_trace_after_upstream(job, model_id=model_name)
        await flush_upstream_call_logs_from_trace(db, job, model_id=model_name)
        logger.exception("Text job %s failed", job.id)
        # 避免空 str(exc) 导致前端只显示「文本生成失败」
        raw = str(exc).strip() or type(exc).__name__
        await log_upstream_api_call(
            db,
            job,
            action="worker_execute",
            outcome="failure",
            provider=job.provider,
            error_message=raw[:2000],
        )
        await fail_job_and_refund(db, job, raw[:2000])
        await db.commit()
