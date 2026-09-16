"""会话可见计划 update_plan：步骤状态投影到 brief.runtimePlan（非状态机驱动）。"""

from __future__ import annotations

import json
from typing import Any

_ALLOWED_STATUS = frozenset({"pending", "in_progress", "completed"})


def normalize_runtime_plan(raw: Any) -> dict[str, Any] | None:
    """校验并归一化 plan；非法返回 None。"""
    if not isinstance(raw, dict):
        return None
    steps_in = raw.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        return None
    steps: list[dict[str, str]] = []
    in_progress_count = 0
    for item in steps_in[:20]:
        if isinstance(item, str):
            title = item.strip()[:80]
            status = "pending"
        elif isinstance(item, dict):
            title = str(item.get("step") or item.get("title") or item.get("label") or "").strip()[:80]
            status = str(item.get("status") or "pending").strip().lower()
        else:
            continue
        if not title:
            continue
        if status not in _ALLOWED_STATUS:
            status = "pending"
        if status == "in_progress":
            in_progress_count += 1
            # 同一时刻仅一步 in_progress：多余的降为 pending
            if in_progress_count > 1:
                status = "pending"
                in_progress_count -= 1
        steps.append({"step": title, "status": status})
    if not steps:
        return None
    explanation = str(raw.get("explanation") or raw.get("note") or "").strip()[:400]
    out: dict[str, Any] = {"steps": steps}
    if explanation:
        out["explanation"] = explanation
    return out


def apply_update_plan_args(args: dict[str, Any]) -> dict[str, Any] | None:
    """从工具参数构造 runtimePlan。"""
    steps = args.get("steps")
    if steps is None and isinstance(args.get("plan"), list):
        steps = args.get("plan")
    return normalize_runtime_plan(
        {
            "steps": steps,
            "explanation": args.get("explanation") or args.get("note"),
        }
    )


def format_update_plan_result(plan: dict[str, Any]) -> str:
    return json.dumps(
        {"ok": True, "runtimePlan": plan, "note": "计划已更新，仅供进度展示，勿当状态机"},
        ensure_ascii=False,
    )
