"""Credit transaction source constants and Chinese labels."""

from __future__ import annotations

CREDIT_TX_SOURCE_RECHARGE = "recharge"
CREDIT_TX_SOURCE_ADMIN_ADJUST = "admin_adjust"
CREDIT_TX_SOURCE_ACTIVITY_CLAIM = "activity_claim"
CREDIT_TX_SOURCE_SUBSCRIPTION_GRANT = "subscription_grant"
CREDIT_TX_SOURCE_JOB_CONSUME = "job_consume"
CREDIT_TX_SOURCE_JOB_REFUND = "job_refund"
CREDIT_TX_SOURCE_AGENT_ORCHESTRATE = "agent_orchestrate"
CREDIT_TX_SOURCE_AGENT_ORCHESTRATE_REFUND = "agent_orchestrate_refund"
CREDIT_TX_SOURCE_SKILL_AI_FILL = "skill_ai_fill"
CREDIT_TX_SOURCE_SKILL_AI_FILL_REFUND = "skill_ai_fill_refund"
CREDIT_TX_SOURCE_MIGRATION = "migration"

CREDIT_TX_SOURCE_LABELS: dict[str, str] = {
    CREDIT_TX_SOURCE_RECHARGE: "用户充值",
    CREDIT_TX_SOURCE_ADMIN_ADJUST: "管理员调账",
    CREDIT_TX_SOURCE_ACTIVITY_CLAIM: "活动领取",
    CREDIT_TX_SOURCE_SUBSCRIPTION_GRANT: "会员赠送",
    CREDIT_TX_SOURCE_JOB_CONSUME: "生成任务扣费",
    CREDIT_TX_SOURCE_JOB_REFUND: "任务失败退还",
    CREDIT_TX_SOURCE_AGENT_ORCHESTRATE: "Agent 编排扣费",
    CREDIT_TX_SOURCE_AGENT_ORCHESTRATE_REFUND: "Agent 编排退还",
    CREDIT_TX_SOURCE_SKILL_AI_FILL: "Skill AI 填充扣费",
    CREDIT_TX_SOURCE_SKILL_AI_FILL_REFUND: "Skill AI 填充退还",
    CREDIT_TX_SOURCE_MIGRATION: "历史迁移",
}


def credit_tx_source_label(source: str) -> str:
    return CREDIT_TX_SOURCE_LABELS.get(source, source)
