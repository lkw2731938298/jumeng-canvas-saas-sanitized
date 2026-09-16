"""Agent 会话编排算力（轨 S）定价。

权威存 MySQL ``platform_settings.agent_skill_pricing``：

```
{
  version,
  sessionStart,          # 兼容旧字段；未配置 conversationTurn 时作回退
  conversationTurn,      # 一次对话（用户发一条并触发 AI）扣费
  skillAiFill,           # 创建 Skill「按类型 AI 填充」单次扣费
  skills: { <slug>: number },  # Skill 覆盖的单次对话价
  controllerModels: [modelId, ...]  # AI 操控可选模型白名单；空=目录内全部控制器
}
```
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.credit_amount import normalize_credit_amount
from .credit_flow import credits_enabled
from .credit_pricing import CreditBreakdownItem, CreditQuote
from .platform_settings import get_agent_skill_pricing_raw, set_agent_skill_pricing_raw

# 默认：一次对话算力（后台可改；0 在开启算力时视为未配置）
DEFAULT_CONVERSATION_TURN_CREDIT = 20.0
# 创建 Skill「按类型 AI 填充」默认算力
DEFAULT_SKILL_AI_FILL_CREDIT = 10.0


def normalize_agent_skill_pricing(raw: Any) -> dict[str, Any]:
    """规范化轨 S 定价（含按次对话价、Skill AI 填充价与控制器白名单；一位小数）。"""
    version = 1
    session_start = DEFAULT_CONVERSATION_TURN_CREDIT
    conversation_turn: float | None = None
    skill_ai_fill = DEFAULT_SKILL_AI_FILL_CREDIT
    skills: dict[str, float] = {}
    controller_models: list[str] = []
    if isinstance(raw, dict):
        try:
            version = max(1, int(raw.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        if raw.get("sessionStart") is not None:
            session_start = normalize_credit_amount(
                raw.get("sessionStart"), default=DEFAULT_CONVERSATION_TURN_CREDIT
            )
        if raw.get("conversationTurn") is not None:
            conversation_turn = normalize_credit_amount(raw.get("conversationTurn"), default=0)
        if raw.get("skillAiFill") is not None:
            skill_ai_fill = normalize_credit_amount(
                raw.get("skillAiFill"), default=DEFAULT_SKILL_AI_FILL_CREDIT
            )
        src = raw.get("skills") if isinstance(raw.get("skills"), dict) else {}
        for slug, cost in src.items():
            s = str(slug or "").strip()
            if not s:
                continue
            skills[s] = normalize_credit_amount(cost, default=0)
        raw_models = raw.get("controllerModels")
        if isinstance(raw_models, (list, tuple)):
            seen: set[str] = set()
            for mid in raw_models:
                m = str(mid or "").strip()
                if not m or m in seen:
                    continue
                seen.add(m)
                controller_models.append(m)

    # 未显式配置 conversationTurn 时回退 sessionStart，保持旧数据行为
    turn = (
        conversation_turn
        if conversation_turn is not None
        else session_start
    )
    return {
        "version": version,
        "sessionStart": session_start,
        "conversationTurn": turn,
        "skillAiFill": skill_ai_fill,
        "skills": skills,
        "controllerModels": controller_models,
    }


async def get_agent_skill_pricing(db: AsyncSession) -> dict[str, Any]:
    raw = await get_agent_skill_pricing_raw(db)
    return normalize_agent_skill_pricing(raw)


async def set_agent_skill_pricing(
    db: AsyncSession,
    *,
    session_start: float | None = None,
    conversation_turn: float | None = None,
    skill_ai_fill: float | None = None,
    skills: dict[str, float] | None = None,
    controller_models: Sequence[str] | None = None,
    bump_version: bool = True,
) -> dict[str, Any]:
    """保存轨 S 定价；默认递增 version。"""
    current = await get_agent_skill_pricing(db)
    next_start = normalize_credit_amount(current["sessionStart"], default=0)
    if session_start is not None:
        next_start = normalize_credit_amount(session_start, default=0)
    next_turn = normalize_credit_amount(current["conversationTurn"], default=0)
    if conversation_turn is not None:
        next_turn = normalize_credit_amount(conversation_turn, default=0)
        # 同步 sessionStart，兼容旧前端只读 sessionStart 的展示
        next_start = next_turn
    next_fill = normalize_credit_amount(current["skillAiFill"], default=0)
    if skill_ai_fill is not None:
        next_fill = normalize_credit_amount(skill_ai_fill, default=0)
    next_skills = dict(current["skills"])
    if skills is not None:
        next_skills = {}
        for slug, cost in skills.items():
            s = str(slug or "").strip()
            if s:
                next_skills[s] = normalize_credit_amount(cost, default=0)
    next_models = list(current["controllerModels"])
    if controller_models is not None:
        seen: set[str] = set()
        next_models = []
        for mid in controller_models:
            m = str(mid or "").strip()
            if not m or m in seen:
                continue
            seen.add(m)
            next_models.append(m)
    next_version = int(current["version"]) + (1 if bump_version else 0)
    if next_version < 1:
        next_version = 1
    payload = {
        "version": next_version,
        "sessionStart": next_start,
        "conversationTurn": next_turn,
        "skillAiFill": next_fill,
        "skills": next_skills,
        "controllerModels": next_models,
    }
    await set_agent_skill_pricing_raw(db, payload)
    return payload


def quote_agent_session_cost(
    *,
    skill_slug: str | None = None,
    pricing: dict[str, Any] | None = None,
) -> CreditQuote:
    """计算一次对话（轨 S）报价。有 Skill 覆盖价用覆盖价，否则用 conversationTurn。"""
    cfg = normalize_agent_skill_pricing(pricing)
    slug = (skill_slug or "").strip() or None
    if slug and slug in cfg["skills"]:
        total = normalize_credit_amount(cfg["skills"][slug], default=0)
        label = f"Skill 对话 · {slug}"
        item_id = slug
    else:
        total = normalize_credit_amount(cfg["conversationTurn"], default=0)
        label = "AI 操控 · 一次对话" if not slug else f"Skill 对话 · {slug}"
        item_id = "conversation_turn"
    return CreditQuote(
        model="agent_orchestrate",
        total=total,
        base=total,
        breakdown=[
            CreditBreakdownItem(
                group_id="agent_orchestrate",
                item_id=item_id,
                label=label,
                cost=total,
            )
        ],
        pricing_version=int(cfg["version"]),
        option_snapshot={
            "skillSlug": slug or "",
            "track": "S",
            "billing": "conversation_turn",
        },
    )


def quote_skill_ai_fill_cost(pricing: dict[str, Any] | None = None) -> CreditQuote:
    """创建 Skill「按类型 AI 填充」单次报价。"""
    cfg = normalize_agent_skill_pricing(pricing)
    total = normalize_credit_amount(cfg["skillAiFill"], default=0)
    return CreditQuote(
        model="skill_ai_fill",
        total=total,
        base=total,
        breakdown=[
            CreditBreakdownItem(
                group_id="skill_ai_fill",
                item_id="skill_ai_fill",
                label="创建 Skill · 按类型 AI 填充",
                cost=total,
            )
        ],
        pricing_version=int(cfg["version"]),
        option_snapshot={"billing": "skill_ai_fill"},
    )


def agent_skill_pricing_public(pricing: dict[str, Any] | None = None) -> dict[str, Any]:
    """前端展示用。"""
    cfg = normalize_agent_skill_pricing(pricing)
    return {
        "version": cfg["version"],
        "sessionStart": cfg["sessionStart"],
        "conversationTurn": cfg["conversationTurn"],
        "skillAiFill": cfg["skillAiFill"],
        "skills": cfg["skills"],
        "controllerModels": list(cfg["controllerModels"]),
        "creditsEnabled": credits_enabled(),
    }
