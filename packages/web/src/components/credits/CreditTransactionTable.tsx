"use client";

import Link from "next/link";
import { Loader2 } from "lucide-react";
import { creditSourceLabel, formatUserFacingCreditReason } from "@/lib/credits/creditTransactionLabels";
import { userFacingModelLabel } from "@/lib/credits/userFacingModelLabel";
import { formatCreditTypeLabel } from "@/lib/api/credits";
import { normalizeUserNo } from "@/lib/admin/displayIds";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { cn } from "@/lib/utils";

export interface CreditTransactionRow {
  id: string;
  delta: number;
  balanceAfter: number;
  source: string;
  sourceLabel?: string;
  creditType?: string;
  modelName?: string;
  /** 模型目录展示名；优先于内部 modelName 显示 */
  modelDisplayName?: string;
  jobId?: string;
  reason?: string;
  createdAt: string;
  expiresAt?: string | null;
  expiresLabel?: string | null;
  userId?: string;
  userNo?: string;
  userDisplayName?: string;
  userPhone?: string;
}

interface CreditTransactionTableProps {
  items: CreditTransactionRow[];
  loading?: boolean;
  emptyText?: string;
  showUser?: boolean;
  showId?: boolean;
  /** 个人中心深色玻璃卡片：完整展示备注/模型/任务号，不截断 */
  variant?: "default" | "account";
  className?: string;
  jobLinkPrefix?: string;
}

export function CreditTransactionTable({
  items,
  loading = false,
  emptyText = "暂无算力变动记录",
  showUser = false,
  showId = true,
  variant = "default",
  className,
  jobLinkPrefix,
}: CreditTransactionTableProps) {
  const isAccount = variant === "account";

  if (loading) {
    return (
      <p className={cn("text-sm", isAccount ? "text-white/50" : "text-muted-foreground")}>
        加载流水…
      </p>
    );
  }

  if (!items.length) {
    return (
      <p
        className={cn(
          "rounded-lg border border-dashed px-4 py-4 text-center text-sm",
          isAccount
            ? "border-white/15 text-white/45"
            : "border-border text-muted-foreground"
        )}
      >
        {emptyText}
      </p>
    );
  }

  const thClass = isAccount
    ? "px-3 py-2.5 font-medium whitespace-nowrap text-white/55"
    : "px-3 py-2 font-medium";
  const tdClass = isAccount ? "px-3 py-2.5 align-top text-white/85" : "px-3 py-2 align-top";
  const mutedClass = isAccount ? "text-white/50" : "text-muted-foreground";

  return (
    <div
      className={cn(
        "overflow-x-auto rounded-lg border",
        isAccount ? "border-white/10" : "border-border",
        className
      )}
    >
      {/* 最小宽度保证列完整可见；窄屏横向滚动，不截断备注 */}
      <table className={cn("w-full text-left text-sm", isAccount ? "min-w-[820px]" : "w-full")}>
        <thead
          className={cn(
            "border-b",
            isAccount ? "border-white/10 bg-white/[0.04]" : "border-border bg-muted/40 text-muted-foreground"
          )}
        >
          <tr>
            {showId ? <th className={thClass}>记录 ID</th> : null}
            <th className={thClass}>时间</th>
            {showUser ? <th className={thClass}>用户</th> : null}
            <th className={thClass}>类型</th>
            <th className={thClass}>变动</th>
            <th className={thClass}>余额</th>
            <th className={thClass}>算力类型</th>
            <th className={thClass}>过期时间</th>
            <th className={cn(thClass, "min-w-[12rem]")}>备注</th>
          </tr>
        </thead>
        <tbody>
          {items.map((tx) => (
            <tr
              key={tx.id}
              className={cn(
                "border-b",
                isAccount ? "border-white/[0.06]" : "border-border/60"
              )}
            >
              {showId ? (
                <td className={cn(tdClass, "font-mono text-xs", mutedClass)}>
                  <span className="break-all">{tx.id}</span>
                </td>
              ) : null}
              <td className={cn(tdClass, "whitespace-nowrap")}>{formatDateTimeCN(tx.createdAt)}</td>
              {showUser ? (
                <td className={tdClass}>
                  <div className="break-words">{tx.userDisplayName || "—"}</div>
                  {normalizeUserNo(tx.userNo) ? (
                    <div className={cn("font-mono text-xs", mutedClass)}>
                      {normalizeUserNo(tx.userNo)}
                    </div>
                  ) : tx.userPhone ? (
                    <div className={cn("text-xs", mutedClass)}>{tx.userPhone}</div>
                  ) : null}
                </td>
              ) : null}
              <td className={cn(tdClass, "whitespace-nowrap")}>
                {creditSourceLabel(tx.source, tx.sourceLabel)}
              </td>
              <td
                className={cn(
                  tdClass,
                  "tabular-nums font-medium whitespace-nowrap",
                  tx.delta >= 0
                    ? isAccount
                      ? "text-emerald-300"
                      : "text-emerald-600"
                    : isAccount
                      ? "text-red-300"
                      : "text-destructive"
                )}
              >
                {tx.delta >= 0 ? `+${tx.delta}` : tx.delta}
              </td>
              <td className={cn(tdClass, "tabular-nums whitespace-nowrap")}>{tx.balanceAfter}</td>
              <td className={cn(tdClass, mutedClass)}>
                <div className="whitespace-nowrap">
                  {tx.creditType ? formatCreditTypeLabel(tx.creditType) : "—"}
                </div>
                {/* 用户侧不展示内部模型标识；管理端仍显示便于对账 */}
                {!isAccount && userFacingModelLabel({
                  modelDisplayName: tx.modelDisplayName,
                  modelName: tx.modelName,
                }) ? (
                  <div
                    className={cn("mt-0.5 break-words text-xs", mutedClass)}
                    title={tx.modelName && tx.modelDisplayName && tx.modelName !== tx.modelDisplayName ? tx.modelName : undefined}
                  >
                    {userFacingModelLabel({
                      modelDisplayName: tx.modelDisplayName,
                      modelName: tx.modelName,
                    })}
                  </div>
                ) : null}
              </td>
              <td className={cn(tdClass, "whitespace-nowrap", mutedClass)}>
                {tx.expiresLabel
                  ? tx.expiresLabel
                  : tx.expiresAt
                    ? formatDateTimeCN(tx.expiresAt)
                    : "—"}
              </td>
              <td className={cn(tdClass, mutedClass)}>
                {/* 用户侧备注去掉模型标识；管理端保留原文 */}
                <div className="whitespace-pre-wrap break-words">
                  {isAccount ? formatUserFacingCreditReason(tx.reason) : tx.reason || "—"}
                </div>
                {tx.jobId ? (
                  jobLinkPrefix ? (
                    <Link
                      href={`${jobLinkPrefix}${encodeURIComponent(tx.jobId)}`}
                      className={cn(
                        "mt-1 inline-block font-mono text-xs hover:underline",
                        isAccount ? "text-sky-300" : "text-primary"
                      )}
                    >
                      任务 #{tx.jobId}
                    </Link>
                  ) : (
                    <div className={cn("mt-1 font-mono text-xs", mutedClass)}>任务 #{tx.jobId}</div>
                  )
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CreditTransactionTableSkeleton() {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      加载算力变动记录…
    </div>
  );
}
