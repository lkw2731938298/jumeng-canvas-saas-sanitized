"""上游 API 调用追溯上下文（基于 contextvars，无额外 DB 往返）。

在单次生成请求生命周期内累积 provider、task id 与事件列表，
供 Worker 落库至 job trace 与 generation_call_logs。
"""

from __future__ import annotations

import contextvars
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .upstream_billing import UpstreamBillingInfo, extract_upstream_billing


@dataclass
class UpstreamTraceSnapshot:
    """单次上游调用过程中累积的追溯快照。"""

    provider: str | None = None
    provider_request_id: str | None = None
    provider_task_id: str | None = None
    provider_job_id: str | None = None
    processing_id: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    billing: UpstreamBillingInfo | None = None


_trace: contextvars.ContextVar[UpstreamTraceSnapshot | None] = contextvars.ContextVar(
    "upstream_trace", default=None
)
_flush_hook: contextvars.ContextVar[Callable[[], Awaitable[None]] | None] = contextvars.ContextVar(
    "upstream_trace_flush_hook", default=None
)


def _active() -> UpstreamTraceSnapshot:
    snap = _trace.get()
    if snap is None:
        snap = UpstreamTraceSnapshot()
        _trace.set(snap)
    return snap


def note_upstream(
    *,
    provider: str | None = None,
    provider_request_id: str | None = None,
    provider_task_id: str | None = None,
    provider_job_id: str | None = None,
    processing_id: str | None = None,
    event: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """记录上游标识符或事件到当前请求的追溯快照。"""
    snap = _active()
    if provider:
        snap.provider = str(provider).strip()
    if provider_request_id:
        snap.provider_request_id = str(provider_request_id).strip()
    if provider_task_id:
        snap.provider_task_id = str(provider_task_id).strip()
        snap.processing_id = snap.processing_id or snap.provider_task_id
    if provider_job_id:
        snap.provider_job_id = str(provider_job_id).strip()
    if processing_id:
        snap.processing_id = str(processing_id).strip()
    if event:
        snap.events.append({"event": event, **(detail or {})})


def note_upstream_billing_from_response(
    data: Any,
    *,
    provider: str | None = None,
    event: str | None = None,
) -> None:
    """从上游 JSON 响应中提取可计费用量并写入快照（后者覆盖前者）。"""
    info = extract_upstream_billing(data, provider=provider)
    if info is None:
        return
    snap = _active()
    if provider and not snap.provider:
        snap.provider = str(provider).strip()
    snap.billing = info
    if event:
        snap.events.append(
            {
                "event": event,
                "billingAmount": info.amount,
                "billingKind": info.kind,
            }
        )


def peek_upstream_trace() -> UpstreamTraceSnapshot | None:
    """读取当前请求的追溯快照，不消费上下文。"""
    return _trace.get()


def set_upstream_trace_flush_hook(
    hook: Callable[[], Awaitable[None]] | None,
) -> contextvars.Token:
    """注册“立即落库当前 trace 快照”的回调（由 Worker 侧提供，把 provider_task_id 等写入 job）。"""
    return _flush_hook.set(hook)


def reset_upstream_trace_flush_hook(token: contextvars.Token) -> None:
    """重置上游 trace flush 回调为注册前的状态。"""
    _flush_hook.reset(token)


async def maybe_flush_upstream_trace() -> None:
    """若已注册 flush 回调则立即执行：submit 后调用，确保 provider_task_id 尽早持久化并 commit。

    这是“每个 job 只 submit 一次”的关键——task_id 必须在 poll 开始前 commit，
    部署/重启时恢复逻辑才能凭 DB 中的 id 续轮询。
    """
    hook = _flush_hook.get()
    if hook is not None:
        await hook()


def take_upstream_trace() -> UpstreamTraceSnapshot | None:
    """取出并清空当前请求的追溯快照（请求结束时调用）。"""
    snap = _trace.get()
    _trace.set(None)
    return snap


__all__ = [
    "UpstreamTraceSnapshot",
    "maybe_flush_upstream_trace",
    "note_upstream",
    "note_upstream_billing_from_response",
    "peek_upstream_trace",
    "reset_upstream_trace_flush_hook",
    "set_upstream_trace_flush_hook",
    "take_upstream_trace",
]
