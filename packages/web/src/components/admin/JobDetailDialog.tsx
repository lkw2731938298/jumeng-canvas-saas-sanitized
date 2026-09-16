"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { getAdminJobDetail, postAdminJobAction } from "@/lib/api/admin";
import { formatDateTimeCN, formatTraceJsonForDisplay } from "@/lib/formatDateTime";
import {
  ANOMALY_REASON_LABEL,
  JOB_CREDIT_STATUS_LABEL,
  adminActionLabel,
  anomalyReasonLabel,
  jobLaneLabel,
  jobStatusLabel,
} from "@/lib/admin/jobLabels";
import { providerDisplayLabel } from "@/lib/admin/providerLabels";
import type { AdminJob, AdminJobDetail } from "@/types/admin";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { normalizeStorageUrl } from "@/lib/api/storageUrl";
import { formatJobDialogSubtitle } from "@/lib/admin/displayIds";
import { splitJobInputParams } from "@/lib/admin/jobInputParams";
import { cn } from "@/lib/utils";

interface JobDetailDialogProps {
  job: AdminJob | null;
  open: boolean;
  onClose: () => void;
}

type AdminActionType =
  | "sync_upstream"
  | "force_succeed"
  | "force_fail"
  | "requeue"
  | "reconcile_credits";

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid grid-cols-[96px_1fr] gap-3 border-b border-border/60 py-2 last:border-b-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="min-w-0 break-all text-sm">{value}</div>
    </div>
  );
}

function statusBadgeVariant(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "failed") return "destructive";
  if (status === "abnormal") return "destructive";
  if (status === "succeeded") return "default";
  return "secondary";
}

export function JobDetailDialog({ job, open, onClose }: JobDetailDialogProps) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [assetId, setAssetId] = useState("");
  const [pendingAction, setPendingAction] = useState<AdminActionType | null>(null);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["admin", "job", job?.id],
    queryFn: () => getAdminJobDetail(job!.id),
    enabled: open && !!job?.id,
  });

  const actionMutation = useMutation({
    mutationFn: (params: {
      action: AdminActionType;
      note: string;
      payload?: Record<string, unknown>;
    }) => postAdminJobAction(job!.id, params),
    onSuccess: (result) => {
      toast.success(result.message || "操作成功");
      void queryClient.invalidateQueries({ queryKey: ["admin", "jobs"] });
      void refetch();
      setPendingAction(null);
      setNote("");
      setAssetId("");
    },
    onError: (err: Error) => {
      toast.error(err.message || "操作失败");
      setPendingAction(null);
    },
  });

  const detail: AdminJobDetail | AdminJob | null = data ?? job;
  const fullDetail = detail && "inputParams" in detail ? (detail as AdminJobDetail) : null;
  const splitInput = fullDetail?.inputParams ? splitJobInputParams(fullDetail.inputParams) : null;

  const canIntervene =
    detail &&
    (detail.status === "abnormal" ||
      detail.status === "failed" ||
      detail.status === "running" ||
      detail.status === "polling" ||
      !!detail.anomalyReason);

  const hasUpstreamId = Boolean(
    detail?.providerTaskId || detail?.upstreamJobId || detail?.providerJobId
  );

  const runAction = (action: AdminActionType, extraPayload?: Record<string, unknown>) => {
    const trimmed = note.trim();
    if (trimmed.length < 10) {
      toast.error("请填写至少 10 个字的操作说明");
      return;
    }
    if (action === "force_succeed" && !assetId.trim()) {
      toast.error("标为成功必须填写 assetId");
      return;
    }
    const payload =
      action === "force_succeed"
        ? { assetId: assetId.trim(), ...extraPayload }
        : extraPayload;
    actionMutation.mutate({ action, note: trimmed, payload });
  };

  const confirmAction = (action: AdminActionType) => {
    if (action === "force_fail" && fullDetail?.outputAssets?.length) {
      const ok = window.confirm("任务已有输出产物，强制失败将放弃产物并退还算力，确定继续？");
      if (!ok) return;
      runAction(action, { acknowledgeOutputLoss: true });
      return;
    }
    if (action === "requeue") {
      const ok = window.confirm("重新入队将再次预扣算力，确定继续？");
      if (!ok) return;
    }
    runAction(action);
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>任务详情</DialogTitle>
          <DialogDescription>
            {detail ? formatJobDialogSubtitle(detail) : "加载任务信息"}
          </DialogDescription>
        </DialogHeader>

        {isError || !detail ? (
          <div className="py-8 text-center text-sm text-destructive">加载失败</div>
        ) : (
          <div className="space-y-4">
            {isLoading ? (
              <div className="rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                正在加载完整详情…
              </div>
            ) : null}
            <DetailRow
              label="状态"
              value={
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={statusBadgeVariant(detail.status)}>
                    {jobStatusLabel(detail.status)}
                  </Badge>
                  {detail.anomalyReason ? (
                    <Badge variant="outline">{anomalyReasonLabel(detail.anomalyReason)}</Badge>
                  ) : null}
                </div>
              }
            />
            {detail.anomalyDetail ? (
              <DetailRow label="异常说明" value={detail.anomalyDetail} />
            ) : null}
            {detail.anomalyDetectedAt ? (
              <DetailRow
                label="异常时间"
                value={formatDateTimeCN(detail.anomalyDetectedAt)}
              />
            ) : null}
            <DetailRow label="任务 ID" value={<span className="font-mono text-xs">{detail.id}</span>} />
            <DetailRow label="场景" value={detail.scene ?? "—"} />
            <DetailRow label="通道" value={jobLaneLabel(detail.lane)} />
            <DetailRow label="类型" value={detail.jobType} />
            <DetailRow label="供应商" value={providerDisplayLabel(detail.provider)} />
            <DetailRow label="模型" value={detail.modelDisplayName || detail.model || "—"} />
            <DetailRow
              label="上游通道"
              value={
                detail.upstreamChannel
                  ? `${detail.upstreamChannel === "fallback" ? "副通道" : "主通道"}${
                      detail.upstreamChannelModel ? ` · ${detail.upstreamChannelModel}` : ""
                    }${
                      detail.upstreamChannelProvider
                        ? ` · ${providerDisplayLabel(detail.upstreamChannelProvider)}`
                        : ""
                    }`
                  : "—"
              }
            />
            <DetailRow
              label="请求 ID"
              value={
                detail.requestId ? (
                  <span className="font-mono text-xs">{detail.requestId}</span>
                ) : (
                  "—"
                )
              }
            />
            <DetailRow
              label="上游请求号"
              value={
                detail.providerRequestId ? (
                  <span className="font-mono text-xs">{detail.providerRequestId}</span>
                ) : (
                  "—"
                )
              }
            />
            <DetailRow
              label="上游任务 ID"
              value={
                detail.providerTaskId || detail.upstreamJobId ? (
                  <span className="font-mono text-xs">
                    {detail.providerTaskId ?? detail.upstreamJobId}
                  </span>
                ) : (
                  "—"
                )
              }
            />
            <DetailRow
              label="处理中 ID"
              value={
                detail.processingId ? (
                  <span className="font-mono text-xs">{detail.processingId}</span>
                ) : (
                  "—"
                )
              }
            />
            <DetailRow
              label="所属项目"
              value={
                detail.projectTitle ? (
                  <div className="space-y-1">
                    <div>{detail.projectTitle}</div>
                    {detail.projectNo ? (
                      <div className="font-mono text-xs text-muted-foreground">{detail.projectNo}</div>
                    ) : null}
                  </div>
                ) : (
                  "—"
                )
              }
            />
            <DetailRow label="节点 ID" value={detail.nodeId ?? "—"} />
            <DetailRow label="工作流 ID" value={detail.workflowId ?? "—"} />
            <DetailRow
              label="用户"
              value={
                detail.userDisplayName || detail.userPhone ? (
                  <div>
                    {detail.userDisplayName ? <div>{detail.userDisplayName}</div> : null}
                    {detail.userPhone ? (
                      <div className="text-xs text-muted-foreground">{detail.userPhone}</div>
                    ) : null}
                  </div>
                ) : (
                  <span className="font-mono text-xs text-muted-foreground">{detail.userId}</span>
                )
              }
            />
            <DetailRow label="平台算力扣费" value={detail.platformQuotedCredits ?? detail.creditCost} />
            <DetailRow label="实际扣除算力" value={detail.actualDeductedCredits ?? detail.creditCost} />
            <DetailRow
              label="上游算力扣费"
              value={detail.upstreamCreditCost != null ? detail.upstreamCreditCost : "—"}
            />
            <DetailRow label="算力状态" value={JOB_CREDIT_STATUS_LABEL[detail.creditStatus ?? ""] ?? detail.creditStatus ?? "—"} />
            <DetailRow label="定价版本" value={detail.pricingVersion ?? "—"} />
            {detail.creditBreakdown?.length ? (
              <div className="rounded-lg border border-border bg-muted/20 p-3">
                <div className="mb-2 text-xs font-medium text-muted-foreground">算力明细</div>
                <ul className="space-y-1 text-xs">
                  {detail.creditBreakdown.map((item, index) => (
                    <li key={`${item.groupId}-${item.itemId}-${index}`} className="flex justify-between gap-4">
                      <span>{item.label}</span>
                      <span className="tabular-nums">{item.cost}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <DetailRow
              label="素材 ID"
              value={
                detail.assetId ? (
                  <span className="font-mono text-xs">{detail.assetId}</span>
                ) : (
                  "—"
                )
              }
            />
            {fullDetail?.projectAsset ? (
              <div className="rounded-lg border border-border bg-muted/20 p-3">
                <div className="mb-2 text-xs font-medium text-muted-foreground">项目素材</div>
                <DetailRow label="标题" value={fullDetail.projectAsset.title} />
                <DetailRow label="分类" value={fullDetail.projectAsset.category} />
                <DetailRow
                  label="文件"
                  value={
                    fullDetail.projectAsset.fileUrl ? (
                      <a
                        href={normalizeStorageUrl(fullDetail.projectAsset.fileUrl)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary underline-offset-2 hover:underline"
                      >
                        打开素材
                      </a>
                    ) : (
                      "—"
                    )
                  }
                />
              </div>
            ) : null}
            <DetailRow
              label="人工干预"
              value={
                detail.lastAdminAction ? (
                  <div className="space-y-1">
                    <Badge variant="outline">{adminActionLabel(detail.lastAdminAction)}</Badge>
                    {detail.lastAdminActionAt ? (
                      <div className="text-xs text-muted-foreground">
                        {formatDateTimeCN(detail.lastAdminActionAt)}
                      </div>
                    ) : null}
                  </div>
                ) : (
                  "—"
                )
              }
            />
            <DetailRow label="创建时间" value={formatDateTimeCN(detail.createdAt)} />
            <DetailRow label="开始时间" value={formatDateTimeCN(detail.startedAt)} />
            <DetailRow label="完成时间" value={formatDateTimeCN(detail.completedAt)} />
            <DetailRow
              label="视频时长"
              value={detail.durationSeconds != null ? `${detail.durationSeconds} 秒` : "—"}
            />
            <DetailRow
              label="任务耗时"
              value={detail.executionSeconds != null ? `${detail.executionSeconds} 秒` : "—"}
            />
            <DetailRow
              label="错误信息"
              value={
                detail.errorMessage ? (
                  <span>
                    {detail.errorMessage}
                    {fullDetail?.traceJson &&
                    typeof fullDetail.traceJson === "object" &&
                    "errorCode" in (fullDetail.traceJson as Record<string, unknown>) ? (
                      <span className="ml-2 font-mono text-xs text-muted-foreground">
                        [{String((fullDetail.traceJson as Record<string, unknown>).errorCode)}]
                      </span>
                    ) : null}
                  </span>
                ) : (
                  "—"
                )
              }
            />
            {fullDetail?.resultText ? (
              <div className="rounded-lg border border-border bg-muted/20 p-3">
                <div className="mb-2 text-xs font-medium text-muted-foreground">生成文本</div>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">
                  {fullDetail.resultText}
                </pre>
              </div>
            ) : null}
            {splitInput ? (
              <div className="space-y-3">
                {splitInput.prompt ? (
                  <div className="rounded-lg border border-border bg-muted/20 p-3">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">提示词（提交给模型）</div>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {splitInput.prompt}
                    </pre>
                  </div>
                ) : null}
                {splitInput.references.length > 0 ? (
                  <div className="rounded-lg border border-border bg-muted/20 p-3">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">
                      参考素材（{splitInput.references.length}）
                    </div>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {JSON.stringify(splitInput.references, null, 2)}
                    </pre>
                  </div>
                ) : null}
                {Object.keys(splitInput.generationOptions).length > 0 ? (
                  <div className="rounded-lg border border-border bg-muted/20 p-3">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">生成选项</div>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {JSON.stringify(splitInput.generationOptions, null, 2)}
                    </pre>
                  </div>
                ) : null}
                {Object.keys(splitInput.billingMeta).length > 0 ? (
                  <div className="rounded-lg border border-border bg-muted/20 p-3">
                    <div className="mb-2 text-xs font-medium text-muted-foreground">
                      计费快照 / 操作人（非参考素材）
                    </div>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {JSON.stringify(splitInput.billingMeta, null, 2)}
                    </pre>
                  </div>
                ) : null}
                {Object.keys(splitInput.context).length > 0 ? (
                  <details className="rounded-lg border border-border bg-muted/10 p-3">
                    <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
                      其他上下文字段
                    </summary>
                    <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {JSON.stringify(splitInput.context, null, 2)}
                    </pre>
                  </details>
                ) : null}
              </div>
            ) : null}
            {fullDetail?.outputAssets?.length ? (
              <div className="rounded-lg border border-border bg-muted/20 p-3">
                <div className="mb-2 text-xs font-medium text-muted-foreground">输出结果</div>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">
                  {JSON.stringify(fullDetail.outputAssets, null, 2)}
                </pre>
              </div>
            ) : null}
            {fullDetail?.traceJson && Object.keys(fullDetail.traceJson).length > 0 ? (
              <div className="rounded-lg border border-border bg-muted/20 p-3">
                <div className="mb-2 text-xs font-medium text-muted-foreground">追溯信息</div>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all text-xs">
                  {JSON.stringify(formatTraceJsonForDisplay(fullDetail.traceJson), null, 2)}
                </pre>
              </div>
            ) : null}

            {fullDetail?.adminActions?.length ? (
              <div className="rounded-lg border border-border bg-muted/20 p-3">
                <div className="mb-2 text-xs font-medium text-muted-foreground">人工干预记录</div>
                <ul className="space-y-2 text-xs">
                  {fullDetail.adminActions.map((row) => (
                    <li key={row.id} className="rounded border border-border/60 bg-background/50 p-2">
                      <div className="font-medium">
                        {row.action} · {formatDateTimeCN(row.createdAt)}
                      </div>
                      <div className="text-muted-foreground">
                        {row.beforeStatus} → {row.afterStatus}
                        {row.beforeCreditStatus || row.afterCreditStatus
                          ? ` · 算力 ${row.beforeCreditStatus ?? "—"} → ${row.afterCreditStatus ?? "—"}`
                          : null}
                      </div>
                      <div className="mt-1 break-all">{row.note}</div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {canIntervene ? (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 space-y-3">
                <div className="text-sm font-medium">人工干预</div>
                <div className="space-y-2">
                  <Label htmlFor="admin-job-note">操作说明（必填，至少 10 字）</Label>
                  <textarea
                    id="admin-job-note"
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    rows={2}
                    placeholder="说明干预原因，便于审计追溯"
                    className={cn(
                      "flex min-h-[60px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm",
                      "ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none",
                      "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                    )}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="admin-job-asset">assetId（仅「标为成功」必填）</Label>
                  <Input
                    id="admin-job-asset"
                    value={assetId}
                    onChange={(e) => setAssetId(e.target.value)}
                    placeholder="项目内已有素材 ID"
                    className="font-mono text-xs"
                  />
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={!hasUpstreamId || actionMutation.isPending}
                    onClick={() => {
                      setPendingAction("sync_upstream");
                      confirmAction("sync_upstream");
                    }}
                  >
                    {actionMutation.isPending && pendingAction === "sync_upstream" ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : null}
                    同步上游
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={actionMutation.isPending}
                    onClick={() => {
                      setPendingAction("force_succeed");
                      confirmAction("force_succeed");
                    }}
                  >
                    标为成功
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    disabled={actionMutation.isPending || detail.status === "failed"}
                    onClick={() => {
                      setPendingAction("force_fail");
                      confirmAction("force_fail");
                    }}
                  >
                    标为失败
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={
                      actionMutation.isPending ||
                      !["abnormal", "failed"].includes(detail.status)
                    }
                    onClick={() => {
                      setPendingAction("requeue");
                      confirmAction("requeue");
                    }}
                  >
                    重新入队
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    disabled={actionMutation.isPending}
                    onClick={() => {
                      setPendingAction("reconcile_credits");
                      confirmAction("reconcile_credits");
                    }}
                  >
                    同步算力
                  </Button>
                </div>
                {!hasUpstreamId ? (
                  <p className="text-xs text-muted-foreground">无上游任务 ID 时不可同步上游状态</p>
                ) : null}
                <p className="text-xs text-muted-foreground">
                  异常原因参考：{Object.values(ANOMALY_REASON_LABEL).join("、")}
                </p>
              </div>
            ) : null}
          </div>
        )}

        <div className="flex justify-end pt-2">
          <Button variant="outline" onClick={onClose}>
            关闭
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
