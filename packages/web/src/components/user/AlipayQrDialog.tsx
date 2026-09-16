"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";
import QRCode from "react-qr-code";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  formatFenToYuan,
  getPaymentOrderStatus,
  type PaymentPrecreateResult,
} from "@/lib/api/payments";

interface AlipayQrDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  payment: PaymentPrecreateResult | null;
  title: string;
  description?: string;
  onPaid?: () => void;
}

export function AlipayQrDialog({
  open,
  onOpenChange,
  payment,
  title,
  description,
  onPaid,
}: AlipayQrDialogProps) {
  const paidRef = useRef(false);

  const { data: orderStatus } = useQuery({
    queryKey: ["payments", "order", payment?.outTradeNo],
    queryFn: () => getPaymentOrderStatus(payment!.outTradeNo),
    enabled: open && Boolean(payment?.outTradeNo),
    refetchInterval: (query) => (query.state.data?.status === "completed" ? false : 2000),
  });

  useEffect(() => {
    if (!open) {
      paidRef.current = false;
    }
  }, [open]);

  useEffect(() => {
    if (orderStatus?.status === "completed" && !paidRef.current) {
      paidRef.current = true;
      toast.success("支付成功");
      onPaid?.();
      onOpenChange(false);
    }
  }, [orderStatus?.status, onOpenChange, onPaid]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {description ?? "请使用支付宝扫一扫完成支付，支付成功后自动到账。"}
          </DialogDescription>
        </DialogHeader>

        {payment ? (
          <div className="flex flex-col items-center gap-3 py-2">
            <div className="rounded-xl border bg-white p-3">
              <QRCode value={payment.qrCode} size={200} />
            </div>
            <p className="text-center text-sm text-muted-foreground">{payment.subject}</p>
            <p className="text-lg font-semibold tabular-nums text-foreground">
              {formatFenToYuan(payment.payAmountFen)}
            </p>
            <p className="text-xs text-muted-foreground">
              订单号 {payment.outTradeNo}
              {orderStatus?.status === "pending" ? " · 等待支付…" : null}
            </p>
          </div>
        ) : (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        )}

        <DialogFooter className="border-t-0 bg-transparent p-0 pt-1 sm:justify-end">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
