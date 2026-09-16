"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { AdminHeader } from "@/components/admin/AdminShell";
import { CreditTransactionTable } from "@/components/credits/CreditTransactionTable";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  cstDateEndExclusiveToUtcIso,
  cstDateStartToUtcIso,
} from "@/lib/admin/adminJobQuery";
import { CREDIT_TX_SOURCE_OPTIONS } from "@/lib/credits/creditTransactionLabels";
import { listAdminCreditTransactions } from "@/lib/api/admin";

export default function AdminCreditTransactionsPage() {
  const [page, setPage] = useState(1);
  const [userId, setUserId] = useState("");
  const [source, setSource] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const pageSize = 20;

  const createdFrom = dateFrom ? cstDateStartToUtcIso(dateFrom) : undefined;
  const createdTo = dateTo ? cstDateEndExclusiveToUtcIso(dateTo) : undefined;

  const filterParams = useMemo(
    () => ({
      page,
      pageSize,
      userId: userId.trim() || undefined,
      source: source || undefined,
      createdFrom,
      createdTo,
    }),
    [page, pageSize, userId, source, createdFrom, createdTo]
  );

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin", "credit-transactions", filterParams],
    queryFn: () => listAdminCreditTransactions(filterParams),
  });

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));

  return (
    <div>
      <AdminHeader
        title="算力变动记录"
        description="全站算力入账、扣费与退还流水，含充值、活动、会员、管理员调账与生成任务。"
      />

      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-muted/20 p-4">
        <div className="w-[220px] space-y-1">
          <label className="text-xs text-muted-foreground">用户</label>
          <Input
            value={userId}
            onChange={(e) => {
              setUserId(e.target.value);
              setPage(1);
            }}
            placeholder="用户编号 / 手机号 / UUID"
          />
        </div>
        <div className="w-[180px] space-y-1">
          <label className="text-xs text-muted-foreground">变动类型</label>
          <Select
            value={source || "__all__"}
            onValueChange={(v) => {
              setSource(!v || v === "__all__" ? "" : v);
              setPage(1);
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部</SelectItem>
              {CREDIT_TX_SOURCE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="w-[150px] space-y-1">
          <label className="text-xs text-muted-foreground">开始日期</label>
          <Input
            type="date"
            value={dateFrom}
            onChange={(e) => {
              setDateFrom(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="w-[150px] space-y-1">
          <label className="text-xs text-muted-foreground">结束日期</label>
          <Input
            type="date"
            value={dateTo}
            onChange={(e) => {
              setDateTo(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setUserId("");
            setSource("");
            setDateFrom("");
            setDateTo("");
            setPage(1);
          }}
        >
          重置筛选
        </Button>
      </div>

      <div className="mb-3 flex items-center justify-between text-sm text-muted-foreground">
        <span>
          共 {data?.total ?? 0} 条
          {isFetching && !isLoading ? (
            <Loader2 className="ml-2 inline h-3.5 w-3.5 animate-spin" />
          ) : null}
        </span>
        <span>
          第 {page} / {totalPages} 页
        </span>
      </div>

      <CreditTransactionTable
        items={(data?.items ?? []).map((tx) => ({
          id: tx.id,
          delta: tx.delta,
          balanceAfter: tx.balanceAfter,
          source: tx.source,
          sourceLabel: tx.sourceLabel,
          creditType: tx.creditType,
          modelName: tx.modelName,
          modelDisplayName: tx.modelDisplayName || tx.modelName,
          jobId: tx.jobId,
          reason: tx.reason,
          createdAt: tx.createdAt,
          expiresAt: tx.expiresAt,
          expiresLabel: tx.expiresLabel,
          userId: tx.userId,
          userNo: tx.userNo,
          userDisplayName: tx.userDisplayName,
          userPhone: tx.userPhone,
        }))}
        loading={isLoading}
        showUser
        showId={false}
        jobLinkPrefix="/admin/jobs?job_id="
      />

      <div className="mt-4 flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={page <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          上一页
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          下一页
        </Button>
      </div>

      <p className="mt-6 text-xs text-muted-foreground">
        提示：可按用户编号、手机号或 UUID 筛选，或前往
        <Link href="/admin/users" className="mx-1 text-primary hover:underline">
          用户管理
        </Link>
        查看单用户详情。
      </p>
    </div>
  );
}
