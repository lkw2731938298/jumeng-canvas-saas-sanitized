"""Agent 上下文注入分预算：各片段硬顶，避免规则/技能/目录/历史/tool 互抢窗口。

字符近似作 token 预算（中文约 1 字≈1 token）；本轮画布目录永不 compact，仅截断并标注。
"""

from __future__ import annotations

# —— 分片上限（字符）——
BUDGET_SYSTEM_RULES = 12000
BUDGET_SKILL_APPENDIX = 14000
BUDGET_OBSERVE_CATALOG = 8000
BUDGET_OBSERVE_FOCUSED = 4000
BUDGET_OBSERVE_TOOLS = 6000
BUDGET_OBSERVE_CAPS = 3000
BUDGET_OBSERVE_TOTAL = 18000
BUDGET_TOOL_RESULT = 12000
BUDGET_HISTORY_RECENT = 4000
BUDGET_HISTORY_OLDER = 2500
BUDGET_HISTORY_MEMO = 4000
BUDGET_USER_TURN_EXTRA = 6000  # 点名工具/附件/$技能提示


def clip_budget(
    text: str,
    max_chars: int,
    *,
    label: str = "",
) -> str:
    """超限截断并可选加标签说明。"""
    raw = text or ""
    limit = max(64, int(max_chars))
    if len(raw) <= limit:
        return raw
    keep = limit - 48
    if keep < 32:
        keep = limit
    head = raw[:keep].rstrip()
    tag = f"…【{label or '片段'}已截断，原长 {len(raw)} 字】"
    return head + tag


def apply_observe_budgets(
    *,
    catalog: str,
    focused: str,
    tools: str,
    caps: str,
    heading: str = "",
) -> str:
    """拼观察块并套分片预算；总长仍超则优先保目录与焦点。"""
    cat = clip_budget(catalog, BUDGET_OBSERVE_CATALOG, label="画布目录")
    foc = clip_budget(focused, BUDGET_OBSERVE_FOCUSED, label="焦点内容")
    tool = clip_budget(tools, BUDGET_OBSERVE_TOOLS, label="工具速查")
    cap = clip_budget(caps, BUDGET_OBSERVE_CAPS, label="模型能力")
    head = f"{heading.strip()}\n" if heading.strip() else ""
    body = (
        f"{head}"
        f"【画布目录】\n{cat}\n\n"
        f"【焦点内容】\n{foc}\n\n"
        f"【画布工具速查】\n{tool}\n\n"
        f"【本轮模型能力】\n{cap}"
    )
    return clip_budget(body, BUDGET_OBSERVE_TOTAL, label="本轮观察")
