"""生成任务持久化 —— 创建 GenerationJob、状态流转、产物资产校验解析。"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.entity_ids import parse_entity_id, is_legacy_uuid

from ..core.job_status import JobStatus
from ..models.job import GenerationJob
from ..models.user import User
from ..services.asset_store import (
    get_project_asset,
    get_project_assets_by_ids,
    legacy_asset_uuid_from_oss_key,
)
from ..services.job_ids import allocate_generation_job_id
from ..services.job_trace import init_job_trace_fields

LANE_BY_CATEGORY = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "text": "text_llm",
    "tool": "image",
}

JOB_TYPE_BY_CATEGORY = {
    "image": "image_gen",
    "video": "video_gen",
    "audio": "audio_gen",
    "text": "text_llm",
    "tool": "image_gen",
}


def extract_node_id(job_or_params: GenerationJob | Any) -> Optional[str]:
    """从 job 或 input_params 中提取节点 ID（兼容驼峰/下划线键）。"""
    if isinstance(job_or_params, GenerationJob):
        if job_or_params.node_id:
            return str(job_or_params.node_id)
        input_params = job_or_params.input_params
    else:
        input_params = job_or_params
    if not isinstance(input_params, dict):
        return None
    node_id = input_params.get("nodeId") or input_params.get("node_id")
    return str(node_id) if node_id else None


def extract_project_id(job_or_params: GenerationJob | Any) -> Optional[str]:
    """从 job 或 input_params 中提取项目 ID（兼容驼峰/下划线键）。"""
    if isinstance(job_or_params, GenerationJob):
        if job_or_params.project_id:
            return str(job_or_params.project_id)
        input_params = job_or_params.input_params
    else:
        input_params = job_or_params
    if not isinstance(input_params, dict):
        return None
    project_id = input_params.get("projectId") or input_params.get("project_id")
    return str(project_id) if project_id else None


def extract_asset_id(job_or_output: GenerationJob | Any) -> Optional[str]:
    """从 job 或 output_assets 中提取首个产物资产 ID。"""
    if isinstance(job_or_output, GenerationJob):
        if job_or_output.asset_id:
            return str(job_or_output.asset_id)
        output_assets = job_or_output.output_assets
    else:
        output_assets = job_or_output
    if not isinstance(output_assets, list):
        return None
    for item in output_assets:
        if not isinstance(item, dict):
            continue
        asset_id = item.get("id") or item.get("assetId")
        if asset_id:
            return str(asset_id)
    return None


async def resolve_verified_asset_id(
    db: AsyncSession,
    job: GenerationJob,
    *,
    sync_oss: bool = True,
) -> Optional[str]:
    """仅当资产确实存在于该任务项目的资产索引中时才返回其 ID。"""
    verified = await resolve_verified_asset_ids_batch(db, [job], sync_oss=sync_oss)
    return verified.get(str(job.id))


async def _resolve_verified_assets_core(
    db: AsyncSession,
    jobs: list[GenerationJob],
    *,
    sync_oss: bool = True,
) -> tuple[dict[str, str], dict[str, dict]]:
    """批量校验多个任务的产物资产（按项目聚合查询，避免 N+1），兼容 legacy UUID。"""
    pending: dict[str, tuple[str, str]] = {}
    for job in jobs:
        project_id = extract_project_id(job)
        if not project_id:
            continue
        job_key = str(job.id)
        if job.asset_id:
            pending[job_key] = (project_id, str(job.asset_id))
            continue
        raw_id = extract_asset_id(job)
        if raw_id:
            pending[job_key] = (project_id, raw_id)

    if not pending:
        return {}, {}

    by_project: dict[str, set[str]] = {}
    for project_id, asset_id in pending.values():
        by_project.setdefault(project_id, set()).add(asset_id)

    verified: dict[tuple[str, str], tuple[str, dict]] = {}
    for project_id, asset_ids in by_project.items():
        rows = await get_project_assets_by_ids(
            db, project_id, asset_ids, sync_oss=sync_oss
        )
        for row in rows:
            rid = str(row["id"])
            keys = {rid, str(row.get("legacyId") or "").strip().lower()}
            legacy_from_key = legacy_asset_uuid_from_oss_key(row.get("ossKey"))
            if legacy_from_key:
                keys.add(legacy_from_key)
            for key in keys:
                if key:
                    verified[(project_id, key)] = (rid, row)

    id_out: dict[str, str] = {}
    record_out: dict[str, dict] = {}
    for job_key, (project_id, asset_id) in pending.items():
        lookup_key = asset_id.lower() if is_legacy_uuid(asset_id) else asset_id
        hit = verified.get((project_id, lookup_key)) or verified.get((project_id, asset_id))
        if hit:
            rid, row = hit
            id_out[job_key] = rid
            record_out[job_key] = row
    return id_out, record_out


async def resolve_verified_asset_ids_batch(
    db: AsyncSession,
    jobs: list[GenerationJob],
    *,
    sync_oss: bool = True,
) -> dict[str, str]:
    """批量返回 job_id → 已校验资产 ID。"""
    ids, _ = await _resolve_verified_assets_core(db, jobs, sync_oss=sync_oss)
    return ids


async def resolve_verified_job_assets_batch(
    db: AsyncSession,
    jobs: list[GenerationJob],
    *,
    sync_oss: bool = True,
) -> dict[str, tuple[Optional[str], Optional[dict]]]:
    """批量返回 job_id →（已校验资产 ID, 资产记录）。"""
    ids, records = await _resolve_verified_assets_core(db, jobs, sync_oss=sync_oss)
    out: dict[str, tuple[Optional[str], Optional[dict]]] = {}
    for job in jobs:
        job_key = str(job.id)
        out[job_key] = (ids.get(job_key), records.get(job_key))
    return out


async def resolve_verified_project_asset(
    db: AsyncSession,
    job: GenerationJob,
    *,
    sync_oss: bool = True,
) -> Optional[dict]:
    """返回单个任务已校验的产物资产记录（不存在时为 None）。"""
    assets = await resolve_verified_job_assets_batch(db, [job], sync_oss=sync_oss)
    _, record = assets.get(str(job.id), (None, None))
    return record


async def create_node_generation_job(
    db: AsyncSession,
    *,
    user: User,
    project_id: str,
    node_id: str,
    category: str,
    model: str,
    input_params: dict[str, Any],
    workflow_id: Optional[str] = None,
    actor_user: User | None = None,
) -> GenerationJob:
    """创建一条待处理生成任务（按类目定 lane/job_type，初始化 trace）。"""
    lane = LANE_BY_CATEGORY.get(category, "image")
    job_type = JOB_TYPE_BY_CATEGORY.get(category, "image_gen")

    workflow_id_int = parse_entity_id(workflow_id) if workflow_id else None

    project_id_int = parse_entity_id(project_id)

    now = now_cst_naive()
    actor_id = (actor_user or user).id
    job_id = await allocate_generation_job_id(db)
    job = GenerationJob(
        id=job_id,
        user_id=user.id,
        actor_user_id=actor_id if actor_id != user.id else None,
        project_id=project_id_int,
        workflow_id=workflow_id_int,
        node_id=node_id,
        job_type=job_type,
        lane=lane,
        status=JobStatus.PENDING.value,
        model=model,
        input_params={
            "projectId": project_id,
            "nodeId": node_id,
            "model": model,
            **input_params,
        },
        output_assets=[],
        progress_pct=0,
        trace_json={},
        created_at=now,
    )
    await init_job_trace_fields(job, category=category, model=model, created_at=now)
    db.add(job)
    await db.flush()
    return job


async def mark_job_pending(
    db: AsyncSession,
    job: GenerationJob,
    *,
    error_message: str | None = None,
    clear_claim: bool = True,
) -> None:
    """将任务重置为 pending（重试用），可选清除 Worker 认领标记。"""
    job.status = JobStatus.PENDING.value
    job.started_at = None
    if clear_claim:
        job.worker_claim_id = None
    if error_message is not None:
        job.error_message = error_message[:2000]
    await db.flush()


EXECUTION_TIMEOUT_ERROR_PREFIX = "任务执行超时"


def job_is_execution_timeout_parked(job: GenerationJob) -> bool:
    """是否已因 Worker 执行超时而停车，保持 running、预扣算力等待管理员处理。"""
    trace = job.trace_json if isinstance(job.trace_json, dict) else {}
    parked = trace.get("executionTimeout")
    if isinstance(parked, dict) and parked.get("awaitingAdmin"):
        return True
    msg = (job.error_message or "").strip()
    return msg.startswith(EXECUTION_TIMEOUT_ERROR_PREFIX)


def apply_execution_timeout_park(job: GenerationJob, *, error_message: str) -> None:
    """Worker 执行超时：保持 running、释放 claim、预扣不动，等待管理员处理。"""
    from ..core.datetime_util import cst_iso_now

    job.status = JobStatus.RUNNING.value
    job.error_message = error_message[:2000]
    job.worker_claim_id = None
    job.completed_at = None
    trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
    trace["executionTimeout"] = {
        "awaitingAdmin": True,
        "detail": error_message[:500],
        "parkedAt": cst_iso_now(),
    }
    job.trace_json = trace


async def park_job_on_execution_timeout(
    db: AsyncSession,
    job: GenerationJob,
    *,
    error_message: str,
) -> None:
    """将超时任务停车：status=running，不退预扣算力。"""
    apply_execution_timeout_park(job, error_message=error_message)
    await db.flush()


async def mark_job_running(db: AsyncSession, job: GenerationJob) -> None:
    """标记任务为执行中并记录开始时间。"""
    job.status = JobStatus.RUNNING.value
    job.started_at = now_cst_naive()
    await db.flush()


async def mark_job_polling(db: AsyncSession, job: GenerationJob) -> None:
    """标记任务为轮询上游中（已 submit，只查询不再次提交）。"""
    job.status = JobStatus.POLLING.value
    await db.flush()


async def mark_job_succeeded(
    db: AsyncSession,
    job: GenerationJob,
    output_assets: list[dict[str, Any]],
    *,
    result_text: Optional[str] = None,
) -> None:
    """标记任务成功：写入产物、校验 asset_id、记录终态调用日志。"""
    job.status = JobStatus.SUCCEEDED.value
    job.output_assets = output_assets
    job.completed_at = now_cst_naive()
    job.error_message = None
    if result_text is not None:
        job.result_text = result_text

    raw_asset_id = extract_asset_id(output_assets)
    project_id = extract_project_id(job)
    if raw_asset_id and project_id:
        asset = await get_project_asset(db, project_id, raw_asset_id)
        job.asset_id = raw_asset_id if asset else None
    else:
        job.asset_id = None

    from .generation_call_logs import log_job_terminal_response

    await log_job_terminal_response(db, job, success=True)
    await db.flush()


async def mark_job_failed(
    db: AsyncSession,
    job: GenerationJob,
    error_message: str,
    *,
    error_code: str | None = None,
) -> None:
    """标记任务失败：写入错误信息、可选 errorCode 到 trace、记录终态日志。"""
    job.status = JobStatus.FAILED.value
    job.error_message = error_message[:2000]
    job.completed_at = now_cst_naive()
    if error_code:
        trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
        trace["errorCode"] = error_code
        job.trace_json = trace
    from .generation_call_logs import log_job_terminal_response

    await log_job_terminal_response(db, job, success=False, error_message=error_message)
    await db.flush()
    # 任务失败站内通知（幂等：同 job 只提醒一次）
    try:
        from .user_notifications import CATEGORY_JOB, notify_user

        uid = int(getattr(job, "user_id", 0) or 0)
        if uid > 0:
            model_name = str(getattr(job, "model", None) or "").strip() or "生成任务"
            reason = (error_message or "未知错误").strip()[:500]
            project_id = getattr(job, "project_id", None)
            link = f"/{project_id}" if project_id else None
            await notify_user(
                db,
                uid,
                category=CATEGORY_JOB,
                title="生成任务失败",
                body=f"「{model_name}」失败：{reason}",
                dedupe_key=f"job:failed:{int(job.id)}",
                link_url=link,
                ref_type="generation_job",
                ref_id=str(job.id),
            )
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "notify job failed skipped job=%s", getattr(job, "id", None), exc_info=True
        )


async def mark_job_abnormal(
    db: AsyncSession,
    job: GenerationJob,
    *,
    reason: str,
    detail: str,
    now: datetime | None = None,
) -> None:
    """标记任务为异常态（需人工介入），写入 anomaly 字段与 trace。"""
    from ..core.datetime_util import cst_iso, cst_iso_now

    ts = now or now_cst_naive()
    job.status = JobStatus.ABNORMAL.value
    job.anomaly_reason = reason[:64]
    job.anomaly_detected_at = ts
    job.anomaly_detail = detail[:2000]
    trace = dict(job.trace_json) if isinstance(job.trace_json, dict) else {}
    trace["anomaly"] = {
        "code": reason,
        "detail": detail[:500],
        "auto": True,
        "detectedAt": cst_iso(ts) or cst_iso_now(),
    }
    job.trace_json = trace
    await db.flush()


async def requeue_job_for_worker(
    db: AsyncSession,
    job: GenerationJob,
    *,
    note: str | None = None,
    clear_upstream_ids: bool = False,
) -> None:
    """将任务重置为 pending 等待 Worker 重新拾取（管理员 requeue 场景）。

    clear_upstream_ids 仅当管理员“显式重新生成”时传 True，才会清空上游 task_id 触发重新 submit；
    自动恢复（stale 恢复、启动恢复、普通重试）必须保持默认 False，
    这样 Worker 会对已有上游任务续轮询，而不是再次向上游提交（防止重复扣费）。
    """
    if clear_upstream_ids:
        # 局部导入避免与 upstream_job_resume 形成模块级循环依赖
        from .credit_operation_lock import clear_job_upstream_submit
        from .upstream_job_resume import clear_upstream_task_ids

        clear_upstream_task_ids(job)
        await clear_job_upstream_submit(int(job.id))
    # 重置执行状态字段，但（默认情况下）保留 provider_task_id 等上游标记
    job.status = JobStatus.PENDING.value
    job.started_at = None
    job.worker_claim_id = None
    job.completed_at = None
    job.error_message = note[:2000] if note else None
    job.progress_pct = 0
    job.output_assets = []
    job.asset_id = None
    job.result_text = None
    job.anomaly_reason = None
    job.anomaly_detected_at = None
    job.anomaly_detail = None
    await db.flush()


def media_output_asset(record: dict, category: str) -> dict[str, Any]:
    """将素材库记录格式化为 job.output_assets 中的媒体产物条目。"""
    return {
        "id": record["id"],
        "assetId": record["id"],
        "type": category,
        "url": record.get("fileUrl"),
        "projectId": record.get("projectId"),
        "title": record.get("title"),
        "category": record.get("category") or category,
    }


def text_output_asset(node_id: str, content: str, model: str) -> dict[str, Any]:
    """将文本生成结果格式化为 job.output_assets 中的文本产物条目。"""
    return {
        "type": "text",
        "nodeId": node_id,
        "model": model,
        "contentPreview": content[:200],
    }
