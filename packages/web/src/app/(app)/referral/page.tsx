"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Copy,
  Gift,
  Loader2,
  Share2,
  Users,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ThemePageBackdrop } from "@/components/theme/ThemePageBackdrop";
import { withBasePath } from "@/lib/basePath";
import { getReferralMe } from "@/lib/api/referral";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { cn } from "@/lib/utils";

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
        "rounded-2xl border border-border bg-card/80 p-5 backdrop-blur-xl text-foreground",
        className
      )}
      style={{ WebkitBackdropFilter: "blur(24px)" }}
    >
      {children}
    </section>
  );
}

export default function ReferralPage() {
  const router = useRouter();
  const [copied, setCopied] = useState<"code" | "link" | null>(null);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["referral", "me"],
    queryFn: getReferralMe,
  });

  const copy = async (text: string, kind: "code" | "link") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      toast.success(kind === "code" ? "邀请码已复制" : "邀请链接已复制");
      setTimeout(() => setCopied(null), 1800);
    } catch {
      toast.error("复制失败，请手动选择复制");
    }
  };

  const campaign = data?.campaign;

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <ThemePageBackdrop />

      <div className="fixed left-0 top-0 z-50 flex items-center gap-4 px-6 py-4">
        <button
          type="button"
          onClick={() => router.push("/account")}
          className="flex items-center gap-2 text-sm text-foreground/70 transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          个人中心
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

      <main className="relative z-10 mx-auto w-full max-w-4xl px-4 pb-20 pt-28">
        <div className="mb-8 text-center">
          <p className="mb-2 text-xs uppercase tracking-[0.2em] text-muted-foreground">Invite Friends</p>
          <h1 className="text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
            {campaign?.title || "用户邀请"}
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm leading-relaxed text-muted-foreground">
            {campaign?.description?.trim() ||
              "分享专属邀请码，好友注册后双方可获得算力奖励。活动规则以页面展示为准。"}
          </p>
        </div>

        {isLoading ? (
          <div className="flex justify-center py-24 text-muted-foreground">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : isError || !data ? (
          <GlassCard className="text-center text-sm text-muted-foreground">加载失败，请稍后重试</GlassCard>
        ) : (
          <div className="space-y-5">
            {campaign?.coverUrl ? (
              <GlassCard className="overflow-hidden p-0">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={campaign.coverUrl}
                  alt=""
                  className="h-40 w-full object-cover sm:h-52"
                />
              </GlassCard>
            ) : null}

            <div className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
              <GlassCard>
                <div className="mb-4 flex items-center gap-2 text-sm text-foreground/70">
                  <Share2 className="h-4 w-4 text-primary" />
                  我的邀请码
                </div>
                <p className="mb-1 font-mono text-4xl font-semibold tracking-[0.35em] text-foreground sm:text-5xl">
                  {data.inviteCode}
                </p>
                <p className="mb-5 text-xs text-muted-foreground">好友注册时填写，或直接分享下方链接</p>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <button
                    type="button"
                    onClick={() => copy(data.inviteCode, "code")}
                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground transition hover:bg-muted"
                  >
                    {copied === "code" ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    复制邀请码
                  </button>
                  <button
                    type="button"
                    onClick={() => copy(data.shareUrl, "link")}
                    className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:bg-primary/90"
                  >
                    {copied === "link" ? <Check className="h-4 w-4" /> : <Share2 className="h-4 w-4" />}
                    复制邀请链接
                  </button>
                </div>
                <p className="mt-4 break-all rounded-xl border border-border/60 bg-muted/40 px-3 py-2 font-mono text-[11px] text-muted-foreground">
                  {data.shareUrl}
                </p>
              </GlassCard>

              <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-1">
                <GlassCard>
                  <div className="flex items-center gap-2 text-sm text-foreground/70">
                    <Users className="h-4 w-4" />
                    成功邀请
                  </div>
                  <p className="mt-3 font-mono text-3xl text-foreground">{data.inviteCount}</p>
                  <p className="mt-1 text-xs text-muted-foreground">人</p>
                </GlassCard>
                <GlassCard>
                  <div className="flex items-center gap-2 text-sm text-foreground/70">
                    <Gift className="h-4 w-4" />
                    已获奖励
                  </div>
                  <p className="mt-3 font-mono text-3xl text-foreground">
                    {data.rewardedCredits.toLocaleString()}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">算力</p>
                  {campaign?.remainingQuota != null ? (
                    <p className="mt-3 text-xs text-muted-foreground">
                      活动剩余名额 {campaign.remainingQuota}
                    </p>
                  ) : null}
                </GlassCard>
              </div>
            </div>

            {campaign ? (
              <GlassCard>
                <h2 className="mb-3 text-sm font-medium text-foreground/80">活动说明</h2>
                <ul className="space-y-2 text-sm text-muted-foreground">
                  <li>
                    邀请人奖励：{campaign.inviterRewardAmount} 算力
                    {campaign.inviterRewardOn === "invitee_first_recharge"
                      ? "（好友完成首充后发放）"
                      : "（好友注册成功后发放）"}
                  </li>
                  <li>被邀请人奖励：{campaign.inviteeRewardAmount} 算力（注册成功后发放）</li>
                  <li>
                    算力类型有效期：
                    {campaign.rewardCreditType === "general"
                      ? "永久通用算力"
                      : `${campaign.rewardValidDays} 天`}
                  </li>
                  {campaign.maxRewardsPerInviter != null ? (
                    <li>每人最多获得 {campaign.maxRewardsPerInviter} 次邀请人奖励</li>
                  ) : null}
                </ul>
              </GlassCard>
            ) : (
              <GlassCard className="text-sm text-muted-foreground">
                当前暂无进行中的邀请活动，你仍可分享邀请码；开启活动后将按规则自动发奖。
              </GlassCard>
            )}

            <GlassCard>
              <h2 className="mb-4 text-sm font-medium text-foreground/80">邀请明细</h2>
              {data.invites.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border px-4 py-10 text-center text-sm text-muted-foreground">
                  还没有好友通过你的邀请码注册，复制链接分享给他们吧。
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[520px] text-left text-sm">
                    <thead>
                      <tr className="border-b border-border text-xs text-muted-foreground">
                        <th className="pb-3 font-medium">用户</th>
                        <th className="pb-3 font-medium">手机</th>
                        <th className="pb-3 font-medium">注册时间</th>
                        <th className="pb-3 font-medium">奖励状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.invites.map((row) => (
                        <tr key={row.inviteeUserId} className="border-b border-border/60 text-foreground/75">
                          <td className="py-3 pr-3">{row.displayName}</td>
                          <td className="py-3 pr-3 font-mono text-xs text-muted-foreground">{row.phoneMasked}</td>
                          <td className="py-3 pr-3 text-xs text-muted-foreground">
                            {row.registeredAt ? formatDateTimeCN(row.registeredAt) : "—"}
                          </td>
                          <td className="py-3">
                            <span className="rounded-full border border-border bg-muted/50 px-2 py-0.5 text-xs">
                              {row.statusLabel}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </GlassCard>
          </div>
        )}
      </main>
    </div>
  );
}
