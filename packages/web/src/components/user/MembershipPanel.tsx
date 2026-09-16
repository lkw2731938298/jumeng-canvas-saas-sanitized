"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Crown, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AlipayQrDialog } from "@/components/user/AlipayQrDialog";
import {
  cancelSubscription,
  formatPriceYuan,
  getMySubscription,
  listSubscriptionPlans,
} from "@/lib/api/subscriptions";
import {
  createAlipaySubscriptionOrder,
  getPaymentConfig,
  type PaymentPrecreateResult,
} from "@/lib/api/payments";
import { cn } from "@/lib/utils";

export function MembershipPanel({
  className,
  /** 弹窗场景：仅打开时拉取，避免账户菜单未点时多余请求 */
  enabled = true,
}: {
  className?: string;
  enabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const [qrPayment, setQrPayment] = useState<PaymentPrecreateResult | null>(null);
  const [qrOpen, setQrOpen] = useState(false);

  const { data: paymentConfig } = useQuery({
    queryKey: ["payments", "config"],
    queryFn: getPaymentConfig,
    staleTime: 60_000,
    enabled,
  });
  // 用户侧仅支付宝开通会员；演示直开通已永久关闭
  const useAlipay = paymentConfig?.alipayEnabled === true;

  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ["subscriptions", "me"],
    queryFn: getMySubscription,
    staleTime: 30_000,
    enabled,
  });

  const { data: plansData, isLoading: plansLoading } = useQuery({
    queryKey: ["subscriptions", "plans"],
    queryFn: listSubscriptionPlans,
    staleTime: 60_000,
    enabled: enabled && me?.creditsEnabled !== false,
  });

  const alipayMutation = useMutation({
    mutationFn: (planId: string) => createAlipaySubscriptionOrder(planId),
    onSuccess: (data) => {
      setQrPayment(data);
      setQrOpen(true);
    },
    onError: (err: Error) => toast.error(err.message || "创建支付订单失败"),
  });

  const cancelMutation = useMutation({
    mutationFn: cancelSubscription,
    onSuccess: () => {
      toast.success("已取消自动续费，当前账期仍可使用至到期");
      void queryClient.invalidateQueries({ queryKey: ["subscriptions"] });
    },
    onError: (err: Error) => toast.error(err.message || "取消失败"),
  });

  const handleSubscribe = (planId: string) => {
    if (!useAlipay) {
      // 用户侧仅提示支付宝，不暴露管理员发放
      toast.error("请使用支付宝扫码开通会员");
      return;
    }
    alipayMutation.mutate(planId);
  };

  if (meLoading) {
    return (
      <div className={cn("flex items-center gap-2 text-sm text-white/50", className)}>
        <Loader2 className="h-4 w-4 animate-spin" />
        加载会员信息…
      </div>
    );
  }

  if (me?.creditsEnabled === false) {
    return <p className={cn("text-sm text-white/45", className)}>当前为免扣费模式</p>;
  }

  const subscription = me?.subscription;
  const plans = plansData?.items ?? [];
  const subscribePending = alipayMutation.isPending;

  return (
    <>
      <div className={cn("space-y-4", className)}>
        {subscription?.isActive ? (
          <div className="rounded-xl border border-amber-400/25 bg-amber-400/10 p-4">
            <p className="flex items-center gap-2 font-medium text-amber-100">
              <Crown className="h-4 w-4" />
              {subscription.planName}
            </p>
            <p className="mt-2 text-sm text-white/70">
              本账期赠送{" "}
              <span className="font-mono text-white/95">{subscription.monthlyCredits}</span>{" "}
              会员订阅算力
              {subscription.storageGb > 0 ? (
                <>
                  ，额外{" "}
                  <span className="font-mono text-white/95">{subscription.storageGb}</span> GiB
                  云存储
                </>
              ) : null}
            </p>
            <p className="mt-1 text-xs text-white/45">
              有效期至 {new Date(subscription.currentPeriodEnd).toLocaleDateString("zh-CN")}
              {subscription.daysRemaining > 0 ? `（剩余 ${subscription.daysRemaining} 天）` : ""}
            </p>
            {subscription.autoRenew ? (
              <button
                type="button"
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
                className="mt-3 text-xs text-white/50 underline-offset-2 hover:text-white/70 hover:underline"
              >
                取消自动续费
              </button>
            ) : (
              <p className="mt-3 text-xs text-white/40">已关闭自动续费</p>
            )}
          </div>
        ) : (
          <p className="text-sm text-white/50">开通月会员，每账期自动赠送限时会员订阅算力。</p>
        )}

        {plansLoading ? (
          <p className="text-sm text-white/45">加载套餐…</p>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {plans.map((plan) => {
              const isCurrent = subscription?.planId === plan.id && subscription.isActive;
              return (
                <div
                  key={plan.id}
                  className="flex flex-col rounded-xl border border-white/10 bg-white/5 p-4"
                >
                  <p className="font-medium text-white/90">{plan.name}</p>
                  <p className="mt-1 text-xs text-white/45">{plan.description}</p>
                  <p className="mt-3 font-mono text-lg text-white/95">
                    {plan.monthlyCredits}{" "}
                    <span className="text-sm font-normal text-white/50">算力/期</span>
                  </p>
                  {plan.storageGb > 0 ? (
                    <p className="mt-1 text-sm text-white/60">+ {plan.storageGb} GiB 云存储</p>
                  ) : null}
                  <p className="mt-1 text-sm text-white/60">{formatPriceYuan(plan.priceCents)}</p>
                  <button
                    type="button"
                    disabled={isCurrent || subscribePending || (!isCurrent && !useAlipay)}
                    onClick={() => handleSubscribe(plan.id)}
                    className="mt-4 rounded-lg border border-white/15 bg-white/10 px-3 py-2 text-sm text-white/90 transition-colors hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {isCurrent
                      ? "当前套餐"
                      : subscribePending
                        ? "处理中…"
                        : useAlipay
                          ? "支付宝开通"
                          : "请使用支付宝开通"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
        <p className="text-[11px] text-white/35">
          {useAlipay
            ? "请使用支付宝扫一扫完成支付；支付成功后自动开通会员并发放算力。"
            : "请使用支付宝扫码开通会员。"}
        </p>
      </div>

      <AlipayQrDialog
        open={qrOpen}
        onOpenChange={setQrOpen}
        payment={qrPayment}
        title="支付宝扫码开通会员"
        onPaid={() => {
          void queryClient.invalidateQueries({ queryKey: ["subscriptions"] });
          void queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
          void queryClient.invalidateQueries({ queryKey: ["storage", "quota"] });
        }}
      />
    </>
  );
}
