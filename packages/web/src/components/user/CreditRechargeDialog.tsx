"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Coins, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AlipayQrDialog } from "@/components/user/AlipayQrDialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { type CreditBalance } from "@/lib/api/credits";
import {
  createAlipayRechargeOrder,
  formatFenToYuan,
  getPaymentConfig,
  type PaymentPrecreateResult,
} from "@/lib/api/payments";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";

/** 后台未返回档位时的兜底（与 API 默认一致） */
const FALLBACK_RECHARGE_TIERS = [100, 200, 500, 1000, 2000];

interface CreditRechargeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreditRechargeDialog({ open, onOpenChange }: CreditRechargeDialogProps) {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [selected, setSelected] = useState<number>(FALLBACK_RECHARGE_TIERS[0]);
  const [qrPayment, setQrPayment] = useState<PaymentPrecreateResult | null>(null);
  const [qrOpen, setQrOpen] = useState(false);

  const { data: paymentConfig, isLoading: configLoading } = useQuery({
    queryKey: ["payments", "config"],
    queryFn: getPaymentConfig,
    staleTime: 60_000,
    enabled: open,
  });

  const rechargeTiers = useMemo(
    () =>
      paymentConfig?.rechargeTiers?.length
        ? paymentConfig.rechargeTiers
        : [...FALLBACK_RECHARGE_TIERS],
    [paymentConfig?.rechargeTiers]
  );

  // 用户侧仅支付宝扫码充值；无支付直连已永久关闭
  const useAlipay = paymentConfig?.alipayEnabled === true;

  useEffect(() => {
    if (open && rechargeTiers.length) {
      setSelected(rechargeTiers[0]);
      setQrPayment(null);
      setQrOpen(false);
    }
  }, [open, rechargeTiers]);

  const alipayMutation = useMutation({
    mutationFn: (amount: number) => createAlipayRechargeOrder(amount),
    onSuccess: (data) => {
      setQrPayment(data);
      setQrOpen(true);
    },
    onError: (err: Error) => {
      toast.error(err.message || "创建支付订单失败");
    },
  });

  const pending = alipayMutation.isPending;

  const currentBalance =
    queryClient.getQueryData<CreditBalance>(["credits", "balance"])?.balance ??
    user?.computePower ??
    0;

  const tierPriceFen = (tier: number) =>
    paymentConfig?.rechargeTierPricesFen?.[String(tier)] ?? null;

  const handleConfirm = () => {
    if (!useAlipay) {
      // 用户侧仅提示支付宝，不暴露管理员加款
      toast.error("请使用支付宝扫码充值");
      return;
    }
    alipayMutation.mutate(selected);
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Coins className="h-4 w-4 text-amber-500" />
              充值算力
            </DialogTitle>
            <DialogDescription>
              选择充值档位
              {useAlipay ? "，支付宝扫码支付后自动到账" : "，请使用支付宝扫码充值"}
              。当前余额{" "}
              <span className="font-medium tabular-nums text-foreground">{currentBalance}</span>{" "}
              算力
            </DialogDescription>
          </DialogHeader>

          {configLoading ? (
            <div className="flex justify-center py-8 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : rechargeTiers.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">暂无可选充值档位</p>
          ) : (
            <div
              className={cn(
                "grid gap-2",
                rechargeTiers.length <= 3 ? "grid-cols-3" : "grid-cols-3 sm:grid-cols-5"
              )}
            >
              {rechargeTiers.map((tier) => {
                const active = selected === tier;
                const priceFen = tierPriceFen(tier);
                return (
                  <button
                    key={tier}
                    type="button"
                    disabled={pending}
                    onClick={() => setSelected(tier)}
                    className={cn(
                      "rounded-lg border px-2 py-3 text-center transition-colors",
                      active
                        ? "border-primary bg-primary/10 text-foreground shadow-[inset_0_0_0_1px_hsl(var(--primary))]"
                        : "border-border bg-muted/30 text-muted-foreground hover:border-primary/40 hover:bg-muted/60"
                    )}
                  >
                    <span className="block text-lg font-semibold tabular-nums">{tier}</span>
                    <span className="mt-0.5 block text-[11px] opacity-70">算力</span>
                    {priceFen ? (
                      <span className="mt-1 block text-[10px] text-foreground/70">
                        {formatFenToYuan(priceFen)}
                      </span>
                    ) : null}
                  </button>
                );
              })}
            </div>
          )}

          <p className="text-xs text-muted-foreground">
            {useAlipay
              ? "请使用支付宝扫一扫完成支付；支付结果以服务端查单为准。"
              : "请使用支付宝扫码充值。"}
          </p>

          <DialogFooter className="border-t-0 bg-transparent p-0 pt-1 sm:justify-end">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
              取消
            </Button>
            <Button
              type="button"
              onClick={handleConfirm}
              disabled={pending || rechargeTiers.length === 0 || !useAlipay}
            >
              {pending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  处理中…
                </>
              ) : useAlipay ? (
                `支付宝支付 ${formatFenToYuan(tierPriceFen(selected) ?? 0)}`
              ) : (
                "请使用支付宝扫码充值"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlipayQrDialog
        open={qrOpen}
        onOpenChange={setQrOpen}
        payment={qrPayment}
        title="支付宝扫码充值"
        onPaid={() => {
          void queryClient.invalidateQueries({ queryKey: ["credits", "transactions"] });
          void queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
          onOpenChange(false);
        }}
      />
    </>
  );
}
