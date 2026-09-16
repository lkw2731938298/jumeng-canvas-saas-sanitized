"""生成任务追溯 —— 在创建/完成时写入 request_id、provider、trace_json（无额外异步迁移）。"""

from __future__ import annotations

from ..core.datetime_util import cst_iso, cst_iso_now, now_cst_naive
import secrets
from datetime import datetime
from typing import Any

from ..integrations.upstream.trace_context import UpstreamTraceSnapshot, take_upstream_trace
from ..models.job import GenerationJob

SCENE_BY_CATEGORY = {
    "text": "text_llm",
    "image": "image_gen",
    "video": "video_gen",
    "audio": "audio_gen",
    "tool": "tool_gen",
}


def new_request_id() -> str:
    """生成任务级唯一请求 ID（32 位十六进制）。"""
    return secrets.token_hex(16)


def scene_for_category(category: str) -> str:
    """将前端类目映射为 trace 场景标识（如 image → image_gen）。"""
    return SCENE_BY_CATEGORY.get(category, "tool_gen")


def provider_for_model(model_id: str) -> str | None:
    """从模型注册表解析上游供应商标识。"""
    from ..core.model_registry import get_model_spec

    spec = get_model_spec(model_id)
    return spec.provider if spec else None


async def init_job_trace_fields(
    job: GenerationJob,
    *,
    category: str,
    model: str,
    created_at: datetime | None = None,
) -> None:
    """任务创建时初始化追溯字段：scene、request_id、provider、trace_json.createdAt。"""
    ts = created_at or now_cst_naive()
    job.scene = scene_for_category(category)
    job.request_id = new_request_id()
    job.provider = provider_for_model(model)
    job.trace_json = {"createdAt": cst_iso(ts) or cst_iso_now()}


def _merge_trace_json(job: GenerationJob, patch: dict[str, Any]) -> None:
    """浅合并 patch 到 job.trace_json。"""
    base = job.trace_json if isinstance(job.trace_json, dict) else {}
    base.update(patch)
    job.trace_json = base


def apply_upstream_trace(
    job: GenerationJob,
    snap: UpstreamTraceSnapshot | None,
    *,
    model_id: str | None = None,
) -> None:
    """将上游 trace 快照合并到 job 列字段与 trace_json.events/billing。"""
    if snap is None:
        snap = UpstreamTraceSnapshot()

    provider = snap.provider or (provider_for_model(model_id) if model_id else None)
    if provider and not job.provider:
        job.provider = provider

    if snap.provider_request_id:
        job.provider_request_id = snap.provider_request_id
    if snap.provider_task_id:
        job.provider_task_id = snap.provider_task_id
    if snap.provider_job_id:
        job.provider_job_id = snap.provider_job_id
    if snap.processing_id:
        job.processing_id = snap.processing_id

    upstream = snap.provider_task_id or snap.provider_job_id or snap.processing_id
    if upstream and not job.upstream_job_id:
        job.upstream_job_id = upstream

    if snap.events:
        existing = job.trace_json.get("events") if isinstance(job.trace_json, dict) else None
        events = list(existing) if isinstance(existing, list) else []
        events.extend(snap.events)
        _merge_trace_json(job, {"events": events})

    if snap.billing is not None:
        _merge_trace_json(job, {"billing": snap.billing.to_trace_dict()})
        if snap.billing.kind == "credits":
            job.upstream_credit_cost = snap.billing.amount


def capture_trace_after_upstream(job: GenerationJob, *, model_id: str | None = None) -> None:
    """上游调用结束后一次性取出 contextvar 快照并写入 job（同事务内调用）。"""
    snap = take_upstream_trace()
    apply_upstream_trace(job, snap, model_id=model_id)
    _merge_trace_json(job, {"completedAt": cst_iso_now()})


__all__ = [
    "apply_upstream_trace",
    "capture_trace_after_upstream",
    "init_job_trace_fields",
    "new_request_id",
    "provider_for_model",
    "scene_for_category",
]
