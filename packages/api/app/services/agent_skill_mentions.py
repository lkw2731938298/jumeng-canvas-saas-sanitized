"""用户消息里的 $skill 显式点名（对齐 Codex `$name`）。"""

from __future__ import annotations

import re

# $product-cinematic-commercial 或 $viral_remake
_DOLLAR_SKILL_RE = re.compile(
    r"(?<![A-Za-z0-9_])\$([a-z][a-z0-9_-]{0,63})",
    re.I,
)


def parse_skill_dollar_mentions(text: str) -> list[str]:
    """从用户原文提取 $name，去重保序（最多 3 个）。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _DOLLAR_SKILL_RE.finditer(text or ""):
        name = (m.group(1) or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= 3:
            break
    return out


def format_skill_mention_hint(names: list[str]) -> str:
    """注入本轮 user 块：强制模型 load_skill。"""
    if not names:
        return ""
    joined = "、".join(f"${n}" for n in names)
    return (
        f"【本轮点名技能】{joined}\n"
        "必须对本轮点名的技能调用 load_skill（已绑定并预载过同技能则可跳过）；"
        "references 用 load_skill_file。禁止只聊天不加载配方。"
    )
