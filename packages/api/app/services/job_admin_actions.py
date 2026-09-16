"""管理员对生成任务的运维干预：同步上游、强制状态、重新入队等。"""

from __future__ import annotations

from ..core.datetime_util import cst_iso, cst_iso_now, now_cst_naive
import logging
import uuid
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import require_entity_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage, new_asset_id
from ..models.job import GenerationJob, Model
from ..models.job_admin_action import GenerationJobAdminAction
from ..models.project import Project
from ..models.user import User
from ..services.asset_store import get_project_asset, insert_project_asset
from ..services.project_scope import project_storage_folder
from ..services.storage_quota import assert_storage_quota_for_project
from ..core.credit_status import CreditStatus
from ..core.job_status import JobStatus
from ..services.credit_flow import (
    commit_for_job,
    credits_enabled,
    fail_job_and_refund,
    get_user_credit_balance,
    refund_job_credits,
)
from ..services.credit_pricing import quote_generation_cost
from ..services.generation_jobs import (
    extract_asset_id,
    extract_project_id,
    mark_job_abnormal,
    mark_job_failed,
    mark_job_polling,
    mark_job_running,
    mark_job_succeeded,
    media_output_asset,
    requeue_job_for_worker,
)
from ..services.job_admin_lock import job_admin_lock
from ..services.storage_urls import normalize_browser_storage_url
from ..services.upstream_status import query_upstream_task_status
from ..services.upstream_job_resume import upstream_task_id_from_job
from ..services.worker_job_lock import is_worker_job_lock_held
logger = logging.getLogger(__name__)

# 管理员可执行的任务运维动作白名单：
#   sync_upstream       同步已有上游任务状态
#   force_succeed       强制标记成功并结算算力
#   force_fail          强制标记失败并退款
#   requeue             重新入队（保留上游标记，续轮询，不重复扣上游费）
#   regenerate_upstream 清空上游标记后重新入队（强制重新提交上游，会再次扣费）
#   reconcile_credits   算力对账
ADMIN_ACTIONS = frozenset(
    {
        "sync_upstream",
        "force_succeed",
        "force_fail",
        "requeue",
        "regenerate_upstream",
        "reconcile_credits",
    }
)

LANE_CATEGORY = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "text_llm": "text",
}


def _validate_note(note: str | None) -> str:
    text = (note or "").strip()
    if len(text) < 10:
        fail(ErrorCode.ADMIN_REASON_TOO_SHORT)
    return text[:2000]


def _requeue_count(job: GenerationJob) -> int:
    trace = job.trace_json if isinstance(job.trace_json, dict) else {}
    try:
        return max(int(trace.get("requeueCount") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _increment_requeue_count(job: GenerationJob) -> int:
    trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
    count = _requeue_count(job) + 1
    trace["requeueCount"] = count
    job.trace_json = trace
    return count


def _clear_anomaly(job: GenerationJob) -> None:
    job.anomaly_reason = None
    job.anomaly_detected_at = None
    job.anomaly_detail = None


def _append_admin_trace(job: GenerationJob, *, action: str, operator_id: int, note: str) -> None:
    trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
    interventions = trace.get("adminInterventions")
    if not isinstance(interventions, list):
        interventions = []
    ts = now_cst_naive()
    interventions.append(
        {
            "action": action,
            "at": cst_iso(ts) or cst_iso_now(),
            "by": str(operator_id),
            "note": note[:500],
        }
    )
    trace["adminInterventions"] = interventions[-20:]
    job.trace_json = trace


async def _record_admin_action(
    db: AsyncSession,
    *,
    job: GenerationJob,
    operator: User,
    action: str,
    note: str,
    payload: dict[str, Any],
    before_status: str,
    before_credit_status: str | None,
) -> GenerationJobAdminAction:
    row = GenerationJobAdminAction(
        job_id=job.id,
        operator_id=operator.id,
        action=action,
        before_status=before_status,
        after_status=job.status,
        before_credit_status=before_credit_status,
        after_credit_status=job.credit_status,
        note=note,
        payload=payload,
    )
    db.add(row)
    job.last_admin_action = action
    job.last_admin_action_at = now_cst_naive()
    job.last_admin_operator_id = operator.id
    job.last_admin_note = note
    _append_admin_trace(job, action=action, operator_id=operator.id, note=note)
    await db.flush()
    return row


async def _lock_job(db: AsyncSession, job_id: int) -> GenerationJob:
    result = await db.execute(
        select(GenerationJob).filter(GenerationJob.id == job_id).with_for_update()
    )
    job = result.scalar_one_or_none()
    if not job:
        fail(ErrorCode.JOB_NOT_FOUND)
    return job


def _upstream_task_id(job: GenerationJob) -> str | None:
    """统一从 upstream_job_resume 取上游 task_id，避免各处重复解析字段。"""
    return upstream_task_id_from_job(job)


async def _requeue_job_for_admin(
    db: AsyncSession,
    job: GenerationJob,
    operator: User,
    *,
    clear_upstream_ids: bool,
) -> dict[str, Any]:
    """管理员重新入队的公共逻辑。

    clear_upstream_ids=False（requeue）：保留上游标记，Worker 续轮询已有任务；
    clear_upstream_ids=True（regenerate_upstream）：清空上游标记，Worker 重新向上游提交。
    """
    if job.status not in (JobStatus.ABNORMAL.value, JobStatus.FAILED.value):
        fail(ErrorCode.INVALID_ADMIN_ACTION, message="仅异常或失败任务可重新入队")

    user = (
        await db.execute(select(User).filter(User.id == job.user_id))
    ).scalar_one_or_none()
    if not user:
        fail(ErrorCode.USER_NOT_FOUND, message="任务用户不存在")

    # 释放旧预扣再重新预扣，保证算力账本与本次重跑对应
    await _release_active_reservation(db, job)
    await _reserve_for_requeue(db, job, user)

    await requeue_job_for_worker(db, job, clear_upstream_ids=clear_upstream_ids)
    _clear_anomaly(job)
    await db.flush()
    if clear_upstream_ids:
        return {"message": "已清空上游任务标记并重新入队（将重新提交上游）"}
    return {"message": "已重新入队并预扣算力（将续轮询已有上游任务）"}


def _generation_options_from_job(job: GenerationJob) -> dict[str, str]:
    params = job.input_params if isinstance(job.input_params, dict) else {}
    snapshot = params.get("optionSnapshot") or params.get("option_snapshot")
    if isinstance(snapshot, dict):
        return {str(k): str(v) for k, v in snapshot.items()}
    gen_opts = params.get("generationOptions") or params.get("generation_options")
    if isinstance(gen_opts, dict):
        return {str(k): str(v) for k, v in gen_opts.items()}
    return {}


async def _resolve_catalog_model(db: AsyncSession, job: GenerationJob) -> Model:
    model_name = job.model or (job.input_params or {}).get("model")
    if not model_name:
        fail(ErrorCode.MODEL_UNAVAILABLE, message="任务缺少模型信息")
    result = await db.execute(select(Model).filter(Model.name == model_name))
    catalog = result.scalar_one_or_none()
    if not catalog:
        fail(ErrorCode.MODEL_NOT_FOUND, message=f"模型 '{model_name}' 不存在")
    return catalog


async def _release_active_reservation(db: AsyncSession, job: GenerationJob) -> None:
    credit_status = (job.credit_status or "").strip().lower()
    if credit_status in (
        CreditStatus.RESERVED.value,
        CreditStatus.COMMIT_PENDING.value,
        CreditStatus.RELEASE_PENDING.value,
    ) and job.credit_reservation_id:
        await refund_job_credits(job, db, force=True)
    job.credit_reservation_id = None


async def _reserve_for_requeue(db: AsyncSession, job: GenerationJob, user: User) -> None:
    catalog = await _resolve_catalog_model(db, job)
    gen_opts = _generation_options_from_job(job)
    quote = quote_generation_cost(catalog, gen_opts, strict=False)

    if credits_enabled() and quote.total > 0:
        balance = await get_user_credit_balance(user, db, model_name=job.model)
        if balance is not None and balance < quote.total:
            fail(
                ErrorCode.INSUFFICIENT_CREDITS,
                message=f"算力不足，重新入队需要 {quote.total}，当前可用 {balance}",
                content={"required": quote.total, "available": balance},
            )

    requeue_seq = _increment_requeue_count(job)
    job.credit_cost = quote.total
    job.pricing_version = quote.pricing_version
    job.credit_breakdown = [item.to_dict() for item in quote.breakdown]

    project_id = extract_project_id(job) or ""
    node_id = job.node_id or ""
    reference = f"canvas:{project_id}:{node_id}:requeue:{requeue_seq}"

    if quote.total <= 0 or not credits_enabled():
        job.credit_status = CreditStatus.SKIPPED.value
        job.credit_reservation_id = None
        return

    from .local_credits import reserve_local_credits
    from .credit_transactions import record_job_consume_transaction

    idempotency_key = f"canvas-job-{job.id}-rq{requeue_seq}"
    result = await reserve_local_credits(
        db,
        user_id=user.id,
        amount=quote.total,
        idempotency_key=idempotency_key,
        reference=reference,
        job_id=job.id,
        model_name=job.model,
    )
    if not result or not result.get("reservation_id"):
        fail(ErrorCode.CREDIT_RESERVE_FAILED, message="算力不足或预扣失败")

    job.credit_reservation_id = str(result["reservation_id"])
    job.credit_status = CreditStatus.RESERVED.value
    if result.get("created") and quote.total > 0:
        balance_after = await get_user_credit_balance(user, db)
        await record_job_consume_transaction(
            db,
            user=user,
            job=job,
            balance_after=balance_after,
            idempotency_key=idempotency_key,
        )
    await db.flush()


async def _download_and_register_from_url(
    db: AsyncSession,
    *,
    job: GenerationJob,
    url: str,
    category: str,
) -> dict:
    project_id = extract_project_id(job)
    if not project_id:
        fail(ErrorCode.INVALID_PROJECT_ID, message="任务缺少 projectId")

    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)
    folder = project_storage_folder(project)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0), follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
        content_type = resp.headers.get("content-type") or {
            "image": "image/png",
            "video": "video/mp4",
            "audio": "audio/mpeg",
        }.get(category, "application/octet-stream")

    ext_map = {"image": "png", "video": "mp4", "audio": "mp3"}
    ext = ext_map.get(category, "bin")
    await assert_storage_quota_for_project(db, project_id, len(data))
    asset_id = new_asset_id()
    rel_path = f"assets/{category}/{asset_id}.{ext}"
    storage = get_canvas_storage()
    import asyncio

    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel_path,
            data,
            content_type.split(";")[0].strip(),
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"存储失败: {exc}")

    # 标题与正常生成一致（取 assetTitle / prompt），避免用户素材库出现「管理员同步上游产物」
    params = job.input_params if isinstance(job.input_params, dict) else {}
    title = (
        str(params.get("assetTitle") or params.get("asset_title") or "").strip()
        or str(params.get("prompt") or "").strip()[:40]
        or {"image": "AI 图片生成", "video": "AI 视频生成", "audio": "AI 音频生成"}.get(category, "AI 生成")
    )
    file_url = normalize_browser_storage_url(stored.file_url, oss_key=stored.oss_key)
    record = {
        "id": asset_id,
        "projectId": project_id,
        "title": title,
        "category": category,
        "subcategory": str(params.get("assetSubcategory") or params.get("asset_subcategory") or "素材").strip()
        or "素材",
        "fileUrl": file_url,
        "thumbnailUrl": "/uploads/audio-default.svg" if category == "audio" else file_url,
        "fileType": content_type.split(";")[0].strip(),
        "fileSize": len(data),
        "ossKey": stored.oss_key,
        "source": "generate",
        "createdAt": cst_iso_now(),
    }
    record = await insert_project_asset(db, project_id, record, sync_oss=False)
    from .cache import invalidate_manifest_cache

    await invalidate_manifest_cache(project_id)
    return record


async def _reuse_existing_job_asset(
    db: AsyncSession,
    job: GenerationJob,
    *,
    category: str,
) -> dict | None:
    """若任务已有有效产物资产则返回记录，供 sync 复用，禁止再下载入库。"""
    asset_id = str(job.asset_id or extract_asset_id(job) or "").strip()
    if not asset_id:
        return None
    project_id = extract_project_id(job)
    if not project_id:
        return None
    return await get_project_asset(db, project_id, asset_id, sync_oss=False)

async def execute_admin_job_action(
    db: AsyncSession,
    *,
    job_id: int,
    operator: User,
    action: str,
    note: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行管理员任务运维动作（加锁、审计、分发到具体 handler）。"""
    action = (action or "").strip().lower()
    if action not in ADMIN_ACTIONS:
        fail(ErrorCode.INVALID_ADMIN_ACTION, message=f"不支持的操作: {action}")

    note_text = _validate_note(note)
    body = dict(payload or {})

    async with job_admin_lock(str(job_id)):
        job = await _lock_job(db, job_id)
        before_status = job.status
        before_credit = job.credit_status

        if action == "reconcile_credits":
            result = await _action_reconcile_credits(db, job)
        elif action == "force_fail":
            result = await _action_force_fail(db, job, note_text, body)
        elif action == "force_succeed":
            result = await _action_force_succeed(db, job, note_text, body)
        elif action == "requeue":
            result = await _action_requeue(db, job, operator, note_text, body)
        elif action == "regenerate_upstream":
            result = await _action_regenerate_upstream(db, job, operator, note_text, body)
        elif action == "sync_upstream":
            result = await _action_sync_upstream(db, job, note_text)
        else:
            fail(ErrorCode.INVALID_ADMIN_ACTION, message="未知操作")

        audit = await _record_admin_action(
            db,
            job=job,
            operator=operator,
            action=action,
            note=note_text,
            payload=body,
            before_status=before_status,
            before_credit_status=before_credit,
        )
        await db.flush()
        return {
            "ok": True,
            "action": action,
            "jobId": str(job.id),
            "status": job.status,
            "creditStatus": job.credit_status,
            "assetId": job.asset_id,
            "errorMessage": job.error_message,
            "anomalyReason": job.anomaly_reason,
            "auditId": str(audit.id),
            **result,
        }


async def _action_reconcile_credits(db: AsyncSession, job: GenerationJob) -> dict[str, Any]:
    if job.status == JobStatus.SUCCEEDED.value:
        await commit_for_job(job, db)
    elif job.status == JobStatus.FAILED.value:
        await refund_job_credits(job, db, force=True)
    elif job.status == JobStatus.ABNORMAL.value:
        if job.asset_id or (isinstance(job.output_assets, list) and job.output_assets):
            await commit_for_job(job, db)
        else:
            await refund_job_credits(job, db, force=True)
    elif job.anomaly_reason == "CREDIT_MISMATCH":
        if job.status == JobStatus.SUCCEEDED.value:
            await commit_for_job(job, db)
        else:
            await refund_job_credits(job, db, force=True)
    else:
        fail(ErrorCode.INVALID_ADMIN_ACTION, message="仅终态或异常任务可同步算力")

    if job.anomaly_reason:
        _clear_anomaly(job)
    return {"message": "算力已同步"}


async def _action_force_fail(
    db: AsyncSession,
    job: GenerationJob,
    note: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if job.status == JobStatus.FAILED.value:
        return {"message": "任务已是失败状态", "unchanged": True}

    has_output = bool(job.output_assets) or bool(job.asset_id)
    if has_output and not payload.get("acknowledgeOutputLoss"):
        fail(
            ErrorCode.OUTPUT_LOSS_ACK_REQUIRED,
            message="任务已有输出产物，强制失败需确认 acknowledgeOutputLoss=true",
        )

    await fail_job_and_refund(
        db,
        job,
        f"管理员标记失败：{note}"[:2000],
        error_code="ADMIN_FORCE_FAIL",
        force=True,
    )
    _clear_anomaly(job)
    return {"message": "已标记失败并退还算力"}


async def _action_force_succeed(
    db: AsyncSession,
    job: GenerationJob,
    note: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if job.status == JobStatus.SUCCEEDED.value:
        return {"message": "任务已是成功状态", "unchanged": True}

    asset_id = str(payload.get("assetId") or payload.get("asset_id") or "").strip()
    if not asset_id:
        fail(ErrorCode.FORCE_SUCCEED_ASSET_REQUIRED)

    project_id = extract_project_id(job)
    if not project_id:
        fail(ErrorCode.INVALID_PROJECT_ID, message="任务缺少 projectId")

    asset = await get_project_asset(db, project_id, asset_id)
    if not asset:
        fail(ErrorCode.ASSET_NOT_IN_PROJECT)

    category = str(asset.get("category") or job.lane or "image").strip()
    if category == "text_llm":
        category = "image"
    output = [media_output_asset(asset, category)]
    await mark_job_succeeded(db, job, output)
    job.error_message = None
    _clear_anomaly(job)
    await commit_for_job(job, db)
    return {"message": "已标记成功并结算算力", "assetId": asset_id}


async def _action_requeue(
    db: AsyncSession,
    job: GenerationJob,
    operator: User,
    note: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """管理员重新入队：默认续轮询已有上游任务；仅当显式传入清标记参数时才重新提交。"""
    clear_upstream = bool(payload.get("clearUpstreamIds") or payload.get("regenerateUpstream"))
    return await _requeue_job_for_admin(
        db, job, operator, clear_upstream_ids=clear_upstream
    )


async def _action_regenerate_upstream(
    db: AsyncSession,
    job: GenerationJob,
    operator: User,
    note: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """管理员显式重新生成：清空上游标记，强制下一轮重新向上游提交（会再次扣上游费）。"""
    return await _requeue_job_for_admin(
        db, job, operator, clear_upstream_ids=True
    )


async def _action_sync_upstream(db: AsyncSession, job: GenerationJob, note: str) -> dict[str, Any]:
    task_id = _upstream_task_id(job)
    if not task_id:
        fail(ErrorCode.JOB_UPSTREAM_NO_TASK_ID)

    # 管理员显式同步：解除「停车等管理员」，允许后续 Worker 续轮询
    trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
    if isinstance(trace.get("upstreamAwaitingAdmin"), dict):
        trace.pop("upstreamAwaitingAdmin", None)
        job.trace_json = trace

    upstream = await query_upstream_task_status(provider=job.provider, task_id=task_id)

    if upstream.state == "unknown":
        await mark_job_abnormal(
            db,
            job,
            reason="UPSTREAM_UNPARSEABLE",
            detail=upstream.error_message or "上游响应无法解析",
        )
        await db.flush()
        return {
            "message": "上游状态无法解析，已标记异常",
            "upstreamState": upstream.state,
            "upstreamDetail": upstream.error_message,
        }

    if upstream.state in ("pending", "running"):
        # 重试用尽后停车的任务：管理员同步时下调 attempt，便于 Worker 再 claim 续轮询
        max_a = int(job.max_attempts or 3)
        if int(job.attempt_count or 0) >= max_a:
            job.attempt_count = max(0, max_a - 1)
        if job.status == JobStatus.ABNORMAL.value:
            if job.provider_task_id:
                await mark_job_polling(db, job)
            else:
                await mark_job_running(db, job)
        elif job.status != JobStatus.POLLING.value:
            await mark_job_polling(db, job)
        job.error_message = None
        await db.flush()
        return {
            "message": f"上游仍在进行中（{upstream.state}），已解除停车并交回轮询",
            "upstreamState": upstream.state,
        }

    if upstream.state == "failed":
        await fail_job_and_refund(
            db,
            job,
            upstream.error_message or "上游任务失败",
            error_code="UPSTREAM_TASK_FAILED",
            force=True,
        )
        _clear_anomaly(job)
        return {"message": "上游已失败，任务已同步为失败", "upstreamState": upstream.state}

    if upstream.state == "succeeded":
        if not upstream.result_url:
            await mark_job_abnormal(
                db,
                job,
                reason="UPSTREAM_UNPARSEABLE",
                detail="上游成功但未返回 URL",
            )
            await db.flush()
            return {"message": "上游成功但无产物 URL", "upstreamState": upstream.state}

        lane = job.lane or "image"
        category = LANE_CATEGORY.get(lane, "image")

        # 已有产物：只对齐终态，禁止再下载注册一份重复资产
        existing = await _reuse_existing_job_asset(db, job, category=category)
        if existing:
            if job.status != JobStatus.SUCCEEDED.value:
                output = [media_output_asset(existing, category)]
                await mark_job_succeeded(db, job, output)
                _clear_anomaly(job)
                await commit_for_job(job, db)
            return {
                "message": "上游已成功，复用已有产物（未重复入库）",
                "upstreamState": upstream.state,
                "assetId": existing.get("id"),
                "reused": True,
            }

        # Worker 仍在执行：交由 Worker 落库，避免与自动 sync 双下载双入库
        if await is_worker_job_lock_held(job.id):
            return {
                "message": "上游已成功，Worker 正在落库，跳过同步下载",
                "upstreamState": upstream.state,
                "deferredToWorker": True,
            }

        record = await _download_and_register_from_url(
            db,
            job=job,
            url=upstream.result_url,
            category=category,
        )
        output = [media_output_asset(record, category)]
        await mark_job_succeeded(db, job, output)
        _clear_anomaly(job)
        await commit_for_job(job, db)
        return {
            "message": "上游已成功，任务已同步为成功",
            "upstreamState": upstream.state,
            "assetId": record["id"],
        }

    return {"message": "未变更", "upstreamState": upstream.state}


async def list_admin_job_actions(
    db: AsyncSession,
    job_id: int,
    *,
    limit: int = 50,
) -> list[GenerationJobAdminAction]:
    """查询指定任务的管理员操作审计记录（按时间倒序）。"""
    result = await db.execute(
        select(GenerationJobAdminAction)
        .filter(GenerationJobAdminAction.job_id == job_id)
        .order_by(GenerationJobAdminAction.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
