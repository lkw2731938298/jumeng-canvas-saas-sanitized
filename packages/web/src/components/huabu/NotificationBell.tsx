"use client";

/**
 * 顶栏信息通知入口：未读角标；点击进入 /notifications 列表页。
 */
import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Bell } from "lucide-react";
import { getNotificationUnreadCount } from "@/lib/api/notifications";

type Props = {
  enabled: boolean;
};

export function NotificationBell({ enabled }: Props) {
  const router = useRouter();

  const unreadQuery = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: getNotificationUnreadCount,
    enabled,
    refetchInterval: enabled ? 30_000 : false,
    staleTime: 10_000,
  });

  const unread = unreadQuery.data ?? 0;

  if (!enabled) return null;

  return (
    <div className="topbar-notify">
      <button
        type="button"
        className="topbar-icon-btn topbar-notify-btn"
        aria-label="信息通知"
        title="信息通知"
        onClick={() => router.push("/notifications")}
      >
        <Bell size={17} strokeWidth={1.7} />
        {unread > 0 ? (
          <span className="topbar-notify-badge" aria-label={`${unread} 条未读`}>
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>
    </div>
  );
}
