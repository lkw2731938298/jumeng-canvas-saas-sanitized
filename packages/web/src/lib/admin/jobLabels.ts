export const JOB_STATUS_LABEL: Record<string, string> = {
  pending: "等待中",
  running: "运行中",
  polling: "轮询中",
  abnormal: "异常",
  succeeded: "成功",
  failed: "失败",
};

export const ANOMALY_REASON_LABEL: Record<string, string> = {
  STALE_RUNNING: "长时间运行中",
  STALE_POLLING: "长时间轮询上游",
  CREDIT_MISMATCH: "算力状态不一致",
  UPSTREAM_UNPARSEABLE: "上游响应无法解析",
};

export const JOB_LANE_LABEL: Record<string, string> = {
  image: "图片",
  video: "视频",
  text_llm: "文本",
  audio: "音频",
};

export const JOB_CREDIT_STATUS_LABEL: Record<string, string> = {
  skipped: "免扣费",
  reserved: "已预扣",
  committed: "已结算",
  released: "已退还",
  commit_pending: "待结算",
  release_pending: "待退还",
};

export const ANOMALY_REASON_OPTIONS = [
  "STALE_RUNNING",
  "STALE_POLLING",
  "CREDIT_MISMATCH",
  "UPSTREAM_UNPARSEABLE",
] as const;

export const ADMIN_ACTION_LABEL: Record<string, string> = {
  sync_upstream: "同步上游",
  force_succeed: "标为成功",
  force_fail: "标为失败",
  requeue: "重新入队",
  reconcile_credits: "同步算力",
};

export const JOB_STATUS_OPTIONS = ["pending", "running", "polling", "abnormal", "succeeded", "failed"] as const;
export const JOB_LANE_OPTIONS = ["image", "video", "text_llm", "audio"] as const;

export function jobStatusLabel(status: string): string {
  return JOB_STATUS_LABEL[status] ?? status;
}

export function anomalyReasonLabel(reason: string | undefined | null): string {
  if (!reason) return "";
  return ANOMALY_REASON_LABEL[reason] ?? reason;
}

export function jobLaneLabel(lane: string): string {
  return JOB_LANE_LABEL[lane] ?? lane;
}

export function adminActionLabel(action: string | undefined | null): string {
  if (!action) return "";
  return ADMIN_ACTION_LABEL[action] ?? action;
}
