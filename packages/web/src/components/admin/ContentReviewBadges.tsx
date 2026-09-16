/**
 * Skill / 工作流发布管理：展示态徽章与审核动作文案。
 */
import type { AdminReviewHistoryItem } from "@/lib/api/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";

export const DISPLAY_STATUS_FILTERS = [
  { id: "all", label: "全部" },
  { id: "listed", label: "展示中" },
  { id: "pending", label: "待审核" },
  { id: "rejected", label: "已驳回" },
  { id: "unlisted", label: "已下架" },
] as const;

export const SKILL_DISPLAY_STATUS_FILTERS = [
  ...DISPLAY_STATUS_FILTERS,
  { id: "none", label: "未提交/平台" },
] as const;

const DISPLAY_BADGE: Record<string, { label: string; className: string }> = {
  listed: {
    label: "展示中",
    className: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  },
  unlisted: {
    label: "已下架",
    className: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
  },
  pending: {
    label: "待审核",
    className: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  },
  rejected: {
    label: "已驳回",
    className: "bg-red-500/15 text-red-700 dark:text-red-300",
  },
  none: {
    label: "未提交",
    className: "bg-muted text-muted-foreground",
  },
};

const ACTION_LABEL: Record<string, string> = {
  approve: "通过",
  reject: "驳回",
  unpublish: "下架",
};

export function DisplayStatusBadge({ status }: { status?: string | null }) {
  const key = status || "none";
  const meta = DISPLAY_BADGE[key] || DISPLAY_BADGE.none;
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${meta.className}`}
    >
      {meta.label}
    </span>
  );
}

export function reviewActionLabel(action?: string | null): string {
  if (!action) return "—";
  return ACTION_LABEL[action] || action;
}

export function ReviewHistoryPanel({
  note,
  reviewedAt,
  reviewedByName,
  history,
}: {
  note?: string | null;
  reviewedAt?: string | null;
  reviewedByName?: string | null;
  history?: AdminReviewHistoryItem[] | null;
}) {
  const items = [...(history || [])].reverse();
  return (
    <div className="space-y-2 text-xs text-muted-foreground">
      <div>
        <span className="text-foreground">最近审核：</span>
        {reviewedAt ? formatDateTimeCN(reviewedAt) : "—"}
        {reviewedByName ? ` · ${reviewedByName}` : ""}
      </div>
      {note ? (
        <div>
          <span className="text-foreground">备注：</span>
          {note}
        </div>
      ) : null}
      {items.length > 0 ? (
        <ul className="max-h-40 space-y-1.5 overflow-y-auto border-t border-border pt-2">
          {items.map((h, idx) => (
            <li key={`${h.at || ""}-${idx}`} className="leading-relaxed">
              <span className="text-foreground">{reviewActionLabel(h.action)}</span>
              {" · "}
              {h.at ? formatDateTimeCN(h.at) : "—"}
              {h.byName ? ` · ${h.byName}` : ""}
              {h.note ? (
                <div className="pl-0 text-muted-foreground">备注：{h.note}</div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p>暂无审核历史</p>
      )}
    </div>
  );
}
