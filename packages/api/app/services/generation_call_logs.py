"""生成调用日志服务 —— 追加式记录 submit / response / upstream 各阶段调用。

每条 ``GenerationCallLog`` 对应一次 API 提交、任务终态响应或上游 HTTP 交互；
与 job trace 互补，供管理端审计与排障。
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import parse_entity_id
from ..core.credit_amount import normalize_credit_amount
from ..integrations.upstream.trace_context import peek_upstream_trace
from ..models.generation_call_log import GenerationCallLog
from ..models.job import GenerationJob
from ..models.types import json_sanitize

logger = logging.getLogger(__name__)

_upstream_logged_event_count: contextvars.ContextVar[int] = contextvars.ContextVar(
    "upstream_logged_event_count",
    default=0,
)

LANE_BY_CATEGORY = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "text": "text_llm",
    "tool": "image",
}

SUBMIT_SOURCE_MANUAL = "manual"
SUBMIT_SOURCE_DEDUPE = "dedupe"
SUBMIT_SOURCE_AUTO = "auto"

_AUTO_IDEMPOTENCY_PREFIXES = ("storyboard-", "batch-", "lighting-", "multi-angle-")


def resolve_submit_source(
    *,
    is_duplicate: bool,
    idempotency_key: str | None,
    explicit: str | None = None,
) -> str:
    """推断提交来源：去重命中 / 自动批量 / 用户手动。"""
    if is_duplicate:
        return SUBMIT_SOURCE_DEDUPE
    normalized = (explicit or "").strip().lower()
    if normalized in (SUBMIT_SOURCE_AUTO, SUBMIT_SOURCE_MANUAL, SUBMIT_SOURCE_DEDUPE):
        return normalized
    key = (idempotency_key or "").strip().lower()
    if any(key.startswith(prefix) for prefix in _AUTO_IDEMPOTENCY_PREFIXES):
        return SUBMIT_SOURCE_AUTO
    return SUBMIT_SOURCE_MANUAL


def _parse_optional_entity_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    return parse_entity_id(value)


def _job_lane(job: GenerationJob | None, category: str) -> str | None:
    if job and job.lane:
        return str(job.lane)
    return LANE_BY_CATEGORY.get(category)


def job_response_payload(job: GenerationJob, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """从 job 终态字段组装 response 阶段日志 payload。"""
    payload: dict[str, Any] = {
        "jobId": job.id,
        "status": job.status,
        "requestId": job.request_id,
        # credit_cost 现为 DECIMAL，须转 float 再入 JSON
        "creditCost": normalize_credit_amount(job.credit_cost, default=0),
        "creditStatus": job.credit_status,
        "errorMessage": job.error_message,
    }
    asset_id = job.asset_id
    if not asset_id and isinstance(job.output_assets, list):
        for item in job.output_assets:
            if isinstance(item, dict):
                asset_id = item.get("id") or item.get("assetId")
                if asset_id:
                    break
    if asset_id:
        payload["assetId"] = str(asset_id)
    if isinstance(job.output_assets, list) and job.output_assets:
        first = job.output_assets[0]
        if isinstance(first, dict):
            if first.get("url"):
                payload["resultUrl"] = first.get("url")
            if first.get("contentPreview"):
                payload["contentPreview"] = first.get("contentPreview")
            if first.get("content") and "contentPreview" not in payload:
                content = str(first.get("content") or "")
                if content:
                    payload["contentPreview"] = content[:200]
    if job.result_text:
        payload["contentPreview"] = job.result_text[:200]
    if extra:
        payload.update(extra)
    return json_sanitize(payload)


async def _response_log_exists(db: AsyncSession, job_id: int) -> bool:
    result = await db.execute(
        select(GenerationCallLog.id)
        .where(
            GenerationCallLog.job_id == job_id,
            GenerationCallLog.phase == "response",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def log_generation_submit(
    db: AsyncSession,
    *,
    job: GenerationJob | None,
    actor_user_id: int | str,
    billing_user_id: int | str | None,
    category: str,
    model: str,
    project_id: str | int,
    node_id: str,
    workflow_id: str | None,
    request_payload: dict[str, Any],
    outcome: str,
    is_duplicate: bool = False,
    submit_source: str | None = None,
    idempotency_key: str | None = None,
    dedupe_key: str | None = None,
    error_message: str | None = None,
) -> GenerationCallLog:
    """记录一次 API 提交（phase=submit），含去重键与来源。"""
    # generation_call_logs.idempotency_key 最长 128，客户端偶发超长时截断防 500
    normalized_idempotency_key = (idempotency_key or "").strip()[:128] or None
    source = resolve_submit_source(
        is_duplicate=is_duplicate,
        idempotency_key=idempotency_key,
        explicit=submit_source,
    )
    row = GenerationCallLog(
        job_id=job.id if job else None,
        phase="submit",
        submit_source=source,
        lane=_job_lane(job, category),
        category=category,
        project_id=_parse_optional_entity_id(project_id),
        node_id=node_id or None,
        workflow_id=workflow_id or None,
        actor_user_id=_parse_optional_entity_id(actor_user_id),
        billing_user_id=_parse_optional_entity_id(billing_user_id),
        model=model,
        outcome=outcome,
        request_payload=request_payload,
        error_message=error_message[:2000] if error_message else None,
        idempotency_key=normalized_idempotency_key,
        dedupe_key=dedupe_key,
    )
    db.add(row)
    await db.flush()
    return row


def _job_category(job: GenerationJob) -> str:
    """从 job 的 input_params / lane / job_type 推断媒体类目。"""
    params = job.input_params if isinstance(job.input_params, dict) else {}
    cat = params.get("category")
    if isinstance(cat, str) and cat.strip():
        return cat.strip()
    lane = (job.lane or "").strip()
    if lane == "text_llm":
        return "text"
    if lane in ("image", "video", "audio"):
        return lane
    job_type = (job.job_type or "").strip()
    if job_type.endswith("_gen"):
        return job_type[:-4]
    return "unknown"


async def log_generation_response(
    db: AsyncSession,
    *,
    job: GenerationJob,
    outcome: str,
    response_payload: dict[str, Any] | None = None,
    error_message: str | None = None,
    allow_duplicate: bool = False,
) -> GenerationCallLog | None:
    """记录任务终态响应（phase=response），默认每 job 仅一条。"""
    if not allow_duplicate and await _response_log_exists(db, job.id):
        return None
    payload = response_payload or job_response_payload(job)
    row = GenerationCallLog(
        job_id=job.id,
        phase="response",
        submit_source=None,
        lane=job.lane,
        category=_job_category(job),
        project_id=_parse_optional_entity_id(job.project_id),
        node_id=job.node_id,
        workflow_id=str(job.workflow_id) if job.workflow_id else None,
        actor_user_id=job.actor_user_id,
        billing_user_id=job.user_id,
        model=job.model or "",
        outcome=outcome,
        response_payload=payload,
        error_message=error_message[:2000] if error_message else None,
    )
    db.add(row)
    await db.flush()
    return row


async def log_job_terminal_response(
    db: AsyncSession,
    job: GenerationJob,
    *,
    success: bool,
    error_message: str | None = None,
) -> GenerationCallLog | None:
    """任务成功/失败时写 response 日志的便捷封装。"""
    return await log_generation_response(
        db,
        job=job,
        outcome="success" if success else "failure",
        error_message=error_message,
    )


def reset_upstream_call_log_cursor() -> None:
    """重置上游 trace 事件游标（每次 Worker 重试从新事件开始记日志）。"""
    _upstream_logged_event_count.set(0)


async def log_upstream_api_call(
    db: AsyncSession,
    job: GenerationJob,
    *,
    action: str,
    outcome: str = "success",
    provider: str | None = None,
    provider_task_id: str | None = None,
    provider_request_id: str | None = None,
    detail: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> GenerationCallLog:
    """追加一条上游 API 调用记录（phase=upstream，重试不去重）。"""
    category = _job_category(job)
    payload: dict[str, Any] = {
        "action": action,
        "attemptCount": int(job.attempt_count or 0),
        "provider": provider or job.provider,
        "providerTaskId": provider_task_id,
        "providerRequestId": provider_request_id,
    }
    if detail:
        payload.update(detail)

    row = GenerationCallLog(
        job_id=job.id,
        phase="upstream",
        submit_source=None,
        lane=job.lane,
        category=category,
        project_id=_parse_optional_entity_id(job.project_id),
        node_id=job.node_id,
        workflow_id=str(job.workflow_id) if job.workflow_id else None,
        actor_user_id=job.actor_user_id,
        billing_user_id=job.user_id,
        model=job.model or "",
        outcome=outcome,
        request_payload=json_sanitize(payload),
        error_message=error_message[:2000] if error_message else None,
    )
    db.add(row)
    await db.flush()
    logger.info(
        "[upstream] job_id=%s action=%s attempt=%s category=%s model=%s provider=%s task_id=%s outcome=%s",
        job.id,
        action,
        job.attempt_count or 0,
        category,
        job.model,
        provider or job.provider,
        provider_task_id,
        outcome,
    )
    return row


async def flush_upstream_call_logs_from_trace(
    db: AsyncSession,
    job: GenerationJob,
    *,
    model_id: str | None = None,
) -> list[GenerationCallLog]:
    """将 trace 中尚未落库的上游事件批量写入 call_log。"""
    snap = peek_upstream_trace()
    if snap is None:
        return []

    start = _upstream_logged_event_count.get()
    new_events = snap.events[start:]
    if not new_events:
        return []

    from .job_trace import provider_for_model

    provider = snap.provider or (provider_for_model(model_id) if model_id else None) or job.provider
    rows: list[GenerationCallLog] = []
    for evt in new_events:
        action = str(evt.get("event") or "unknown")
        detail = {k: v for k, v in evt.items() if k != "event"}
        row = await log_upstream_api_call(
            db,
            job,
            action=action,
            outcome="success",
            provider=provider,
            provider_task_id=snap.provider_task_id,
            provider_request_id=snap.provider_request_id,
            detail=detail or None,
        )
        rows.append(row)
    _upstream_logged_event_count.set(len(snap.events))
    return rows
