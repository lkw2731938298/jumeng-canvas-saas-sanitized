"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Clock,
  Coins,
  KeyRound,
  Loader2,
  LogOut,
  Sparkles,
  User,
  UserPlus,
  Zap,
} from "lucide-react";
import { ThemePageBackdrop } from "@/components/theme/ThemePageBackdrop";
import { withBasePath } from "@/lib/basePath";
import { normalizeUserNo } from "@/lib/admin/displayIds";
import { AppThemePicker } from "@/components/user/AppThemePicker";
import { CreditActivitiesPanel } from "@/components/user/CreditActivitiesPanel";
import { CreditConsumePriorityEditor } from "@/components/user/CreditConsumePriorityEditor";
import { CreditTransactionHistoryPanel } from "@/components/user/CreditTransactionHistoryPanel";
import { MembershipPanel } from "@/components/user/MembershipPanel";
import { LocalModelsPanel } from "@/components/user/LocalModelsPanel";
import { StorageQuotaPanel } from "@/components/user/StorageQuotaPanel";
import { CreditRechargeDialog } from "@/components/user/CreditRechargeDialog";
import { UserAccountMenu } from "@/components/user/UserAccountMenu";
import { useAppTheme } from "@/components/providers/AppThemeProvider";
import { useCreditBalance } from "@/lib/canvas/useGenerationCreditQuote";
import * as authApi from "@/lib/api/auth";
import { formatCreditTypeLabel } from "@/lib/api/credits";
import { userFacingModelLabel, userFacingModelSuffix } from "@/lib/credits/userFacingModelLabel";
import { performLogout } from "@/lib/auth/session";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";

function maskPhone(phone?: string): string {
  if (!phone || phone.length < 7) return "未绑定手机号";
  return `${phone.slice(0, 3)}****${phone.slice(-4)}`;
}

function GlassCard({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "flex h-full flex-col rounded-2xl border border-border bg-card/80 p-5 backdrop-blur-xl text-foreground",
        className
      )}
      style={{ WebkitBackdropFilter: "blur(24px)" }}
    >
      {children}
    </section>
  );
}

function CardTitle({ children, action }: { children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <p className="text-sm font-medium text-foreground/80">{children}</p>
      {action}
    </div>
  );
}

function BreakdownRow({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <div className="min-w-0">
        <p className="text-sm text-foreground/80">{label}</p>
        {hint ? <p className="text-[11px] text-muted-foreground">{hint}</p> : null}
      </div>
      <span className="shrink-0 font-mono text-sm text-foreground">{value.toLocaleString()}</span>
    </div>
  );
}

export default function AccountPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const { themeId, selectTheme } = useAppTheme();
  const { data: creditBalance, isLoading: balanceLoading } = useCreditBalance();
  const [rechargeOpen, setRechargeOpen] = useState(false);

  const { data: me, isLoading: meLoading } = useQuery({
    queryKey: ["auth", "me"],
    queryFn: authApi.getMe,
  });

  useEffect(() => {
    if (me) setUser(me);
  }, [me, setUser]);

  const balance =
    creditBalance?.creditsEnabled && creditBalance.balance != null
      ? creditBalance.balance
      : (user?.computePower ?? 0);
  const breakdown = creditBalance?.breakdown;
  const displayName = user?.displayName ?? "用户";
  const loading = meLoading && !user;

  const handleLogout = async () => {
    await performLogout(queryClient);
    router.push("/login");
  };

  const handleSwitchUser = async () => {
    await performLogout(queryClient);
    router.push("/login");
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <ThemePageBackdrop />

      <div className="fixed top-0 left-0 z-50 flex items-center gap-4 px-6 py-4">
        <button
          type="button"
          onClick={() => router.push("/projects")}
          className="flex items-center gap-2 text-sm text-foreground/70 transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          返回项目
        </button>
        {/* 品牌 Logo：个人中心顶栏，高度与项目页一致（20px） */}
        <button
          type="button"
          onClick={() => router.push("/projects")}
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

      <div className="fixed top-0 right-0 z-50 flex items-center gap-4 px-6 py-4">
        <div
          className="flex items-center gap-2 rounded-full border border-border bg-card/70 px-3 py-1.5 text-sm text-foreground/70"
          style={{ backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)" }}
        >
          <Zap className="h-3.5 w-3.5 text-primary" />
          <span className="mr-0.5 text-xs text-muted-foreground">剩余算力</span>
          <span className="font-mono text-foreground">
            {balanceLoading ? "…" : balance.toLocaleString()}
          </span>
        </div>
        <UserAccountMenu
          displayName={displayName}
          variant="canvas"
          onLogout={() => {
            void performLogout(queryClient).then(() => router.push("/login"));
          }}
        />
      </div>

      <main className="relative z-10 mx-auto w-full max-w-6xl px-4 pb-16 pt-28">
        <h1 className="mb-8 text-center text-2xl font-bold tracking-wide text-foreground">
          个人中心
        </h1>

        {loading ? (
          <div className="flex items-center justify-center py-20 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            加载中…
          </div>
        ) : (
          /* 均匀网格：同排等高卡片，流水全宽完整展示 */
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 lg:gap-5">
            <GlassCard className="md:col-span-1">
              <div className="flex items-center gap-4">
                <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full border border-border bg-muted">
                  <User className="h-7 w-7 text-foreground/70" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-lg font-semibold text-foreground">{displayName}</p>
                  <p className="text-sm text-muted-foreground">{maskPhone(user?.phone)}</p>
                  {normalizeUserNo(user?.userNo) ? (
                    <p className="mt-1 font-mono text-[11px] text-muted-foreground/70">
                      用户编号 {normalizeUserNo(user?.userNo)}
                    </p>
                  ) : null}
                </div>
              </div>
            </GlassCard>

            <GlassCard className="md:col-span-1 lg:col-span-2">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">总算力</p>
                  <p className="mt-1 font-mono text-3xl font-semibold text-foreground">
                    {balanceLoading ? "…" : balance.toLocaleString()}
                  </p>
                  {creditBalance?.creditsEnabled === false ? (
                    <p className="mt-1 text-xs text-muted-foreground">当前为免扣费模式</p>
                  ) : (
                    <p className="mt-1 text-xs text-muted-foreground">含限时与永久算力</p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => setRechargeOpen(true)}
                  className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-foreground transition-colors hover:bg-muted"
                >
                  <Coins className="h-4 w-4 text-amber-400" />
                  充值通用算力
                </button>
              </div>

              {breakdown && creditBalance?.creditsEnabled !== false ? (
                <div className="mt-5 grid gap-x-6 sm:grid-cols-2">
                  <div className="divide-y divide-border border-t border-border pt-3 sm:border-t-0 sm:pt-0">
                    <BreakdownRow
                      label={formatCreditTypeLabel("activity")}
                      value={breakdown.activity}
                      hint="活动领取，限时有效"
                    />
                    <BreakdownRow
                      label={formatCreditTypeLabel("model_specific")}
                      value={breakdown.modelSpecific}
                      hint="仅可用于指定模型"
                    />
                  </div>
                  <div className="divide-y divide-border border-t border-border pt-3 sm:border-t-0 sm:pt-0">
                    <BreakdownRow
                      label={formatCreditTypeLabel("subscription")}
                      value={breakdown.subscription}
                      hint="会员订阅赠送，限时有效"
                    />
                    <BreakdownRow
                      label={formatCreditTypeLabel("general")}
                      value={breakdown.general}
                      hint="充值获得，永久有效"
                    />
                  </div>
                </div>
              ) : null}

              {creditBalance?.modelSpecific && creditBalance.modelSpecific.length > 0 ? (
                <div className="mt-4 rounded-xl border border-border bg-card/70 p-3">
                  <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                    <Sparkles className="h-3.5 w-3.5" />
                    模型专用明细
                  </p>
                  <div className="space-y-2">
                    {creditBalance.modelSpecific.map((row) => {
                      const modelLabel = userFacingModelLabel({
                        modelDisplayName: row.modelDisplayName,
                        model: row.model,
                      });
                      return (
                      <div
                        key={row.model}
                        className="flex items-center justify-between gap-2 text-sm"
                      >
                        <span className="break-words text-foreground/80">
                          {modelLabel ?? "模型专用算力"}
                        </span>
                        <span className="shrink-0 font-mono text-foreground">
                          {row.balance.toLocaleString()}
                        </span>
                      </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {creditBalance?.expiringSoon && creditBalance.expiringSoon.length > 0 ? (
                <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/5 p-3">
                  <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-amber-200/80">
                    <Clock className="h-3.5 w-3.5" />
                    即将过期
                  </p>
                  <div className="space-y-1.5">
                    {creditBalance.expiringSoon.slice(0, 5).map((row, idx) => (
                      <div
                        key={`${row.type}-${row.expiresAt}-${idx}`}
                        className="flex items-center justify-between gap-2 text-xs text-foreground/70"
                      >
                        <span className="break-words">
                          {formatCreditTypeLabel(row.type)}
                          {userFacingModelSuffix({
                            modelDisplayName: row.modelDisplayName,
                            model: row.model,
                          })}
                        </span>
                        <span className="shrink-0 font-mono">
                          {row.amount} · {new Date(row.expiresAt).toLocaleDateString()}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </GlassCard>

            <GlassCard className="overflow-hidden p-0">
              <p className="border-b border-border px-5 py-3 text-sm font-medium text-foreground/70">
                偏好设置
              </p>
              <div className="flex-1 px-2 py-1">
                <AppThemePicker
                  value={themeId}
                  onChange={selectTheme}
                  itemClass="text-foreground/80 hover:bg-muted hover:text-foreground"
                  labelClass="text-muted-foreground"
                />
              </div>
            </GlassCard>

            <GlassCard className="space-y-1 p-2">
              <p className="px-3 py-2 text-sm font-medium text-foreground/70">账号安全</p>
              <Link
                href="/reset-password"
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-muted hover:text-foreground"
              >
                <KeyRound className="h-4 w-4 text-muted-foreground" />
                修改密码
              </Link>
              <button
                type="button"
                onClick={() => void handleSwitchUser()}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-muted hover:text-foreground"
              >
                <UserPlus className="h-4 w-4 text-muted-foreground" />
                切换用户
              </button>
              <button
                type="button"
                onClick={() => void handleLogout()}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-red-400 transition-colors hover:bg-red-400/10"
              >
                <LogOut className="h-4 w-4" />
                退出登录
              </button>
            </GlassCard>

            <GlassCard>
              <CardTitle>算力消耗顺序</CardTitle>
              <CreditConsumePriorityEditor />
            </GlassCard>

            <GlassCard>
              <CardTitle
                action={
                  <Link
                    href="/activities"
                    className="text-xs text-muted-foreground transition-colors hover:text-foreground/70"
                  >
                    查看全部 →
                  </Link>
                }
              >
                算力活动
              </CardTitle>
              <div className="min-h-0 flex-1">
                <CreditActivitiesPanel />
              </div>
            </GlassCard>

            <GlassCard>
              <CardTitle>云存储空间</CardTitle>
              <StorageQuotaPanel />
            </GlassCard>

            <GlassCard>
              <CardTitle>会员订阅</CardTitle>
              <MembershipPanel />
            </GlassCard>

            <GlassCard className="md:col-span-2 lg:col-span-3">
              <CardTitle>本地模型</CardTitle>
              <LocalModelsPanel />
            </GlassCard>

            {/* 全宽：避免半栏挤压导致备注/任务号被截断 */}
            <GlassCard className="md:col-span-2 lg:col-span-3">
              <CardTitle>算力变动记录</CardTitle>
              <CreditTransactionHistoryPanel variant="account" pageSize={15} />
            </GlassCard>
          </div>
        )}
      </main>

      <CreditRechargeDialog open={rechargeOpen} onOpenChange={setRechargeOpen} />
    </div>
  );
}
