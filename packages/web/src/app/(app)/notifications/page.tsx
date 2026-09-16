"use client";

/**
 * 消息通知列表页：充值 / 任务失败 / 技能与工作流审核等。
 */
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bell } from "lucide-react";
import { toast } from "sonner";
import { ThemePageBackdrop } from "@/components/theme/ThemePageBackdrop";
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  type UserNotificationItem,
} from "@/lib/api/notifications";
import { ApiError } from "@/lib/api/client";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { withBasePath } from "@/lib/basePath";
import { useAuthStore } from "@/stores/authStore";
import { useState } from "react";

const CATEGORY_LABEL: Record<string, string> = {
  recharge: "充值",
  job: "任务",
  skill_review: "技能审核",
  workflow_review: "工作流审核",
};

const PAGE_SIZE = 20;

export default function NotificationsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [page, setPage] = useState(1);

  const listQuery = useQuery({
    queryKey: ["notifications", "page", page],
    queryFn: () => listNotifications({ page, pageSize: PAGE_SIZE }),
    enabled: Boolean(user),
  });

  const items = listQuery.data?.items ?? [];
  const total = listQuery.data?.total ?? 0;
  const unread = listQuery.data?.unreadCount ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  };

  const readOne = useMutation({
    mutationFn: (id: string) => markNotificationRead(id),
    onSuccess: invalidate,
    onError: (err: unknown) => {
      toast.error(
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "标记失败"
      );
    },
  });

  const readAll = useMutation({
    mutationFn: () => markAllNotificationsRead(),
    onSuccess: () => {
      toast.success("已全部标为已读");
      invalidate();
    },
    onError: (err: unknown) => {
      toast.error(
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "操作失败"
      );
    },
  });

  const openItem = (item: UserNotificationItem) => {
    if (!item.isRead) readOne.mutate(item.id);
    const link = (item.linkUrl || "").trim();
    if (!link) return;
    if (link.startsWith("http://") || link.startsWith("https://")) {
      window.open(link, "_blank", "noopener,noreferrer");
      return;
    }
    if (link.startsWith("/")) router.push(link);
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <ThemePageBackdrop />

      <div className="fixed left-0 top-0 z-50 flex items-center gap-4 px-6 py-4">
        <button
          type="button"
          onClick={() => router.back()}
          className="flex items-center gap-2 text-sm text-foreground/70 transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          返回
        </button>
        <button
          type="button"
          onClick={() => router.push("/")}
          className="flex items-center gap-2 text-lg font-bold text-foreground transition-opacity hover:opacity-80"
        >
          <Image
            src={withBasePath("/brand-logo.png")}
            alt="聚梦画布"
            width={36}
            height={20}
            className="h-5 w-auto object-contain"
            priority
            unoptimized
          />
          <span className="text-primary">聚梦</span>
          <span>画布</span>
        </button>
      </div>

      <main className="relative z-10 mx-auto w-full max-w-2xl px-4 pb-16 pt-28">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="flex items-center gap-2 text-2xl font-bold text-foreground">
              <Bell size={22} strokeWidth={1.8} />
              消息通知
              {unread > 0 ? (
                <span className="text-base font-normal text-muted-foreground">({unread} 未读)</span>
              ) : null}
            </h1>
            <p className="mt-1 text-sm text-muted-foreground">
              充值结果、任务失败原因、技能与工作流审核等
            </p>
          </div>
          <button
            type="button"
            disabled={!user || unread <= 0 || readAll.isPending}
            onClick={() => readAll.mutate()}
            className="rounded-lg border border-border bg-card/70 px-3 py-1.5 text-sm text-foreground/80 transition hover:bg-muted disabled:opacity-40"
          >
            全部已读
          </button>
        </div>

        <section
          className="rounded-2xl border border-border bg-card/70 p-3 backdrop-blur-xl"
          style={{ WebkitBackdropFilter: "blur(24px)" }}
        >
          {!user ? (
            <p className="px-3 py-10 text-center text-sm text-muted-foreground">请先登录后查看通知</p>
          ) : listQuery.isLoading ? (
            <p className="px-3 py-10 text-center text-sm text-muted-foreground">加载中…</p>
          ) : items.length === 0 ? (
            <p className="px-3 py-10 text-center text-sm text-muted-foreground">暂无通知</p>
          ) : (
            <ul className="divide-y divide-border">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`w-full px-3 py-3 text-left transition hover:bg-muted/60 ${
                      item.isRead ? "opacity-75" : ""
                    }`}
                    onClick={() => openItem(item)}
                  >
                    <div className="mb-1 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span className="text-primary">
                        {CATEGORY_LABEL[item.category] || "通知"}
                        {!item.isRead ? (
                          <span className="ml-2 inline-block h-1.5 w-1.5 rounded-full bg-red-400 align-middle" />
                        ) : null}
                      </span>
                      <time dateTime={item.createdAt}>
                        {item.createdAt ? formatDateTimeCN(item.createdAt) : ""}
                      </time>
                    </div>
                    <div className="text-sm font-medium text-foreground">{item.title}</div>
                    {item.body ? (
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{item.body}</p>
                    ) : null}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        {user && totalPages > 1 ? (
          <div className="mt-4 flex items-center justify-center gap-3 text-sm text-muted-foreground">
            <button
              type="button"
              disabled={page <= 1}
              className="rounded-md border border-border px-3 py-1 disabled:opacity-35"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </button>
            <span>
              {page} / {totalPages}
            </span>
            <button
              type="button"
              disabled={page >= totalPages}
              className="rounded-md border border-border px-3 py-1 disabled:opacity-35"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              下一页
            </button>
          </div>
        ) : null}
      </main>
    </div>
  );
}
