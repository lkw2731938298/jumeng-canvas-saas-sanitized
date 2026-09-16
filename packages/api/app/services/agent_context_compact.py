"""会话上下文 compaction：超阈值时用备忘替换早期原文，避免硬截断丢目标/nodeId。

本轮画布目录不经本模块；历史消息只压缩，不原地改 DB 审计全文。
"""

from __future__ import annotations

import re
from typing import Any

# 拼进 LLM 的历史总字符超过此值则 compact（截断前先估）
_COMPACT_TRIGGER_CHARS = 48000
_MEMO_MAX_CHARS = 3500
_KEEP_RECENT = 4
_GOAL_MAX = 6
_NODE_MAX = 24

_NODE_ID_RE = re.compile(r"nodeId[=:]\s*([0-9a-zA-Z_-]{1,128})")
_NODE_MARK_RE = re.compile(
    r"\[节点:([^\]|]*)\|nodeId=([0-9a-zA-Z_-]{1,128})"
)
_DONE_HINT_RE = re.compile(
    r"已提交生成|已生成|故事板|确认生成|ask_user|待确认|出片|宣传片|爆款|出海"
)


def build_context_memo(
    role_contents: list[tuple[str, str]],
    *,
    previous: str = "",
    skill_hint: str = "",
) -> str:
    """从历史抽会话备忘：目标、nodeId、完成/待确认线索。不调用 LLM。"""
    goals: list[str] = []
    node_ids: list[str] = []
    seen_nodes: set[str] = set()
    hints: list[str] = []

    for role, content in role_contents:
        text = (content or "").strip()
        if not text:
            continue
        for m in _NODE_MARK_RE.finditer(text):
            nid = m.group(2)
            if nid not in seen_nodes and len(node_ids) < _NODE_MAX:
                seen_nodes.add(nid)
                label = (m.group(1) or "").strip()
                node_ids.append(f"{label}:{nid}" if label else nid)
        for m in _NODE_ID_RE.finditer(text):
            nid = m.group(1)
            if nid not in seen_nodes and len(node_ids) < _NODE_MAX:
                seen_nodes.add(nid)
                node_ids.append(nid)
        if role == "user" and len(goals) < _GOAL_MAX and len(text) <= 240:
            # 去掉附件/节点标记后的短目标
            slim = _NODE_MARK_RE.sub("", text)
            slim = re.sub(r"\[附件:[^\]]+\]", "", slim).strip()
            if slim and slim not in goals:
                goals.append(slim[:160])
        if _DONE_HINT_RE.search(text) and len(hints) < 8:
            snippet = text.replace("\n", " ")[:100]
            if snippet not in hints:
                hints.append(snippet)

    lines = ["【会话备忘】（早期原文已压缩；以最近消息与本轮画布目录为准）"]
    if skill_hint.strip():
        lines.append(f"- 绑定技能：{skill_hint.strip()}")
    if previous.strip():
        # 保留上一版备忘里「待确认」类短句
        for line in previous.splitlines():
            s = line.strip()
            if s.startswith("- 待确认") or s.startswith("- 目标"):
                if s not in lines and len(lines) < 20:
                    lines.append(s)
    if goals:
        lines.append("- 近期用户目标：")
        for g in goals[-_GOAL_MAX:]:
            lines.append(f"  · {g}")
    if node_ids:
        lines.append("- 提到的 nodeId：" + "、".join(node_ids[:_NODE_MAX]))
    if hints:
        lines.append("- 线索：")
        for h in hints[-6:]:
            lines.append(f"  · {h}")
    lines.append("- 待确认：出视频须用户说「确认生成」等短语（见规则硬闸）")
    memo = "\n".join(lines).strip()
    return memo[:_MEMO_MAX_CHARS]


def should_compact_history(role_contents: list[tuple[str, str]]) -> bool:
    total = sum(len(c or "") for _, c in role_contents)
    return total >= _COMPACT_TRIGGER_CHARS or len(role_contents) > 36


def compact_role_contents(
    role_contents: list[tuple[str, str]],
    *,
    previous_memo: str = "",
    skill_hint: str = "",
    keep_recent: int = _KEEP_RECENT,
) -> tuple[list[tuple[str, str]], str]:
    """超阈值时：备忘 + 最近 K 条；返回 (压缩后列表, 新备忘)。"""
    if not should_compact_history(role_contents):
        return role_contents, (previous_memo or "").strip()

    memo = build_context_memo(
        role_contents,
        previous=previous_memo,
        skill_hint=skill_hint,
    )
    keep = max(1, int(keep_recent))
    recent = role_contents[-keep:] if len(role_contents) > keep else list(role_contents)
    compacted: list[tuple[str, str]] = [("user", memo)]
    compacted.extend(recent)
    return compacted, memo
