"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  approvePendingGeneration,
  getCollaborationSettings,
  listPendingApprovals,
  rejectPendingGeneration,
  updateCollaborationSettings,
  type ProjectCollaborationSettings,
  type ProjectPendingApproval,
} from "@/lib/api/projects";
import { ApiError } from "@/lib/api/client";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { userFacingModelLabel } from "@/lib/credits/userFacingModelLabel";

function capInputValue(cap: number | null | undefined): string {
  return cap != null && cap > 0 ? String(cap) : "";
}

function parseCapInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.floor(n);
}

export function ProjectCollaborationSpendPanel({
  projectId,
  enabled = true,
}: {
  projectId: string;
  enabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({
    queryKey: ["projects", projectId, "collaboration-settings"],
    queryFn: () => getCollaborationSettings(projectId),
    enabled: Boolean(projectId) && enabled,
  });

  const approvalsQuery = useQuery({
    queryKey: ["projects", projectId, "pending-approvals"],
    queryFn: () => listPendingApprovals(projectId),
    enabled: Boolean(projectId) && enabled,
    refetchInterval: enabled ? 10_000 : false,
  });

  const settings = settingsQuery.data;
  const [dailyCap, setDailyCap] = useState("");
  const [totalCap, setTotalCap] = useState("");
  const [approvalThreshold, setApprovalThreshold] = useState("");

  useEffect(() => {
    if (!settings) return;
    setDailyCap(capInputValue(settings.collaboratorDailyCap));
    setTotalCap(capInputValue(settings.collaboratorTotalCap));
    setApprovalThreshold(capInputValue(settings.collaboratorApprovalThreshold));
  }, [settings]);

  const saveMutation = useMutation({
    mutationFn: () =>
      updateCollaborationSettings(projectId, {
        collaboratorDailyCap: parseCapInput(dailyCap),
        collaboratorTotalCap: parseCapInput(totalCap),
        collaboratorApprovalThreshold: parseCapInput(approvalThreshold),
      }),
    onSuccess: () => {
      toast.success("协作消耗设置已保存");
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "collaboration-settings"],
      });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) toast.error(err.message);
      else toast.error("保存失败");
    },
  });

  const approveMutation = useMutation({
    mutationFn: (jobId: string) => approvePendingGeneration(projectId, jobId),
    onSuccess: () => {
      toast.success("已批准生成");
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "pending-approvals"] });
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "collaboration-settings"],
      });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) toast.error(err.message);
      else toast.error("批准失败");
    },
  });

  const rejectMutation = useMutation({
    mutationFn: (jobId: string) => rejectPendingGeneration(projectId, jobId),
    onSuccess: () => {
      toast.success("已拒绝");
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "pending-approvals"] });
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "collaboration-settings"],
      });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) toast.error(err.message);
      else toast.error("拒绝失败");
    },
  });

  const pending = approvalsQuery.data ?? [];

  return (
    <div className="space-y-4 border-t border-border/60 pt-4">
      <div>
        <p className="mb-1 text-sm font-medium">协作消耗管控</p>
        <p className="mb-3 text-xs text-muted-foreground">
          仅限制协作者触发的生成消耗（从您的算力扣除）。留空表示不限制。
        </p>
        {settings ? (
          <p className="mb-3 text-xs text-muted-foreground">
            今日已用 {settings.dailySpent.toLocaleString()}
            {settings.collaboratorDailyCap != null
              ? ` / ${settings.collaboratorDailyCap.toLocaleString()}`
              : ""}
            {" · "}
            累计 {settings.totalSpent.toLocaleString()}
            {settings.collaboratorTotalCap != null
              ? ` / ${settings.collaboratorTotalCap.toLocaleString()}`
              : ""}
          </p>
        ) : null}
        <div className="grid gap-2 sm:grid-cols-3">
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">每日上限</span>
            <Input
              value={dailyCap}
              onChange={(e) => setDailyCap(e.target.value)}
              placeholder="不限"
              className="h-9"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">总上限</span>
            <Input
              value={totalCap}
              onChange={(e) => setTotalCap(e.target.value)}
              placeholder="不限"
              className="h-9"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted-foreground">审批阈值</span>
            <Input
              value={approvalThreshold}
              onChange={(e) => setApprovalThreshold(e.target.value)}
              placeholder="不需审批"
              className="h-9"
            />
          </label>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          单次生成算力 ≥ 审批阈值时，协作者提交后需您批准才会执行。
        </p>
        <Button
          type="button"
          size="sm"
          className="mt-3"
          disabled={saveMutation.isPending}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "保存设置"}
        </Button>
      </div>

      <div>
        <p className="mb-2 text-sm font-medium">
          待审批生成
          {pending.length > 0 ? (
            <span className="ml-1.5 text-xs text-primary">({pending.length})</span>
          ) : null}
        </p>
        {approvalsQuery.isLoading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : pending.length === 0 ? (
          <p className="py-3 text-center text-xs text-muted-foreground">暂无待审批任务</p>
        ) : (
          <div className="max-h-48 space-y-2 overflow-y-auto pr-1">
            {pending.map((item) => (
              <PendingApprovalRow
                key={item.jobId}
                item={item}
                busy={approveMutation.isPending || rejectMutation.isPending}
                onApprove={() => approveMutation.mutate(item.jobId)}
                onReject={() => rejectMutation.mutate(item.jobId)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PendingApprovalRow({
  item,
  busy,
  onApprove,
  onReject,
}: {
  item: ProjectPendingApproval;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-2 rounded-lg border border-border/50 bg-muted/10 px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">
          {item.actorDisplayName || "协作者"} · {item.creditCost} 算力
        </p>
        <p className="truncate text-xs text-muted-foreground">
          {userFacingModelLabel({
            modelDisplayName: item.modelDisplayName,
            model: item.model,
          }) ?? "生成任务"}
          {item.nodeId ? ` · 节点 ${item.nodeId.slice(0, 8)}…` : ""}
        </p>
        {item.createdAt ? (
          <p className="text-[11px] text-muted-foreground">{formatDateTimeCN(item.createdAt)}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 gap-1">
        <Button
          type="button"
          size="icon"
          variant="secondary"
          className="h-8 w-8"
          disabled={busy}
          onClick={onApprove}
          title="批准"
        >
          <Check className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-8 w-8 text-destructive"
          disabled={busy}
          onClick={onReject}
          title="拒绝"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

export function ProjectCollaborationSpendSummary({
  settings,
}: {
  settings: ProjectCollaborationSettings | undefined;
}) {
  if (!settings) return null;
  const hasLimits =
    settings.collaboratorDailyCap != null ||
    settings.collaboratorTotalCap != null ||
    settings.collaboratorApprovalThreshold != null;
  if (!hasLimits) return null;

  return (
    <p className="border-t border-border/60 pt-3 text-xs text-muted-foreground">
      协作消耗：今日 {settings.dailySpent}
      {settings.collaboratorDailyCap != null ? `/${settings.collaboratorDailyCap}` : ""}
      ，累计 {settings.totalSpent}
      {settings.collaboratorTotalCap != null ? `/${settings.collaboratorTotalCap}` : ""}
      {settings.collaboratorApprovalThreshold != null
        ? `；≥${settings.collaboratorApprovalThreshold} 算力需审批`
        : ""}
    </p>
  );
}
