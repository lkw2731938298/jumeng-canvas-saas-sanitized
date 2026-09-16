"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { listAdminCreditPending, runAdminCreditReconcile } from "@/lib/api/admin";
import { formatJobDisplayId } from "@/lib/admin/displayIds";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { jobStatusLabel } from "@/lib/admin/jobLabels";

export default function AdminCreditsPage() {
  const queryClient = useQueryClient();

  const { data: pending = [], isLoading, refetch, isFetching } = useQuery({
    queryKey: ["admin", "credits", "pending"],
    queryFn: () => listAdminCreditPending(100),
    refetchInterval: 30_000,
  });

  const reconcileMutation = useMutation({
    mutationFn: runAdminCreditReconcile,
    onSuccess: (result) => {
      toast.success(
        `对账完成：提交 ${result.committed} · 释放 ${result.released} · 超时失败 ${result.failedStale}`
      );
      void queryClient.invalidateQueries({ queryKey: ["admin", "credits"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "jobs"] });
    },
    onError: () => toast.error("对账失败"),
  });

  return (
    <div>
      <AdminHeader
        title="算力对账"
        description="查看预扣/待提交/待释放的生成任务，并手动触发后台 reconcile。"
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          {isFetching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          刷新列表
        </Button>
        <Button
          size="sm"
          onClick={() => reconcileMutation.mutate()}
          disabled={reconcileMutation.isPending}
        >
          {reconcileMutation.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : null}
          立即对账
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : pending.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无待处理算力任务</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[720px] text-sm">
            <thead className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">提交号</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">算力状态</th>
                <th className="px-4 py-3 font-medium">算力</th>
                <th className="px-4 py-3 font-medium">模型</th>
                <th className="px-4 py-3 font-medium">创建时间</th>
              </tr>
            </thead>
            <tbody>
              {pending.map((job) => (
                <tr key={job.id} className="border-b border-border/60 last:border-b-0">
                  <td className="px-4 py-3 font-mono text-xs">{formatJobDisplayId(job)}</td>
                  <td className="px-4 py-3">
                    <Badge variant="outline">{jobStatusLabel(job.status)}</Badge>
                  </td>
                  <td className="px-4 py-3">{job.creditStatus ?? "—"}</td>
                  <td className="px-4 py-3 tabular-nums">{job.creditCost}</td>
                  <td className="px-4 py-3">{job.model ?? "—"}</td>
                  <td className="px-4 py-3 text-muted-foreground">{formatDateTimeCN(job.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
