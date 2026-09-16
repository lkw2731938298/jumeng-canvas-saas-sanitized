/** 用户站内通知 API */
import { apiFetch } from "./client";

export type NotificationCategory =
  | "recharge"
  | "job"
  | "skill_review"
  | "workflow_review"
  | string;

export type UserNotificationItem = {
  id: string;
  category: NotificationCategory;
  title: string;
  body: string;
  linkUrl?: string | null;
  refType?: string | null;
  refId?: string | null;
  isRead: boolean;
  createdAt: string;
};

export type NotificationListResult = {
  items: UserNotificationItem[];
  total: number;
  page: number;
  pageSize: number;
  unreadCount: number;
};

export async function listNotifications(params?: {
  page?: number;
  pageSize?: number;
  unreadOnly?: boolean;
}): Promise<NotificationListResult> {
  const sp = new URLSearchParams();
  if (params?.page) sp.set("page", String(params.page));
  if (params?.pageSize) sp.set("pageSize", String(params.pageSize));
  if (params?.unreadOnly) sp.set("unreadOnly", "true");
  const qs = sp.toString();
  return apiFetch<NotificationListResult>(
    `/api/v1/notifications${qs ? `?${qs}` : ""}`
  );
}

export async function getNotificationUnreadCount(): Promise<number> {
  const res = await apiFetch<{ unreadCount: number }>(
    "/api/v1/notifications/unread-count"
  );
  return Number(res.unreadCount ?? 0);
}

export async function markNotificationRead(
  id: string
): Promise<UserNotificationItem> {
  return apiFetch<UserNotificationItem>(
    `/api/v1/notifications/${encodeURIComponent(id)}/read`,
    { method: "POST" }
  );
}

export async function markAllNotificationsRead(): Promise<{
  updated: number;
  unreadCount: number;
}> {
  return apiFetch("/api/v1/notifications/read-all", { method: "POST" });
}
