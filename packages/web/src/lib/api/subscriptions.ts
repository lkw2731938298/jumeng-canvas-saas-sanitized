import { apiFetch } from "./client";

export interface SubscriptionPlan {
  id: string;
  code: string;
  name: string;
  description: string;
  monthlyCredits: number;
  storageGb: number;
  periodDays: number;
  priceCents: number;
  sortOrder: number;
  isActive: boolean;
}

export interface UserSubscription {
  id: string;
  planId: string;
  planCode: string;
  planName: string;
  status: string;
  autoRenew: boolean;
  monthlyCredits: number;
  storageGb: number;
  currentPeriodStart: string;
  currentPeriodEnd: string;
  isActive: boolean;
  daysRemaining: number;
}

export function listSubscriptionPlans() {
  return apiFetch<{ items: SubscriptionPlan[] }>("/api/v1/subscriptions/plans");
}

export function getMySubscription() {
  return apiFetch<{ creditsEnabled: boolean; subscription: UserSubscription | null }>(
    "/api/v1/subscriptions/me"
  );
}

export function cancelSubscription() {
  return apiFetch<UserSubscription>("/api/v1/subscriptions/cancel", {
    method: "POST",
  });
}

export function formatPriceYuan(priceCents: number) {
  if (priceCents <= 0) return "演示免费";
  return `¥${(priceCents / 100).toFixed(priceCents % 100 === 0 ? 0 : 2)}`;
}
