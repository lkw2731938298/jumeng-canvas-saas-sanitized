"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gift, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  claimCreditActivity,
  formatCreditTypeLabel,
  listCreditActivities,
  type CreditActivity,
  type CreditBalance,
} from "@/lib/api/credits";
import { userFacingModelSuffix } from "@/lib/credits/userFacingModelLabel";
import { handleApiError } from "@/lib/errors/handleApiError";
import { cn } from "@/lib/utils";

function formatWindow(activity: CreditActivity) {
  if (!activity.startsAt || !activity.endsAt) return "";
  const start = new Date(activity.startsAt).toLocaleDateString("zh-CN");
  const end = new Date(activity.endsAt).toLocaleDateString("zh-CN");
  return `${start} — ${end}`;
}

export function CreditActivitiesPanel({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["credits", "activities"],
    queryFn: listCreditActivities,
    staleTime: 30_000,
  });

  const claimMutation = useMutation({
    mutationFn: (activityId: string) => claimCreditActivity(activityId),
    onSuccess: (result) => {
      toast.success(`领取成功，到账 ${result.amount} 算力`);
      void queryClient.invalidateQueries({ queryKey: ["credits", "activities"] });
      void queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
      queryClient.setQueryData<CreditBalance>(["credits", "balance"], (prev) =>
        prev
          ? { ...prev, balance: result.balance }
          : { balance: result.balance, creditsEnabled: true }
      );
    },
    onError: (err: unknown) => {
      handleApiError(err);
    },
  });

  const items = data?.items ?? [];

  if (isLoading) {
    return (
      <div className={cn("flex items-center gap-2 text-sm text-muted-foreground", className)}>
        <Loader2 className="h-4 w-4 animate-spin" />
        加载活动…
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <p className={cn("text-sm text-muted-foreground", className)}>暂无可领取的算力活动</p>
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      {items.map((activity) => {
        const claimed = activity.userClaimCount >= activity.perUserLimit;
        const ineligible = activity.eligible === false;
        const disabled = !activity.canClaim || claimed || claimMutation.isPending;
        const buttonLabel = claimed
          ? "已领取"
          : ineligible
            ? "未满足条件"
            : "领取";
        return (
          <div
            key={activity.id}
            className="rounded-xl border border-border bg-card/70 p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 font-medium text-foreground">
                  <Gift className="h-4 w-4 shrink-0 text-primary" />
                  {activity.title}
                </p>
                {activity.description ? (
                  <p className="mt-1 text-xs text-muted-foreground">{activity.description}</p>
                ) : null}
                <p className="mt-2 text-sm text-foreground/70">
                  <span className="font-mono text-foreground">{activity.amount}</span>{" "}
                  {formatCreditTypeLabel(activity.creditType)}
                  {userFacingModelSuffix({
                    modelDisplayName: activity.modelDisplayName,
                    modelName: activity.modelName,
                  })}
                  <span className="text-muted-foreground"> · 领取后 {activity.validDays} 天有效</span>
                </p>
                <p className="mt-1 text-[11px] text-muted-foreground">{formatWindow(activity)}</p>
                {activity.totalQuota != null ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    剩余名额 {activity.remainingQuota ?? 0} / {activity.totalQuota}
                  </p>
                ) : null}
                {(activity.claimRuleSummary?.length ?? 0) > 0 ? (
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    领取条件：{activity.claimRuleSummary!.join("；")}
                  </p>
                ) : null}
                {ineligible && (activity.eligibilityReasons?.length ?? 0) > 0 ? (
                  <p className="mt-1 text-[11px] text-amber-300/80">
                    {activity.eligibilityReasons!.join("；")}
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                disabled={disabled}
                onClick={() => claimMutation.mutate(activity.id)}
                className="shrink-0 rounded-lg border border-border bg-muted px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-muted/80 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {claimMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  buttonLabel
                )}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
