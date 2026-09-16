"""管理后台生成任务列表筛选、行数据组装与 CSV 导出。"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from ..core.datetime_util import as_cst_aware
from ..core.credit_amount import normalize_credit_amount
from ..core.entity_ids import format_user_display_id, parse_entity_id, parse_user_display_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.job import GenerationJob, Model
from ..models.project import Project
from ..models.user import User
from ..services.generation_jobs import (
    extract_asset_id,
    extract_node_id,
    extract_project_id,
    resolve_verified_asset_id,
    resolve_verified_asset_ids_batch,
)
from ..services.job_anomaly import ANOMALY_REASON_LABEL
from ..services.model_channels import channel_summary_from_job
from ..services.upstream_cost import (
    compute_upstream_cost_yuan,
    format_upstream_cost_yuan,
)

JOB_STATUS_LABEL: dict[str, str] = {
    "pending": "等待中",
    "running": "运行中",
    "polling": "轮询中",
    "abnormal": "异常",
    "succeeded": "成功",
    "failed": "失败",
}

JOB_LANE_LABEL: dict[str, str] = {
    "image": "图片",
    "video": "视频",
    "text_llm": "文本",
    "audio": "音频",
}

BILLING_KIND_LABEL: dict[str, str] = {
    "tokens": "Token",
    "credits": "积分",
    "cny_fen": "人民币(分)",
    "usd_micro": "美元(微元)",
    "image_count": "图片张数",
    "video_seconds": "视频秒数",
}

EXPORT_MAX_ROWS = 50_000


@dataclass
class AdminJobFilters:
    """管理后台任务列表查询条件。"""
    status: Optional[str] = None
    lane: Optional[str] = None
    anomaly_reason: Optional[str] = None
    has_admin_action: bool = False
    user_id: Optional[int] = None
    user_filter: Optional[str] = None
    project_id: Optional[int] = None
    job_id: Optional[str] = None
    project_search: Optional[str] = None
    created_from: Optional[datetime] = None
    created_to: Optional[datetime] = None


def _trace_billing(job: GenerationJob) -> dict[str, Any]:
    trace = job.trace_json if isinstance(job.trace_json, dict) else {}
    billing = trace.get("billing")
    return billing if isinstance(billing, dict) else {}


def job_execution_seconds(job: GenerationJob) -> int | None:
    """计算任务墙钟执行时长（started_at → completed_at，秒）。"""
    if not job.started_at or not job.completed_at:
        return None
    started = as_cst_aware(job.started_at)
    completed = as_cst_aware(job.completed_at)
    if started is None or completed is None:
        return None
    return max(int((completed - started).total_seconds()), 0)


def _is_video_job(job: GenerationJob, model_row: Model | None) -> bool:
    if job.lane == "video":
        return True
    if model_row and str(model_row.category or "").strip().lower() == "video":
        return True
    return False


def job_video_duration_seconds(job: GenerationJob, model_row: Model | None) -> int | None:
    """解析视频任务生成时长（秒）；非视频或未知时返回 None。"""
    if not _is_video_job(job, model_row):
        return None

    billing = _trace_billing(job)
    if str(billing.get("kind") or "").strip() == "video_seconds":
        amount = billing.get("amount")
        if amount is not None:
            try:
                return max(int(amount), 0)
            except (TypeError, ValueError):
                pass

    params = job.input_params if isinstance(job.input_params, dict) else {}
    option_snapshot = params.get("optionSnapshot") or params.get("option_snapshot")
    if not isinstance(option_snapshot, dict):
        option_snapshot = {}
    else:
        option_snapshot = {str(k): str(v) for k, v in option_snapshot.items()}

    presets: dict[str, Any] | None = None
    if model_row and isinstance(model_row.parameters, dict):
        raw_presets = model_row.parameters.get("generationPresets")
        presets = raw_presets if isinstance(raw_presets, dict) else None

    from .credit_pricing import _duration_seconds

    if option_snapshot.get("duration"):
        return _duration_seconds(presets, option_snapshot)

    gen_opts = params.get("generationOptions") or params.get("generation_options")
    if isinstance(gen_opts, dict):
        gen_snapshot = {str(k): str(v) for k, v in gen_opts.items()}
        if gen_snapshot.get("duration"):
            return _duration_seconds(presets, gen_snapshot)

    for key in ("duration", "durationSec", "duration_sec"):
        raw = params.get(key)
        if raw is not None:
            try:
                return max(1, int(raw))
            except (TypeError, ValueError):
                pass

    return None


def upstream_credits_cost(job: GenerationJob) -> float | None:
    """从 trace 提取上游显式返回的积分/点数消耗。"""
    billing = _trace_billing(job)
    if str(billing.get("kind") or "").strip() != "credits":
        return None
    amount = billing.get("amount")
    if amount is None:
        return None
    try:
        return normalize_credit_amount(amount, default=0)
    except (TypeError, ValueError):
        return None


def export_upstream_cost_value(
    job: GenerationJob,
    model_row: Model | None,
    *,
    video_seconds: int | None,
) -> str:
    """CSV 导出列：按管理端成本价配置估算上游金额（元）。"""
    yuan = compute_upstream_cost_yuan(job, model_row, video_seconds=video_seconds)
    return format_upstream_cost_yuan(yuan)


def actual_deducted_credits(job: GenerationJob) -> float:
    """实际向用户扣除的算力（已释放/跳过则为 0）。"""
    credit_status = (job.credit_status or "").strip().lower()
    if credit_status in ("released", "release_pending", "skipped"):
        return 0.0
    return normalize_credit_amount(job.credit_cost, default=0)


def platform_quoted_credits(job: GenerationJob) -> float:
    """平台侧对该任务报价的算力总额。"""
    return normalize_credit_amount(job.credit_cost, default=0)


def billing_kind_label(job: GenerationJob) -> str:
    """上游计费类型中文标签（Token/积分/视频秒数等）。"""
    trace = job.trace_json if isinstance(job.trace_json, dict) else {}
    billing = trace.get("billing")
    if not isinstance(billing, dict):
        return ""
    kind = str(billing.get("kind") or "").strip()
    return BILLING_KIND_LABEL.get(kind, kind)


def apply_admin_job_filters(
    query: Select,
    count_query: Select,
    filters: AdminJobFilters,
) -> tuple[Select, Select]:
    """将 AdminJobFilters 应用到列表查询与计数查询。"""
    if filters.status:
        query = query.filter(GenerationJob.status == filters.status)
        count_query = count_query.filter(GenerationJob.status == filters.status)
    if filters.lane:
        query = query.filter(GenerationJob.lane == filters.lane)
        count_query = count_query.filter(GenerationJob.lane == filters.lane)
    if filters.anomaly_reason:
        query = query.filter(GenerationJob.anomaly_reason == filters.anomaly_reason)
        count_query = count_query.filter(GenerationJob.anomaly_reason == filters.anomaly_reason)
    if filters.has_admin_action:
        query = query.filter(GenerationJob.last_admin_action.isnot(None))
        count_query = count_query.filter(GenerationJob.last_admin_action.isnot(None))
    if filters.user_id:
        query = query.filter(GenerationJob.user_id == filters.user_id)
        count_query = count_query.filter(GenerationJob.user_id == filters.user_id)
    elif filters.user_filter:
        trimmed = filters.user_filter.strip()
        like = f"%{trimmed}%"
        prefix = f"{trimmed}%"
        user_ids = select(User.id).where(
            or_(
                User.phone.ilike(like),
                User.display_name.ilike(like),
                cast(User.id, String).like(prefix),
            )
        )
        query = query.filter(GenerationJob.user_id.in_(user_ids))
        count_query = count_query.filter(GenerationJob.user_id.in_(user_ids))
    if filters.project_id:
        query = query.filter(GenerationJob.project_id == filters.project_id)
        count_query = count_query.filter(GenerationJob.project_id == filters.project_id)
    if filters.job_id:
        trimmed = filters.job_id.strip()
        job_id_int = parse_entity_id(trimmed)
        if job_id_int is not None:
            query = query.filter(GenerationJob.id == job_id_int)
            count_query = count_query.filter(GenerationJob.id == job_id_int)
        else:
            like = f"{trimmed}%"
            query = query.filter(cast(GenerationJob.id, String).like(like))
            count_query = count_query.filter(cast(GenerationJob.id, String).like(like))
    if filters.project_search and filters.project_search.strip():
        term = f"%{filters.project_search.strip()}%"
        project_ids = select(Project.id).where(
            or_(
                Project.title.ilike(term),
                Project.project_no.ilike(term),
                cast(Project.id, String).ilike(term),
            )
        )
        query = query.filter(GenerationJob.project_id.in_(project_ids))
        count_query = count_query.filter(GenerationJob.project_id.in_(project_ids))
    if filters.created_from:
        query = query.filter(GenerationJob.created_at >= filters.created_from.replace(tzinfo=None))
        count_query = count_query.filter(GenerationJob.created_at >= filters.created_from.replace(tzinfo=None))
    if filters.created_to:
        query = query.filter(GenerationJob.created_at < filters.created_to.replace(tzinfo=None))
        count_query = count_query.filter(GenerationJob.created_at < filters.created_to.replace(tzinfo=None))
    return query, count_query


async def load_projects(db: AsyncSession, jobs: list[GenerationJob]) -> dict[str, Project]:
    """批量加载任务关联的项目实体。"""
    project_ids: set[int] = set()
    for job in jobs:
        if job.project_id:
            project_ids.add(job.project_id)
            continue
        project_id = extract_project_id(job)
        if not project_id:
            continue
        parsed = parse_entity_id(project_id)
        if parsed is not None:
            project_ids.add(parsed)
    if not project_ids:
        return {}
    result = await db.execute(select(Project).filter(Project.id.in_(project_ids)))
    return {str(project.id): project for project in result.scalars().all()}


async def load_users(db: AsyncSession, jobs: list[GenerationJob]) -> dict[str, User]:
    """批量加载任务提交用户。"""
    user_ids = {job.user_id for job in jobs if job.user_id}
    if not user_ids:
        return {}
    result = await db.execute(select(User).filter(User.id.in_(user_ids)))
    return {str(user.id): user for user in result.scalars().all()}


async def load_models(db: AsyncSession, jobs: list[GenerationJob]) -> dict[str, Model]:
    """批量加载任务使用的模型目录行。"""
    names = {str(job.model).strip() for job in jobs if job.model}
    if not names:
        return {}
    result = await db.execute(select(Model).filter(Model.name.in_(names)))
    return {model.name: model for model in result.scalars().all()}


def parse_admin_job_filters(
    *,
    status: Optional[str] = None,
    lane: Optional[str] = None,
    anomaly_reason: Optional[str] = None,
    has_admin_action: bool = False,
    user_id: Optional[str] = None,
    project_id: Optional[str] = None,
    job_id: Optional[str] = None,
    project_search: Optional[str] = None,
    created_from: Optional[str] = None,
    created_to: Optional[str] = None,
) -> AdminJobFilters:
    """解析管理端任务列表 HTTP 查询参数为结构化筛选条件。"""
    from ..core.datetime_util import parse_query_datetime

    user_id_int = None
    user_filter = None
    if user_id:
        trimmed = user_id.strip()
        parsed = parse_user_display_id(trimmed)
        if parsed is not None:
            user_id_int = parsed
        else:
            user_filter = trimmed

    project_id_int = None
    if project_id:
        project_id_int = parse_entity_id(project_id)
        if project_id_int is None:
            fail(ErrorCode.INVALID_PROJECT_ID)

    created_from_dt = None
    created_to_dt = None
    try:
        if created_from:
            created_from_dt = parse_query_datetime(created_from)
        if created_to:
            created_to_dt = parse_query_datetime(created_to)
    except ValueError:
        fail(ErrorCode.INVALID_DATETIME)

    return AdminJobFilters(
        status=status,
        lane=lane,
        anomaly_reason=anomaly_reason,
        has_admin_action=has_admin_action,
        user_id=user_id_int,
        user_filter=user_filter,
        project_id=project_id_int,
        job_id=job_id,
        project_search=project_search,
        created_from=created_from_dt,
        created_to=created_to_dt,
    )


async def build_admin_job_row(
    db: AsyncSession,
    job: GenerationJob,
    *,
    projects: dict[str, Project],
    users: dict[str, User],
    models: dict[str, Model],
    verified_asset_id: str | None = None,
    asset_resolved: bool = False,
) -> dict[str, Any]:
    """组装单条管理后台任务列表行（含项目/用户/模型展示字段）。"""
    project_id = str(job.project_id) if job.project_id else extract_project_id(job)
    project = projects.get(project_id) if project_id else None
    user = users.get(str(job.user_id))
    model_row = models.get(str(job.model or ""))
    model_display = (model_row.display_name if model_row and model_row.display_name else None) or job.model

    if asset_resolved:
        asset_id = verified_asset_id or (
            str(job.asset_id) if job.asset_id else extract_asset_id(job)
        )
    else:
        asset_id = await resolve_verified_asset_id(db, job, sync_oss=False)

    # 主/副通道快照（trace_json.channel），列表区分实际命中线路
    channel = channel_summary_from_job(job)

    return {
        "id": str(job.id),
        "user_id": str(job.user_id),
        "user_no": format_user_display_id(user.id) if user else None,
        "user_display_name": (user.display_name if user else None) or "",
        "user_phone": user.phone if user else None,
        "project_id": project_id,
        "project_no": project.project_no if project else None,
        "project_title": project.title if project else None,
        "workflow_id": str(job.workflow_id) if job.workflow_id else None,
        "node_id": extract_node_id(job),
        "job_type": job.job_type,
        "scene": job.scene,
        "lane": job.lane,
        "status": job.status,
        "provider": job.provider,
        "request_id": job.request_id,
        "provider_request_id": job.provider_request_id,
        "provider_task_id": job.provider_task_id,
        "provider_job_id": job.provider_job_id,
        "processing_id": job.processing_id,
        "upstream_job_id": job.upstream_job_id,
        "model": job.model,
        "model_display_name": model_display,
        "upstream_channel": channel.get("role") if channel else None,
        "upstream_channel_model": channel.get("model_name") if channel else None,
        "upstream_channel_provider": channel.get("provider") if channel else None,
        "asset_id": asset_id,
        "credit_cost": normalize_credit_amount(job.credit_cost, default=0),
        "upstream_credit_cost": upstream_credits_cost(job),
        "credit_status": job.credit_status,
        "pricing_version": job.pricing_version,
        "credit_breakdown": job.credit_breakdown if isinstance(job.credit_breakdown, list) else None,
        "error_message": job.error_message,
        "anomaly_reason": job.anomaly_reason,
        "anomaly_detected_at": job.anomaly_detected_at,
        "anomaly_detail": job.anomaly_detail,
        "last_admin_action": job.last_admin_action,
        "last_admin_action_at": job.last_admin_action_at,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "duration_seconds": job_video_duration_seconds(job, model_row),
        "execution_seconds": job_execution_seconds(job),
        "actual_deducted_credits": actual_deducted_credits(job),
        "platform_quoted_credits": platform_quoted_credits(job),
        "upstream_billing_kind": billing_kind_label(job),
    }


async def build_admin_job_rows(
    db: AsyncSession,
    jobs: list[GenerationJob],
    *,
    projects: dict[str, Project],
    users: dict[str, User],
    models: dict[str, Model],
    sync_oss: bool = False,
) -> list[dict[str, Any]]:
    """批量组装管理后台任务列表行。"""
    verified_assets = await resolve_verified_asset_ids_batch(
        db, jobs, sync_oss=sync_oss
    )
    rows: list[dict[str, Any]] = []
    for job in jobs:
        rows.append(
            await build_admin_job_row(
                db,
                job,
                projects=projects,
                users=users,
                models=models,
                verified_asset_id=verified_assets.get(str(job.id)),
                asset_resolved=True,
            )
        )
    return rows


def _format_cn_datetime(dt: datetime | None) -> str:
    """Format stored CST naive datetimes for CSV export."""
    if dt is None:
        return ""
    naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
    return naive.strftime("%Y-%m-%d %H:%M:%S")


def _submitter_label(row: dict[str, Any]) -> str:
    name = str(row.get("user_display_name") or "").strip()
    phone = str(row.get("user_phone") or "").strip()
    user_no = str(row.get("user_no") or "").strip()
    if name and phone:
        return f"{name} ({phone})"
    if name and user_no:
        return f"{name} ({user_no})"
    return name or phone or user_no or str(row.get("user_id") or "")


def _project_label(row: dict[str, Any]) -> str:
    title = str(row.get("project_title") or "").strip()
    project_no = str(row.get("project_no") or "").strip()
    if title and project_no:
        return f"{title} [{project_no}]"
    return title or project_no or str(row.get("project_id") or "")


async def export_admin_jobs_csv(db: AsyncSession, filters: AdminJobFilters) -> tuple[str, int]:
    """按筛选条件导出任务 CSV，返回内容与行数。"""
    query = select(GenerationJob)
    count_query = select(func.count(GenerationJob.id))
    query, count_query = apply_admin_job_filters(query, count_query, filters)
    total = (await db.execute(count_query)).scalar_one()
    if total > EXPORT_MAX_ROWS:
        fail(
            ErrorCode.EXPORT_TOO_LARGE,
            message=f"导出条数 {total} 超过上限 {EXPORT_MAX_ROWS}，请缩小筛选范围",
            content={"total": int(total), "maxRows": EXPORT_MAX_ROWS},
        )

    result = await db.execute(
        query.order_by(GenerationJob.id.desc(), GenerationJob.created_at.desc()).limit(EXPORT_MAX_ROWS)
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

    buffer = io.StringIO()
    buffer.write("\ufeff")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "任务ID",
            "任务ID",
            "生成模型",
            "提交人",
            "所属项目",
            "状态",
            "通道",
            "创建时间",
            "完成时间",
            "视频时长(秒)",
            "扣除算力点",
            "平台设置算力扣费",
            "上游成本(元)",
            "上游积分(实返)",
            "上游计量类型",
        ]
    )

    for job, row in zip(jobs, rows, strict=True):
        model_row = models.get(str(job.model or ""))
        video_seconds = row.get("duration_seconds")
        upstream_export = export_upstream_cost_value(
            job,
            model_row,
            video_seconds=video_seconds if isinstance(video_seconds, int) else None,
        )
        upstream_credits = upstream_credits_cost(job)
        writer.writerow(
            [
                row["id"],
                str(row.get("id") or ""),
                row.get("model_display_name") or row.get("model") or "",
                _submitter_label(row),
                _project_label(row),
                JOB_STATUS_LABEL.get(str(row.get("status") or ""), row.get("status") or ""),
                JOB_LANE_LABEL.get(str(row.get("lane") or ""), row.get("lane") or ""),
                _format_cn_datetime(row.get("created_at")),
                _format_cn_datetime(row.get("completed_at")),
                row.get("duration_seconds") if row.get("duration_seconds") is not None else "",
                row.get("actual_deducted_credits", 0),
                row.get("platform_quoted_credits", 0),
                upstream_export,
                upstream_credits if upstream_credits is not None else "",
                row.get("upstream_billing_kind") or "",
            ]
        )

    return buffer.getvalue(), int(total)
