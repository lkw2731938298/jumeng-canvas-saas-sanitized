"""会话 runtime 事件：供轮询 / SSE 展示（thinking、tool、plan、ask_user）。"""

from __future__ import annotations

from typing import Any

from ..core.datetime_util import now_cst_naive, to_cst_iso

_MAX_EVENTS = 40
_ALLOWED_KINDS = frozenset(
    {
        "thinking",
        "tool_started",
        "tool_finished",
        "canvas_op_applied",
        "plan_updated",
        "ask_user",
        "steer_applied",
        "hard_gate",
    }
)


def append_runtime_event(
    brief: dict[str, Any],
    *,
    kind: str,
    message: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """向 brief.runtimeEvents 追加一条；返回该事件。原地改 brief。"""
    k = str(kind or "").strip()
    if k not in _ALLOWED_KINDS:
        k = "thinking"
    events = brief.get("runtimeEvents")
    if not isinstance(events, list):
        events = []
    next_id = 1
    if events:
        try:
            next_id = int(events[-1].get("id") or 0) + 1
        except (TypeError, ValueError):
            next_id = len(events) + 1
    ev: dict[str, Any] = {
        "id": next_id,
        "kind": k,
        "message": str(message or "").strip()[:400],
        "at": to_cst_iso(now_cst_naive()),
    }
    if isinstance(data, dict) and data:
        # 只保留浅层可 JSON 字段
        clean: dict[str, Any] = {}
        for key, val in list(data.items())[:12]:
            if isinstance(val, (str, int, float, bool)) or val is None:
                clean[str(key)[:64]] = val
            elif isinstance(val, list):
                clean[str(key)[:64]] = [
                    x for x in val[:20] if isinstance(x, (str, int, float, bool))
                ]
        if clean:
            ev["data"] = clean
    events.append(ev)
    brief["runtimeEvents"] = events[-_MAX_EVENTS:]
    brief["runtimeEventCursor"] = next_id
    return ev


def list_runtime_events_since(
    brief: dict[str, Any] | None,
    *,
    after_id: int = 0,
) -> list[dict[str, Any]]:
    if not isinstance(brief, dict):
        return []
    events = brief.get("runtimeEvents")
    if not isinstance(events, list):
        return []
    out: list[dict[str, Any]] = []
    for item in events:
        if not isinstance(item, dict):
            continue
        try:
            eid = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if eid > int(after_id or 0):
            out.append(item)
    return out


def consume_runtime_steer(brief: dict[str, Any]) -> str | None:
    """取出并清空 brief.runtimeSteer（飞行中追加的用户指令）。"""
    raw = brief.pop("runtimeSteer", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:2000]
    if isinstance(raw, dict):
        msg = str(raw.get("message") or raw.get("text") or "").strip()
        return msg[:2000] if msg else None
    if isinstance(raw, list) and raw:
        # 合并多条
        parts = [
            str(x.get("message") if isinstance(x, dict) else x).strip()
            for x in raw[:5]
        ]
        text = "\n".join(p for p in parts if p)
        return text[:2000] if text else None
    return None
