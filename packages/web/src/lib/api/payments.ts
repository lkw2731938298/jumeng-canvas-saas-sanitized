import { apiFetch } from "./client";

export interface PaymentConfig {
  /** 支付宝已配置且可用；用户侧唯一自助充值/开通通道 */
  alipayEnabled: boolean;
  /** @deprecated 直连已关闭，后端恒 false */
  directRechargeEnabled?: boolean;
  rechargeTiers: number[];
  rechargeTierPricesFen: Record<string, number>;
}

export interface PaymentPrecreateResult {
  outTradeNo: string;
  orderType: "recharge" | "subscription";
  amount: number;
  payAmountFen: number;
  payAmountYuan: string;
  status: string;
  paymentChannel: string;
  qrCode: string;
  subject: string;
  planId?: string | null;
  planName?: string | null;
}

export interface PaymentOrderStatus {
  outTradeNo: string;
  orderType: string;
  amount: number;
  payAmountFen: number;
  status: string;
  paymentChannel: string;
  planId?: string | null;
  completedAt?: string | null;
  balance?: number | null;
}

export function getPaymentConfig() {
  return apiFetch<PaymentConfig>("/api/v1/payments/config");
}

export function createAlipayRechargeOrder(amount: number) {
  return apiFetch<PaymentPrecreateResult>("/api/v1/payments/alipay/recharge", {
    method: "POST",
    body: JSON.stringify({ amount }),
  });
}

export function createAlipaySubscriptionOrder(planId: string) {
  return apiFetch<PaymentPrecreateResult>("/api/v1/payments/alipay/subscription", {
    method: "POST",
    body: JSON.stringify({ planId }),
  });
}

export function getPaymentOrderStatus(outTradeNo: string) {
  return apiFetch<PaymentOrderStatus>(
    `/api/v1/payments/orders/${encodeURIComponent(outTradeNo)}`
  );
}

export function formatFenToYuan(priceFen: number) {
  if (priceFen <= 0) return "免费";
  return `¥${(priceFen / 100).toFixed(priceFen % 100 === 0 ? 0 : 2)}`;
}
