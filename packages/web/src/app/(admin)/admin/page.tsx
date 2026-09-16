"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Coins, Sparkles, TrendingDown, Users, Zap } from "lucide-react";
import { AdminHeader } from "@/components/admin/AdminShell";
import {
  CreditFlowCard,
  CreditTypeBarChart,
  JobStatusDonut,
  MetricCard,
  TrendAreaChart,
  buildGrowthSeries,
} from "@/components/admin/AdminDashboardCharts";
import { getAdminStats } from "@/lib/api/admin";
import { cn } from "@/lib/utils";

type TrendRange = 7 | 30;
/** 指标卡点击聚焦的图表区域 */
type FocusKey = "users" | "jobs" | "credits" | "consume" | null;

export default function AdminDashboardPage() {
  const [range, setRange] = useState<TrendRange>(30);
  const [focus, setFocus] = useState<FocusKey>(null);
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin", "stats"],
    queryFn: getAdminStats,
  });

  const series = data ? buildGrowthSeries(data) : null;
  const jobTotal = data ? Object.values(data.jobs ?? {}).reduce((a, b) => a + b, 0) : 0;

  const toggleFocus = (key: Exclude<FocusKey, null>) => {
    setFocus((prev) => (prev === key ? null : key));
  };

  const creditSeries = useMemo(() => {
    if (!series) return [];
    return [series.creditConsume, series.creditGrant];
  }, [series]);

  return (
    <>
      <AdminHeader
        title="仪表盘"
        description="画布系统运营概览 · 用户增长与算力流转实时洞察"
      />

      {isLoading ? (
        <DashboardSkeleton />
      ) : isError ? (
        <p className="text-sm text-destructive">加载统计数据失败</p>
      ) : data && series ? (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              label="注册用户"
              value={data.userCount}
              delta={data.usersToday}
              deltaHint={`今日新增 · 近7日 ${data.usersWeek ?? 0} · 近30日 ${data.usersMonth ?? 0}`}
              icon={Users}
              accent="#0f766e"
              active={focus === "users"}
              onClick={() => toggleFocus("users")}
            />
            <MetricCard
              label="生成任务"
              value={jobTotal}
              delta={data.jobsToday}
              deltaHint={`今日提交 · 近7日 ${data.jobsWeek ?? 0} · 近30日 ${data.jobsMonth ?? 0}`}
              icon={Sparkles}
              accent="#b45309"
              active={focus === "jobs"}
              onClick={() => toggleFocus("jobs")}
            />
            <MetricCard
              label="算力库存"
              value={data.creditsRemainingTotal ?? 0}
              delta={data.creditsGrantedToday ?? 0}
              deltaHint={`今日发放 · 近7日消耗 ${(data.creditsConsumedWeek ?? 0).toLocaleString("zh-CN")}`}
              icon={Coins}
              accent="#1d4ed8"
              active={focus === "credits"}
              onClick={() => toggleFocus("credits")}
            />
            <MetricCard
              label="今日消耗算力"
              value={data.creditsConsumedToday ?? 0}
              deltaHint={`近7日 ${(data.creditsConsumedWeek ?? 0).toLocaleString("zh-CN")} · 近30日 ${(data.creditsConsumedMonth ?? 0).toLocaleString("zh-CN")}`}
              icon={TrendingDown}
              accent="#c2410c"
              active={focus === "consume"}
              onClick={() => toggleFocus("consume")}
            />
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold tracking-tight">趋势洞察</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                按东八区日历日聚合 · 点击图例可显隐序列 · 点击上方指标卡聚焦
              </p>
            </div>
            <div className="inline-flex rounded-lg border border-border bg-muted/40 p-0.5">
              {([7, 30] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setRange(d)}
                  className={cn(
                    "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                    range === d
                      ? "bg-background text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                >
                  近 {d} 日
                </button>
              ))}
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <TrendAreaChart
              title="用户注册趋势"
              subtitle={`近 ${range} 日每日新增注册`}
              days={range}
              series={[series.users]}
              highlighted={focus === "users"}
            />
            <TrendAreaChart
              title="生成任务趋势"
              subtitle={`近 ${range} 日每日任务提交量`}
              days={range}
              series={[series.jobs]}
              highlighted={focus === "jobs"}
            />
          </div>

          <TrendAreaChart
            title="算力消耗 / 发放趋势"
            subtitle="点击图例切换显示 · 悬停图例高亮对应曲线"
            days={range}
            series={creditSeries}
            highlighted={focus === "credits" || focus === "consume"}
            emptyHint="近窗口暂无算力流水"
          />

          <div className="grid gap-4 lg:grid-cols-3">
            <JobStatusDonut jobs={data.jobs ?? {}} highlighted={focus === "jobs"} />
            <CreditTypeBarChart
              creditsByType={data.creditsByType ?? {}}
              highlighted={focus === "credits"}
            />
            <CreditFlowCard
              consumedWeek={data.creditsConsumedWeek ?? 0}
              grantedWeek={data.creditsGrantedWeek ?? 0}
              remaining={data.creditsRemainingTotal ?? 0}
              highlighted={focus === "credits" || focus === "consume"}
            />
          </div>

          <div className="flex items-center gap-2 rounded-xl border border-border/70 bg-muted/30 px-4 py-3 text-xs text-muted-foreground">
            <Zap className="h-3.5 w-3.5 shrink-0 text-amber-600" />
            项目 {data.projectCount.toLocaleString("zh-CN")} · 工作流{" "}
            {data.workflowCount.toLocaleString("zh-CN")} · 近7日任务成功{" "}
            {(data.jobsSucceededWeek ?? 0).toLocaleString("zh-CN")} / 失败{" "}
            {(data.jobsFailedWeek ?? 0).toLocaleString("zh-CN")}
          </div>
        </div>
      ) : null}
    </>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6 animate-pulse">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-[130px] rounded-2xl border border-border/60 bg-muted/40" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <div className="h-[320px] rounded-2xl border border-border/60 bg-muted/40" />
        <div className="h-[320px] rounded-2xl border border-border/60 bg-muted/40" />
      </div>
      <div className="h-[320px] rounded-2xl border border-border/60 bg-muted/40" />
      <div className="grid gap-4 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-[280px] rounded-2xl border border-border/60 bg-muted/40" />
        ))}
      </div>
    </div>
  );
}
