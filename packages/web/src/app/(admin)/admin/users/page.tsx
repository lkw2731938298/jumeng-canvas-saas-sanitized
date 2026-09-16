"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { useAdminAuth } from "@/components/admin/AdminAuthContext";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { adjustAdminUserCredits, clearAdminUserAuthRisk, getAdminAuthSettings, getAdminUserDetail, listAdminUserCreditHistory, listAdminUsers, patchAdminAuthSettings, patchAdminUser, revokeAdminUserSessions } from "@/lib/api/admin";
import { formatJobDisplayId, normalizeUserNo } from "@/lib/admin/displayIds";
import { usePersistedAdminQuery } from "@/lib/admin/usePersistedAdminQuery";
import { CreditTransactionTable } from "@/components/credits/CreditTransactionTable";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { cn } from "@/lib/utils";
import type { AdminUserDetail, AdminUserListItem } from "@/types/admin";

/** 用户管理列表筛选（session 内切页保留） */
const USERS_QUERY_DEFAULTS = {
  searchInput: "",
  search: "",
  role: "",
  activeFilter: "",
  page: 1,
};

function authActionLabel(action: string) {
  const map: Record<string, string> = {
    register: "注册",
    login_password: "密码登录",
    login_sms: "验证码登录",
    login_admin_password: "管理后台登录",
    login_sso: "SSO 登录",
    logout: "登出",
    reset_password: "重置密码",
  };
  return map[action] || action;
}

function RegistrationSettingsBar() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "auth-settings"],
    queryFn: getAdminAuthSettings,
  });

  const patchMutation = useMutation({
    mutationFn: (enabled: boolean) => patchAdminAuthSettings(enabled),
    onSuccess: (result) => {
      toast.success(result.registrationEnabled ? "已开放注册" : "已关闭注册");
      void queryClient.invalidateQueries({ queryKey: ["admin", "auth-settings"] });
    },
    onError: () => toast.error("更新注册开关失败"),
  });

  if (isLoading || !data) return null;

  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-muted/20 px-4 py-3">
      <div>
        <p className="text-sm font-medium">开放注册</p>
        <p className="text-xs text-muted-foreground">
          关闭后新用户无法注册；已注册账号不受影响。环境变量默认：
          {data.envRegistrationDefault ? "开启" : "关闭"}。
          注册赠送算力见{" "}
          <Link href="/admin/register-bonus" className="underline underline-offset-2">
            注册赠送
          </Link>
          （每人仅一次）。
        </p>
      </div>
      <Button
        variant={data.registrationEnabled ? "default" : "outline"}
        size="sm"
        disabled={patchMutation.isPending}
        onClick={() => patchMutation.mutate(!data.registrationEnabled)}
      >
        {patchMutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : data.registrationEnabled ? (
          "当前：已开放"
        ) : (
          "当前：已关闭"
        )}
      </Button>
    </div>
  );
}

function roleBadge(role: string) {
  if (role === "admin") {
    return <Badge variant="default">管理员</Badge>;
  }
  return <Badge variant="secondary">用户</Badge>;
}

function statusBadge(isActive: boolean) {
  if (isActive) {
    return <Badge variant="outline">正常</Badge>;
  }
  return <Badge variant="destructive">已禁用</Badge>;
}

function UserDetailDialog({
  user,
  open,
  onOpenChange,
}: {
  user: AdminUserListItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { hasPermission } = useAdminAuth();
  const canAdjustCredits = hasPermission("credits");
  const [deltaInput, setDeltaInput] = useState("");
  const [reason, setReason] = useState("");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin", "user", user?.id],
    queryFn: () => getAdminUserDetail(user!.id),
    enabled: open && Boolean(user?.id),
  });

  useEffect(() => {
    if (!open) {
      setDeltaInput("");
      setReason("");
    }
  }, [open]);

  const adjustMutation = useMutation({
    mutationFn: (delta: number) => adjustAdminUserCredits(user!.id, delta, reason.trim()),
    onSuccess: (result) => {
      toast.success(`算力已调整，当前余额 ${result.balance}`);
      setDeltaInput("");
      setReason("");
      void queryClient.invalidateQueries({ queryKey: ["admin", "user", user?.id] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "user", user?.id, "credit-history"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: () => toast.error("算力调整失败"),
  });

  const applyDelta = (delta: number) => {
    if (!user || delta === 0) return;
    adjustMutation.mutate(delta);
  };

  const submitCustomDelta = () => {
    const delta = Number.parseInt(deltaInput, 10);
    if (!Number.isFinite(delta) || delta === 0) {
      toast.error("请输入非零整数");
      return;
    }
    applyDelta(delta);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{user?.displayName ?? "用户详情"}</DialogTitle>
          <DialogDescription>
            {normalizeUserNo(data?.userNo || user?.userNo) ? (
              <span className="font-mono">{normalizeUserNo(data?.userNo || user?.userNo)}</span>
            ) : null}
            {normalizeUserNo(data?.userNo || user?.userNo) && (data?.phone || user?.phone)
              ? " · "
              : null}
            {data?.phone || user?.phone || "无手机号"}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">加载详情…</p>
        ) : isError || !data ? (
          <p className="text-sm text-destructive">加载用户详情失败</p>
        ) : (
          <UserDetailBody
            userId={user!.id}
            detail={data}
            canAdjustCredits={canAdjustCredits}
            deltaInput={deltaInput}
            reason={reason}
            adjusting={adjustMutation.isPending}
            onDeltaInputChange={setDeltaInput}
            onReasonChange={setReason}
            onSubmitCustomDelta={submitCustomDelta}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function UserDetailBody({
  userId,
  detail,
  canAdjustCredits,
  deltaInput,
  reason,
  adjusting,
  onDeltaInputChange,
  onReasonChange,
  onSubmitCustomDelta,
}: {
  userId: string;
  detail: AdminUserDetail;
  canAdjustCredits: boolean;
  deltaInput: string;
  reason: string;
  adjusting: boolean;
  onDeltaInputChange: (v: string) => void;
  onReasonChange: (v: string) => void;
  onSubmitCustomDelta: () => void;
}) {
  const queryClient = useQueryClient();
  const [historyPage, setHistoryPage] = useState(1);
  const historyPageSize = 10;
  const jobStatusEntries = Object.entries(detail.jobsByStatus);

  const patchMutation = useMutation({
    mutationFn: (data: { role?: string; isActive?: boolean; displayName?: string }) =>
      patchAdminUser(userId, data),
    onSuccess: () => {
      toast.success("用户信息已更新");
      void queryClient.invalidateQueries({ queryKey: ["admin", "user", userId] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (err: Error) => toast.error(err.message || "更新失败"),
  });

  const revokeMutation = useMutation({
    mutationFn: () => revokeAdminUserSessions(userId),
    onSuccess: (result) => {
      toast.success(`已踢出 ${result.revoked} 个会话`);
      void queryClient.invalidateQueries({ queryKey: ["admin", "user", userId] });
    },
    onError: () => toast.error("踢出会话失败"),
  });

  const clearRiskMutation = useMutation({
    mutationFn: () => clearAdminUserAuthRisk(userId),
    onSuccess: (result) => {
      toast.success(`已清除 ${result.phone} 的登录/短信风控锁定`);
    },
    onError: () => toast.error("清除风控失败"),
  });

  const { data: creditHistory, isLoading: historyLoading } = useQuery({
    queryKey: ["admin", "user", userId, "credit-history", historyPage],
    queryFn: () => listAdminUserCreditHistory(userId, historyPage, historyPageSize),
  });

  const historyTotalPages = Math.max(
    1,
    Math.ceil((creditHistory?.total ?? 0) / historyPageSize)
  );

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-border bg-muted/20 p-4">
        <h3 className="mb-3 text-sm font-medium">账号管理</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-[140px] space-y-1">
            <label className="text-xs text-muted-foreground">角色</label>
            <Select
              value={detail.role}
              disabled={patchMutation.isPending || Boolean(detail.isSuperAdmin)}
              onValueChange={(v) => {
                if (v) patchMutation.mutate({ role: v });
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="user">普通用户</SelectItem>
                <SelectItem value="admin">管理员</SelectItem>
              </SelectContent>
            </Select>
            {detail.isSuperAdmin ? (
              <p className="text-[11px] text-muted-foreground">超级管理员不可降权</p>
            ) : null}
          </div>
          <div className="w-[140px] space-y-1">
            <label className="text-xs text-muted-foreground">账号状态</label>
            <Select
              value={detail.isActive ? "active" : "disabled"}
              disabled={patchMutation.isPending || Boolean(detail.isSuperAdmin)}
              onValueChange={(v) => {
                if (!v) return;
                patchMutation.mutate({ isActive: v === "active" });
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active">正常</SelectItem>
                <SelectItem value="disabled">禁用</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={revokeMutation.isPending || detail.sessionCount === 0}
            onClick={() => revokeMutation.mutate()}
          >
            {revokeMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              `踢出会话 (${detail.sessionCount})`
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={clearRiskMutation.isPending || !detail.phone}
            onClick={() => clearRiskMutation.mutate()}
          >
            {clearRiskMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              "清除风控锁定"
            )}
          </Button>
        </div>
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <InfoItem label="用户编号" value={normalizeUserNo(detail.userNo) || "—"} mono />
        <InfoItem label="昵称" value={detail.displayName} />
        <InfoItem label="手机号" value={detail.phone || "—"} />
        <InfoItem label="内部用户 ID" value={detail.id} mono muted />
        <InfoItem label="账号状态" value={detail.isActive ? "正常" : "已禁用"} />
        <InfoItem label="注册时间" value={formatDateTimeCN(detail.createdAt)} />
        <InfoItem label="最后登录" value={formatDateTimeCN(detail.lastLoginAt)} />
        <InfoItem label="最后登录 IP" value={detail.lastLoginIp || "—"} mono />
        <InfoItem label="项目数" value={String(detail.projectCount)} />
        <InfoItem label="任务数" value={String(detail.jobCount)} />
        <InfoItem label="工作流数" value={String(detail.workflowCount)} />
      </section>

      <section className="rounded-lg border border-border bg-muted/20 p-4">
        <div className="flex items-baseline justify-between gap-4">
          <div>
            <p className="text-xs text-muted-foreground">算力余额</p>
            <p className="mt-1 text-3xl font-semibold tabular-nums">{detail.balance}</p>
          </div>
          <Link href="/admin/credits" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            算力对账
          </Link>
        </div>

        {canAdjustCredits ? (
          <div className="mt-4 flex flex-wrap items-end gap-2">
            <div className="min-w-[120px] flex-1 space-y-1">
              <label className="text-xs text-muted-foreground">自定义调整（可负数）</label>
              <Input
                placeholder="例如 500 或 -100"
                value={deltaInput}
                onChange={(e) => onDeltaInputChange(e.target.value)}
              />
            </div>
            <div className="min-w-[160px] flex-[2] space-y-1">
              <label className="text-xs text-muted-foreground">备注（可选）</label>
              <Input
                placeholder="调账原因"
                value={reason}
                onChange={(e) => onReasonChange(e.target.value)}
              />
            </div>
            <Button size="sm" disabled={adjusting} onClick={onSubmitCustomDelta}>
              {adjusting ? <Loader2 className="h-4 w-4 animate-spin" /> : "确认调整"}
            </Button>
          </div>
        ) : (
          <p className="mt-4 text-xs text-muted-foreground">无算力调账权限</p>
        )}
      </section>

      <section>
        <h3 className="mb-2 text-sm font-medium">算力流水</h3>
        <CreditTransactionTable
          items={creditHistory?.items ?? []}
          loading={historyLoading}
          showId={false}
          jobLinkPrefix="/admin/jobs?job_id="
        />
        {(creditHistory?.total ?? 0) > historyPageSize ? (
          <div className="mt-3 flex items-center justify-between text-sm text-muted-foreground">
            <span>
              第 {historyPage} / {historyTotalPages} 页，共 {creditHistory?.total ?? 0} 条
            </span>
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={historyPage <= 1}
                onClick={() => setHistoryPage((p) => Math.max(1, p - 1))}
              >
                上一页
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={historyPage >= historyTotalPages}
                onClick={() => setHistoryPage((p) => p + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        ) : null}
      </section>

      <section>
        <h3 className="mb-2 text-sm font-medium">最近登录审计</h3>
        {!detail.recentAuthEvents?.length ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-4 text-center text-sm text-muted-foreground">
            暂无登录记录
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                  <th className="px-3 py-2 font-medium">结果</th>
                  <th className="px-3 py-2 font-medium">IP</th>
                </tr>
              </thead>
              <tbody>
                {detail.recentAuthEvents.map((ev) => (
                  <tr key={ev.id} className="border-b border-border/60">
                    <td className="px-3 py-2 whitespace-nowrap">{formatDateTimeCN(ev.createdAt)}</td>
                    <td className="px-3 py-2">{authActionLabel(ev.action)}</td>
                    <td className="px-3 py-2">
                      <Badge variant={ev.result === "success" ? "outline" : "destructive"}>
                        {ev.result === "success" ? "成功" : "失败"}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{ev.ip || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {jobStatusEntries.length > 0 ? (
        <section>
          <h3 className="mb-2 text-sm font-medium">任务状态分布</h3>
          <div className="flex flex-wrap gap-2">
            {jobStatusEntries.map(([status, count]) => (
              <Badge key={status} variant="outline">
                {status} {count}
              </Badge>
            ))}
          </div>
        </section>
      ) : null}

      <section>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-medium">最近任务</h3>
          <div className="flex gap-2">
            <Link
              href={`/admin/projects?search=${encodeURIComponent(detail.phone || detail.displayName)}`}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              查看项目
            </Link>
            <Link
              href={`/admin/jobs?user_id=${encodeURIComponent(normalizeUserNo(detail.userNo) || detail.id)}`}
              className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
            >
              查看任务
            </Link>
          </div>
        </div>
        {detail.recentJobs.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
            暂无生成任务
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">提交号</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 font-medium">类型</th>
                  <th className="px-3 py-2 font-medium">算力</th>
                  <th className="px-3 py-2 font-medium">时间</th>
                </tr>
              </thead>
              <tbody>
                {detail.recentJobs.map((job) => (
                  <tr key={job.id} className="border-b border-border/60">
                    <td className="px-3 py-2 font-mono text-xs">{formatJobDisplayId(job)}</td>
                    <td className="px-3 py-2">
                      <Badge variant="outline">{job.status}</Badge>
                    </td>
                    <td className="px-3 py-2">{job.lane}</td>
                    <td className="px-3 py-2 tabular-nums">{job.creditCost}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{formatDateTimeCN(job.createdAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function InfoItem({
  label,
  value,
  mono,
  muted,
}: {
  label: string;
  value: string;
  mono?: boolean;
  muted?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={`mt-0.5 text-sm ${mono ? "font-mono text-xs break-all" : ""} ${muted ? "text-muted-foreground" : ""}`}
      >
        {value}
      </p>
    </div>
  );
}

export default function AdminUsersPage() {
  // 筛选条件写入 sessionStorage，切换管理后台选项卡后再回来时保留输入与结果
  const [query, setQuery] = usePersistedAdminQuery(
    "canvas-admin-users-query-v1",
    USERS_QUERY_DEFAULTS
  );
  const { searchInput, search, role, activeFilter, page } = query;
  const [selected, setSelected] = useState<AdminUserListItem | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const pageSize = 20;

  useEffect(() => {
    const timer = setTimeout(() => {
      const next = searchInput.trim();
      setQuery((prev) => {
        if (prev.search === next) return prev;
        return { ...prev, search: next, page: 1 };
      });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput, setQuery]);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["admin", "users", search, role, activeFilter, page],
    queryFn: () =>
      listAdminUsers({
        search: search || undefined,
        role: role || undefined,
        isActive: activeFilter === "" ? undefined : activeFilter === "active",
        page,
        pageSize,
        sort: "created_at",
        order: "desc",
      }),
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  const openDetail = (user: AdminUserListItem) => {
    setSelected(user);
    setDetailOpen(true);
  };

  return (
    <>
      <AdminHeader
        title="用户管理"
        description="查看注册用户、算力余额与使用情况；支持手动调账。"
      />

      <RegistrationSettingsBar />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-[240px] flex-1 space-y-1">
          <label className="text-xs text-muted-foreground">用户搜索</label>
          <Input
            placeholder="昵称 / 手机号 / 用户编号"
            value={searchInput}
            onChange={(e) => setQuery({ searchInput: e.target.value })}
          />
        </div>
        <div className="w-[140px] space-y-1">
          <label className="text-xs text-muted-foreground">角色</label>
          <Select
            value={role || "all"}
            onValueChange={(v) => {
              if (!v) return;
              setQuery({ role: v === "all" ? "" : v, page: 1 });
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="user">普通用户</SelectItem>
              <SelectItem value="admin">管理员</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="w-[140px] space-y-1">
          <label className="text-xs text-muted-foreground">状态</label>
          <Select
            value={activeFilter || "all"}
            onValueChange={(v) => {
              if (!v) return;
              setQuery({ activeFilter: v === "all" ? "" : v, page: 1 });
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部</SelectItem>
              <SelectItem value="active">正常</SelectItem>
              <SelectItem value="disabled">已禁用</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
          刷新
        </Button>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[1040px] text-left text-sm">
          <thead className="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">用户</th>
              <th className="px-4 py-3 font-medium">用户编号</th>
              <th className="px-4 py-3 font-medium">手机号</th>
              <th className="px-4 py-3 font-medium">角色</th>
              <th className="px-4 py-3 font-medium">状态</th>
              <th className="px-4 py-3 font-medium">算力</th>
              <th className="px-4 py-3 font-medium">项目</th>
              <th className="px-4 py-3 font-medium">任务</th>
              <th className="px-4 py-3 font-medium">注册时间</th>
              <th className="px-4 py-3 font-medium">最后登录</th>
              <th className="px-4 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={11} className="px-4 py-8 text-center text-muted-foreground">
                  加载中…
                </td>
              </tr>
            ) : isError ? (
              <tr>
                <td colSpan={11} className="px-4 py-8 text-center text-destructive">
                  加载用户列表失败
                </td>
              </tr>
            ) : !data?.items.length ? (
              <tr>
                <td colSpan={11} className="px-4 py-8 text-center text-muted-foreground">
                  暂无用户
                </td>
              </tr>
            ) : (
              data.items.map((user) => (
                <tr key={user.id} className="border-b border-border/60 hover:bg-muted/20">
                  <td className="px-4 py-3 font-medium">{user.displayName}</td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {normalizeUserNo(user.userNo) || "—"}
                  </td>
                  <td className="px-4 py-3 tabular-nums">{user.phone || "—"}</td>
                  <td className="px-4 py-3">{roleBadge(user.role)}</td>
                  <td className="px-4 py-3">{statusBadge(user.isActive)}</td>
                  <td className="px-4 py-3 tabular-nums font-medium">{user.balance}</td>
                  <td className="px-4 py-3 tabular-nums">{user.projectCount}</td>
                  <td className="px-4 py-3 tabular-nums">{user.jobCount}</td>
                  <td className="px-4 py-3 whitespace-nowrap">{formatDateTimeCN(user.createdAt)}</td>
                  <td className="px-4 py-3 whitespace-nowrap">{formatDateTimeCN(user.lastLoginAt)}</td>
                  <td className="px-4 py-3">
                    <Button variant="outline" size="sm" onClick={() => openDetail(user)}>
                      详情
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {data ? (
        <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
          <span>
            共 {data.total} 条 · 第 {data.page} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setQuery({ page: Math.max(1, page - 1) })}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setQuery({ page: page + 1 })}
            >
              下一页
            </Button>
          </div>
        </div>
      ) : null}

      <UserDetailDialog user={selected} open={detailOpen} onOpenChange={setDetailOpen} />
    </>
  );
}
