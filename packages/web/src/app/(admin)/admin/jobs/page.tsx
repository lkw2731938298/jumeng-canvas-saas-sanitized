"use client";

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Download, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { JobDetailDialog } from "@/components/admin/JobDetailDialog";
import { Badge } from "@/components/ui/badge";
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
import {
  ANOMALY_REASON_OPTIONS,
  JOB_LANE_OPTIONS,
  JOB_STATUS_OPTIONS,
  adminActionLabel,
  anomalyReasonLabel,
  jobLaneLabel,
  jobStatusLabel,
} from "@/lib/admin/jobLabels";
import { exportAdminJobs, listAdminJobs, syncAdminActiveJobs } from "@/lib/api/admin";
import { formatJobDisplayId, normalizeUserNo } from "@/lib/admin/displayIds";
import { usePersistedAdminQuery } from "@/lib/admin/usePersistedAdminQuery";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import type { AdminJob } from "@/types/admin";

const JOBS_QUERY_DEFAULTS = {
  status: "",
  lane: "",
  anomalyReason: "",
  hasAdminAction: false,
  userId: "",
  jobId: "",
  projectSearch: "",
  dateFrom: "",
  dateTo: "",
  page: 1,
};

function statusVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive";
  if (status === "abnormal") return "destructive";
  if (status === "succeeded") return "default";
  return "secondary";
}

function submitterLabel(job: AdminJob): string {
  const name = job.userDisplayName?.trim();
  const phone = job.userPhone?.trim();
  const userNo = normalizeUserNo(job.userNo);
  if (name && phone) return `${name} (${phone})`;
  if (name && userNo) return `${name} (${userNo})`;
  if (name) return name;
  if (phone) return phone;
  if (userNo) return userNo;
  return `${job.userId.slice(0, 8)}…`;
}

export default function AdminJobsPage() {
  const searchParams = useSearchParams();
  // 筛选条件写入 sessionStorage，切换选项卡后再回来时保留
  const [query, setQuery, resetQuery] = usePersistedAdminQuery(
    "canvas-admin-jobs-query-v1",
    JOBS_QUERY_DEFAULTS
  );
  const {
    status,
    lane,
    anomalyReason,
    hasAdminAction,
    userId,
    jobId,
    projectSearch,
    dateFrom,
    dateTo,
    page,
  } = query;
  const [detailJob, setDetailJob] = useState<AdminJob | null>(null);
  const [exporting, setExporting] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const pageSize = 20;

  useEffect(() => {
    const fromUrlUser = searchParams.get("user_id")?.trim();
    const fromUrlJob = searchParams.get("job_id")?.trim();
    const fromUrlProject = searchParams.get("project_search")?.trim();
    if (!fromUrlUser && !fromUrlJob && !fromUrlProject) return;
    setQuery({
      ...(fromUrlUser ? { userId: fromUrlUser } : {}),
      ...(fromUrlJob ? { jobId: fromUrlJob } : {}),
      ...(fromUrlProject ? { projectSearch: fromUrlProject } : {}),
      page: 1,
    });
  }, [searchParams, setQuery]);

  const createdFrom = dateFrom ? cstDateStartToUtcIso(dateFrom) : undefined;
  const createdTo = dateTo ? cstDateEndExclusiveToUtcIso(dateTo) : undefined;

  const filterParams = useMemo(
    () => ({
      status: status || undefined,
      lane: lane || undefined,
      anomalyReason: anomalyReason || undefined,
      hasAdminAction: hasAdminAction || undefined,
      userId: userId.trim() || undefined,
      jobId: jobId.trim() || undefined,
      projectSearch: projectSearch.trim() || undefined,
      createdFrom,
      createdTo,
    }),
    [status, lane, anomalyReason, hasAdminAction, userId, jobId, projectSearch, createdFrom, createdTo]
  );

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["admin", "jobs", filterParams, page],
    queryFn: () =>
      listAdminJobs({
        ...filterParams,
        page,
        pageSize,
      }),
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  const handleExport = async () => {
    if (data && data.total > 50_000) {
      toast.error("筛选结果超过 50000 条，请缩小范围后再导出");
      return;
    }
    if (data && data.total > 10_000) {
      const ok = window.confirm(`将导出 ${data.total} 条任务，是否继续？`);
      if (!ok) return;
    }
    setExporting(true);
    try {
      const { blob, filename } = await exportAdminJobs(filterParams);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success(`已导出 ${data?.total ?? ""} 条任务`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };

  const handleSyncActive = async () => {
    setSyncing(true);
    try {
      const result = await syncAdminActiveJobs();
      const recovered =
        result.interruptedRecovered +
        result.staleRecovered +
        result.exhaustedFailed +
        (result.exhaustedPendingFailed ?? 0);
      const synced = result.upstreamSynced.filter((row) => !row.error).length;
      const failed = result.upstreamSynced.filter((row) => row.error).length;
      toast.success(
        `已同步：恢复 ${recovered} 条，上游同步 ${synced} 条${failed ? `，失败 ${failed} 条` : ""}`
      );
      await refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  };

  const resetFilters = () => {
    resetQuery();
  };

  return (
    <>
      <AdminHeader
        title="生成任务"
        description={`全站 AI 生成任务队列；支持提交号 / 项目 / 日期筛选与 CSV 导出${
          process.env.NEXT_PUBLIC_BUILD_ID ? ` · 构建 ${process.env.NEXT_PUBLIC_BUILD_ID}` : ""
        }`}
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">状态</label>
          <Select
            value={status || "all"}
            onValueChange={(v) => {
              setQuery({ status: !v || v === "all" ? "" : v, page: 1 });
            }}
          >
            <SelectTrigger className="w-36">
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              {JOB_STATUS_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>
                  {jobStatusLabel(s)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">通道</label>
          <Select
            value={lane || "all"}
            onValueChange={(v) => {
              setQuery({ lane: !v || v === "all" ? "" : v, page: 1 });
            }}
          >
            <SelectTrigger className="w-36">
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              {JOB_LANE_OPTIONS.map((l) => (
                <SelectItem key={l} value={l}>
                  {jobLaneLabel(l)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">异常类型</label>
          <Select
            value={anomalyReason || "all"}
            onValueChange={(v) => {
              setQuery({ anomalyReason: !v || v === "all" ? "" : v, page: 1 });
            }}
          >
            <SelectTrigger className="w-44">
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              {ANOMALY_REASON_OPTIONS.map((reason) => (
                <SelectItem key={reason} value={reason}>
                  {anomalyReasonLabel(reason)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <label className="flex items-center gap-2 pb-2 text-sm">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-border"
            checked={hasAdminAction}
            onChange={(e) => {
              setQuery({ hasAdminAction: e.target.checked, page: 1 });
            }}
          />
          <span className="text-xs text-muted-foreground">仅人工干预</span>
        </label>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">任务 ID</label>
          <Input
            className="w-56 font-mono text-xs"
            placeholder="任务编号"
            value={jobId}
            onChange={(e) => {
              setQuery({ jobId: e.target.value, page: 1 });
            }}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">所属项目</label>
          <Input
            className="w-48"
            placeholder="项目名 / 编号 / ID"
            value={projectSearch}
            onChange={(e) => {
              setQuery({ projectSearch: e.target.value, page: 1 });
            }}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">用户</label>
          <Input
            className="w-56 font-mono text-xs"
            placeholder="用户编号 / 手机号"
            value={userId}
            onChange={(e) => {
              setQuery({ userId: e.target.value, page: 1 });
            }}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">开始日期</label>
          <Input
            type="date"
            className="w-40"
            value={dateFrom}
            onChange={(e) => {
              setQuery({ dateFrom: e.target.value, page: 1 });
            }}
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">结束日期</label>
          <Input
            type="date"
            className="w-40"
            value={dateTo}
            onChange={(e) => {
              setQuery({ dateTo: e.target.value, page: 1 });
            }}
          />
        </div>

        <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
          刷新
        </Button>
        <Button variant="outline" onClick={() => void handleSyncActive()} disabled={syncing}>
          {syncing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          同步活跃任务
        </Button>
        <Button variant="outline" onClick={resetFilters}>
          重置
        </Button>
        <Button onClick={() => void handleExport()} disabled={exporting || isLoading || !data?.total}>
          {exporting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Download className="mr-2 h-4 w-4" />}
          导出筛选结果
        </Button>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[1560px] text-left text-sm">
          <thead className="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">提交号</th>
              <th className="px-4 py-3 font-medium">所属项目</th>
              <th className="px-4 py-3 font-medium">模型</th>
              <th className="px-4 py-3 font-medium">状态</th>
              <th className="px-4 py-3 font-medium">通道</th>
              <th className="px-4 py-3 font-medium">算力</th>
              <th className="px-4 py-3 font-medium">提交人</th>
              <th className="px-4 py-3 font-medium">视频时长(秒)</th>
              <th className="px-4 py-3 font-medium">创建时间</th>
              <th className="px-4 py-3 font-medium">完成时间</th>
              <th className="px-4 py-3 font-medium">人工干预</th>
              <th className="px-4 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={12} className="px-4 py-8 text-center text-muted-foreground">
                  加载中…
                </td>
              </tr>
            ) : isError ? (
              <tr>
                <td colSpan={10} className="px-4 py-8 text-center text-destructive">
                  加载失败
                </td>
              </tr>
            ) : !data?.items.length ? (
              <tr>
                <td colSpan={12} className="px-4 py-8 text-center text-muted-foreground">
                  暂无任务
                </td>
              </tr>
            ) : (
              data.items.map((job) => (
                <tr key={job.id} className="border-b border-border/60 hover:bg-muted/20">
                  <td className="px-4 py-3 font-mono text-xs" title={String(job.id)}>
                    {formatJobDisplayId(job)}
                  </td>
                  <td className="px-4 py-3">
                    {job.projectTitle ? (
                      <div className="min-w-[120px]">
                        <div className="truncate">{job.projectTitle}</div>
                        {job.projectNo ? (
                          <div className="font-mono text-[11px] text-muted-foreground">{job.projectNo}</div>
                        ) : null}
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="min-w-[140px]">
                      <div>{job.modelDisplayName || job.model || "—"}</div>
                      {job.upstreamChannel ? (
                        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                          {job.upstreamChannel === "fallback" ? "副通道" : "主通道"}
                          {job.upstreamChannelModel ? ` · ${job.upstreamChannelModel}` : ""}
                          {job.upstreamChannelProvider ? ` · ${job.upstreamChannelProvider}` : ""}
                        </div>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-1">
                      <Badge variant={statusVariant(job.status)}>{jobStatusLabel(job.status)}</Badge>
                      {job.anomalyReason ? (
                        <Badge variant="outline" className="text-[10px]">
                          {anomalyReasonLabel(job.anomalyReason)}
                        </Badge>
                      ) : null}
                    </div>
                  </td>
                  <td className="px-4 py-3">{jobLaneLabel(job.lane)}</td>
                  <td className="px-4 py-3 tabular-nums">{job.actualDeductedCredits ?? job.creditCost}</td>
                  <td className="px-4 py-3 text-xs">{submitterLabel(job)}</td>
                  <td className="px-4 py-3 tabular-nums">{job.durationSeconds ?? "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap">{formatDateTimeCN(job.createdAt)}</td>
                  <td className="px-4 py-3 whitespace-nowrap">{formatDateTimeCN(job.completedAt)}</td>
                  <td className="px-4 py-3 text-xs">
                    {job.lastAdminAction ? (
                      <div className="space-y-0.5">
                        <Badge variant="outline">{adminActionLabel(job.lastAdminAction)}</Badge>
                        {job.lastAdminActionAt ? (
                          <div className="text-[11px] text-muted-foreground">
                            {formatDateTimeCN(job.lastAdminActionAt)}
                          </div>
                        ) : null}
                      </div>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <Button variant="outline" size="sm" onClick={() => setDetailJob(job)}>
                      详情
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {data ? (
        <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
          <span>
            共 {data.total} 条 · 第 {data.page} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setQuery({ page: Math.max(1, page - 1) })}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setQuery({ page: page + 1 })}
            >
              下一页
            </Button>
          </div>
        </div>
      ) : null}

      <JobDetailDialog
        job={detailJob}
        open={!!detailJob}
        onClose={() => setDetailJob(null)}
      />
    </>
  );
}
