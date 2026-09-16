"""画布模型主/副上游通道：配置解析、failover 判定、任务追溯快照。

通道引用「目录模型 name」走现有 dispatch；用户侧 job.model / 报价仍用画布模型名。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..core.datetime_util import cst_iso_now
from ..core.model_registry import get_model_spec
from ..integrations.providers.stubs import ProviderNotImplementedError
from ..integrations.upstream.errors import UpstreamError
from ..models.job import GenerationJob, Model

# 默认可切换副通道的上游错误码（主通道已写入 provider_task_id 时禁止切换）
DEFAULT_FAILOVER_ERROR_CODES: tuple[str, ...] = (
    "SUBMIT_ERROR",
    "TIMEOUT",
    "RATE_LIMITED",
    "NOT_CONFIGURED",
    "UPSTREAM_HTTP_ERROR",
    "UPSTREAM_ERROR",
)

VALID_CHANNEL_ROLES = frozenset({"primary", "fallback"})


@dataclass(frozen=True)
class ModelChannel:
    """单条上游通道（主或副）。"""

    role: str
    model_name: str
    enabled: bool = True
    on_errors: tuple[str, ...] = DEFAULT_FAILOVER_ERROR_CODES
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "modelName": self.model_name,
            "enabled": self.enabled,
            "onErrors": list(self.on_errors),
            "label": self.label,
        }


def _as_str_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _parse_channel_entry(raw: Any, *, catalog_name: str) -> ModelChannel | None:
    if not isinstance(raw, dict):
        return None
    role = str(raw.get("role") or "").strip().lower()
    if role not in VALID_CHANNEL_ROLES:
        return None
    model_name = str(raw.get("modelName") or raw.get("model_name") or "").strip()
    if not model_name:
        model_name = catalog_name
    enabled = raw.get("enabled")
    if enabled is None:
        enabled = True
    on_raw = raw.get("onErrors") or raw.get("on_errors")
    on_errors = tuple(_as_str_list(on_raw)) if on_raw is not None else DEFAULT_FAILOVER_ERROR_CODES
    if not on_errors:
        on_errors = DEFAULT_FAILOVER_ERROR_CODES
    label = str(raw.get("label") or "").strip()
    return ModelChannel(
        role=role,
        model_name=model_name,
        enabled=bool(enabled),
        on_errors=on_errors,
        label=label,
    )


def normalize_channels_payload(
    raw: Any,
    *,
    catalog_name: str,
) -> list[dict[str, Any]]:
    """校验并规范化 channels 写入 parameters；非法结构抛 ValueError。"""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("channels 须为数组")

    parsed: list[ModelChannel] = []
    seen_roles: set[str] = set()
    for entry in raw:
        ch = _parse_channel_entry(entry, catalog_name=catalog_name)
        if ch is None:
            raise ValueError("channels 项须含 role=primary|fallback 与 modelName")
        if ch.role in seen_roles:
            raise ValueError(f"channels 中 role={ch.role} 只能出现一次")
        seen_roles.add(ch.role)
        parsed.append(ch)

    # 无 primary 时补本模型为主通道，保证运行时可解析
    if "primary" not in seen_roles and parsed:
        parsed.insert(
            0,
            ModelChannel(role="primary", model_name=catalog_name, enabled=True),
        )

    return [c.to_dict() for c in parsed]


def resolve_channels_from_catalog(catalog_model: Model) -> list[ModelChannel]:
    """从目录模型 parameters.channels 解析有序通道；缺省仅本模型主通道。"""
    catalog_name = catalog_model.name
    params = catalog_model.parameters if isinstance(catalog_model.parameters, dict) else {}
    raw = params.get("channels")
    channels: list[ModelChannel] = []
    if isinstance(raw, list):
        for entry in raw:
            ch = _parse_channel_entry(entry, catalog_name=catalog_name)
            if ch is not None:
                channels.append(ch)

    if not channels:
        return [ModelChannel(role="primary", model_name=catalog_name, enabled=True)]

    # 保证至少有一条 primary
    if not any(c.role == "primary" for c in channels):
        channels.insert(0, ModelChannel(role="primary", model_name=catalog_name, enabled=True))

    # 主在前、副在后；仅 enabled
    ordered = sorted(channels, key=lambda c: 0 if c.role == "primary" else 1)
    return [c for c in ordered if c.enabled]


def error_code_from_exc(exc: BaseException) -> str:
    """将异常映射为可与 onErrors 比对的稳定码。"""
    if isinstance(exc, UpstreamError):
        return str(exc.code or "UPSTREAM_ERROR").strip() or "UPSTREAM_ERROR"
    if isinstance(exc, httpx.HTTPStatusError):
        return "UPSTREAM_HTTP_ERROR"
    if isinstance(exc, ProviderNotImplementedError):
        return "NOT_CONFIGURED"
    msg = str(exc).lower()
    if "not configured" in msg or "api key not configured" in msg or "密钥" in msg:
        return "NOT_CONFIGURED"
    return "UPSTREAM_ERROR"


def can_failover_to_next(
    exc: BaseException,
    *,
    current: ModelChannel,
    has_provider_task_id: bool,
    has_next: bool,
) -> bool:
    """当前通道失败后是否允许立刻试下一通道（支持多跳兜底）。

    已有 provider_task_id 时禁止（§1.7/§1.9），避免重复 POST 上游任务；
    仅当前通道 onErrors 白名单内的错误可切换。主/副通道均可向后兜底，
    以支持「后台配置主模型自身主副通道 + 副模型通道」串联的多跳 failover。
    """
    if not has_next:
        return False
    if has_provider_task_id:
        return False
    code = error_code_from_exc(exc)
    return code in set(current.on_errors)


def merge_channel_into_trace(
    job: GenerationJob,
    *,
    catalog_name: str,
    channel: ModelChannel,
    failover_from: str | None = None,
    failover_reason: str | None = None,
) -> None:
    """写入 trace_json.channel，供管理端区分主/副通道。"""
    base = job.trace_json if isinstance(job.trace_json, dict) else {}
    effective_spec = get_model_spec(channel.model_name)
    patch = {
        "channel": {
            "catalogModel": catalog_name,
            "requestedRole": "primary",
            "effectiveRole": channel.role,
            "effectiveModelName": channel.model_name,
            "effectiveProvider": (effective_spec.provider if effective_spec else None)
            or job.provider,
            "effectiveUpstreamModel": (effective_spec.upstream_model if effective_spec else "")
            or "",
            "label": channel.label or "",
            "failoverFrom": failover_from,
            "failoverReason": failover_reason,
            "updatedAt": cst_iso_now(),
        }
    }
    base.update(patch)
    job.trace_json = base
    if effective_spec and effective_spec.provider:
        job.provider = effective_spec.provider


def append_channel_event(
    job: GenerationJob,
    *,
    event: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """向 trace_json.events 追加通道相关事件。"""
    base = job.trace_json if isinstance(job.trace_json, dict) else {}
    events = list(base.get("events") or []) if isinstance(base.get("events"), list) else []
    events.append({"event": event, "at": cst_iso_now(), **(detail or {})})
    base["events"] = events
    job.trace_json = base


def channel_summary_from_job(job: GenerationJob) -> dict[str, Any] | None:
    """从 job.trace_json 提取通道摘要（管理端列表用）。"""
    trace = job.trace_json if isinstance(job.trace_json, dict) else {}
    channel = trace.get("channel")
    if not isinstance(channel, dict):
        return None
    role = str(channel.get("effectiveRole") or "").strip()
    if not role:
        return None
    return {
        "role": role,
        "model_name": channel.get("effectiveModelName"),
        "provider": channel.get("effectiveProvider"),
        "upstream_model": channel.get("effectiveUpstreamModel"),
        "failover_from": channel.get("failoverFrom"),
        "failover_reason": channel.get("failoverReason"),
        "label": channel.get("label") or "",
    }
