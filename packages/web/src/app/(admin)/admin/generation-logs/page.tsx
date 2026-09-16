"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
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
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { listAdminGenerationLogs, purgeAdminGenerationLogs } from "@/lib/api/admin";

const PHASE_LABEL: Record<string, string> = {
  submit: "提交",
  response: "返回",
};

const SOURCE_LABEL: Record<string, string> = {
  manual: "手工",
  dedupe: "幂等复用",
  auto: "自动",
};

export default function AdminGenerationLogsPage() {
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [jobId, setJobId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [model, setModel] = useState("");
  const [phase, setPhase] = useState("");
  const [submitSource, setSubmitSource] = useState("");
  const [outcome, setOutcome] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [purgeBefore, setPurgeBefore] = useState("");
  const pageSize = 20;

  const createdFrom = dateFrom ? cstDateStartToUtcIso(dateFrom) : undefined;
  const createdTo = dateTo ? cstDateEndExclusiveToUtcIso(dateTo) : undefined;

  const filterParams = useMemo(
    () => ({
      page,
      pageSize,
      jobId: jobId.trim() || undefined,
      projectId: projectId.trim() || undefined,
      model: model.trim() || undefined,
      phase: phase || undefined,
      submitSource: submitSource || undefined,
      outcome: outcome || undefined,
      createdFrom,
      createdTo,
    }),
    [page, pageSize, jobId, projectId, model, phase, submitSource, outcome, createdFrom, createdTo]
  );

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin", "generation-logs", filterParams],
    queryFn: () => listAdminGenerationLogs(filterParams),
  });

  const purgeMutation = useMutation({
    mutationFn: () => {
      if (!purgeBefore.trim()) throw new Error("请填写清理截止日期");
      return purgeAdminGenerationLogs(cstDateStartToUtcIso(purgeBefore));
    },
    onSuccess: (res) => {
      toast.success(`已清理 ${res.deleted} 条日志`);
      void queryClient.invalidateQueries({ queryKey: ["admin", "generation-logs"] });
    },
    onError: (err: Error) => toast.error(err.message || "清理失败"),
  });

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));

  return (
    <div>
      <AdminHeader
        title="模型调用日志"
        description="每次模型调用记录两行：提交请求与返回结果。提交成功仅表示入队/受理，返回成功才表示生成完成。"
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Input placeholder="任务 ID" value={jobId} onChange={(e) => setJobId(e.target.value)} className="w-28" />
        <Input placeholder="项目 ID" value={projectId} onChange={(e) => setProjectId(e.target.value)} className="w-28" />
        <Input placeholder="模型名" value={model} onChange={(e) => setModel(e.target.value)} className="w-36" />
        <Select value={phase || "all"} onValueChange={(v) => setPhase(!v || v === "all" ? "" : v)}>
          <SelectTrigger className="w-28">
            <SelectValue placeholder="阶段" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部阶段</SelectItem>
            <SelectItem value="submit">提交</SelectItem>
            <SelectItem value="response">返回</SelectItem>
          </SelectContent>
        </Select>
        <Select value={submitSource || "all"} onValueChange={(v) => setSubmitSource(!v || v === "all" ? "" : v)}>
          <SelectTrigger className="w-32">
            <SelectValue placeholder="来源" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部来源</SelectItem>
            <SelectItem value="manual">手工</SelectItem>
            <SelectItem value="dedupe">幂等复用</SelectItem>
            <SelectItem value="auto">自动</SelectItem>
          </SelectContent>
        </Select>
        <Select value={outcome || "all"} onValueChange={(v) => setOutcome(!v || v === "all" ? "" : v)}>
          <SelectTrigger className="w-28">
            <SelectValue placeholder="结果" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部结果</SelectItem>
            <SelectItem value="success">成功</SelectItem>
            <SelectItem value="failure">失败</SelectItem>
          </SelectContent>
        </Select>
        <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36" />
        <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36" />
        <Button variant="secondary" onClick={() => setPage(1)}>
          筛选
        </Button>
      </div>

      <div className="mb-6 flex flex-wrap items-end gap-2 rounded-lg border border-border p-3">
        <Input
          type="date"
          value={purgeBefore}
          onChange={(e) => setPurgeBefore(e.target.value)}
          className="w-40"
          placeholder="清理此日期前"
        />
        <Button
          variant="destructive"
          size="sm"
          disabled={purgeMutation.isPending}
          onClick={() => purgeMutation.mutate()}
        >
          {purgeMutation.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Trash2 className="mr-1 h-4 w-4" />}
          清理历史日志
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="min-w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-3 py-2">时间</th>
                <th className="px-3 py-2">阶段</th>
                <th className="px-3 py-2">来源</th>
                <th className="px-3 py-2">结果</th>
                <th className="px-3 py-2">任务</th>
                <th className="px-3 py-2">模型</th>
                <th className="px-3 py-2">操作人</th>
                <th className="px-3 py-2">载荷</th>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((row) => (
                <tr key={row.id} className="border-t border-border align-top">
                  <td className="whitespace-nowrap px-3 py-2">{formatDateTimeCN(row.createdAt)}</td>
                  <td className="px-3 py-2">{PHASE_LABEL[row.phase] ?? row.phase}</td>
                  <td className="px-3 py-2">
                    {row.submitSource ? SOURCE_LABEL[row.submitSource] ?? row.submitSource : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant={row.outcome === "success" ? "secondary" : "destructive"}>
                      {row.outcome === "success" ? "成功" : "失败"}
                    </Badge>
                  </td>
                  <td className="px-3 py-2">
                    {row.jobId ? (
                      <Link href={`/admin/jobs?jobId=${row.jobId}`} className="text-primary hover:underline">
                        #{row.jobId}
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2">{row.model}</td>
                  <td className="px-3 py-2">{row.actorDisplayName || row.actorUserId || "—"}</td>
                  <td className="max-w-md px-3 py-2">
                    <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-all text-[11px] text-muted-foreground">
                      {JSON.stringify(
                        row.phase === "submit" ? row.requestPayload : row.responsePayload,
                        null,
                        2
                      ) || row.errorMessage || "—"}
                    </pre>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data?.items?.length ?? 0) === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">暂无日志</p>
          ) : null}
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
        <span>
          共 {data?.total ?? 0} 条{isFetching ? "（刷新中…）" : ""}
        </span>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            上一页
          </Button>
          <span>
            {page} / {totalPages}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </div>
    </div>
  );
}
