/** Chinese labels for credit transaction `source` values. */

const CREDIT_TX_SOURCE_LABELS: Record<string, string> = {
  recharge: "用户充值",
  admin_adjust: "管理员调账",
  activity_claim: "活动领取",
  subscription_grant: "会员赠送",
  job_consume: "生成任务扣费",
  job_refund: "任务失败退还",
  migration: "历史迁移",
};

export function creditSourceLabel(source: string, sourceLabel?: string): string {
  if (sourceLabel?.trim()) return sourceLabel;
  return CREDIT_TX_SOURCE_LABELS[source] ?? source;
}

/**
 * 用户侧备注去掉内部模型标识（如 wan30_r2v），只保留扣费/退还与任务号。
 * 历史流水 reason 形如「生成任务扣费 · rh_minimax_hailuo_h3_r2v · 任务 12837」。
 */
export function formatUserFacingCreditReason(reason?: string | null): string {
  const text = (reason || "").trim();
  if (!text) return "—";
  const matched = text.match(
    /^(生成任务扣费|任务失败退还)\s*·\s*.+?\s*·\s*(任务\s+\d+)$/
  );
  if (matched) return `${matched[1]} · ${matched[2]}`;
  return text;
}

export const CREDIT_TX_SOURCE_OPTIONS = Object.entries(CREDIT_TX_SOURCE_LABELS).map(
  ([value, label]) => ({ value, label })
);
