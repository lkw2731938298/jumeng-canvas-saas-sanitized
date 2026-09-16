"use client";

import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Sector,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { cn } from "@/lib/utils";
import { jobStatusLabel } from "@/lib/admin/jobLabels";
import type { AdminStats, AdminStatsDailyPoint } from "@/types/admin";

/** 仪表盘图表配色：青绿主色 + 琥珀/石板 */
const CHART = {
  users: "#0f766e",
  projects: "#1d4ed8",
  jobs: "#b45309",
  consume: "#c2410c",
  grant: "#0f766e",
  grid: "rgba(148, 163, 184, 0.22)",
  axis: "#94a3b8",
  tooltipBg: "rgba(15, 23, 42, 0.94)",
  tooltipBorder: "rgba(148, 163, 184, 0.28)",
} as const;

const STATUS_COLORS: Record<string, string> = {
  succeeded: "#059669",
  failed: "#dc2626",
  polling: "#0891b2",
  running: "#0284c7",
  pending: "#64748b",
  abnormal: "#ea580c",
};

const CREDIT_TYPE_META: Record<string, { label: string; color: string }> = {
  general: { label: "通用算力", color: "#0f766e" },
  subscription: { label: "会员算力", color: "#1d4ed8" },
  activity: { label: "活动算力", color: "#b45309" },
  model_specific: { label: "模型专用", color: "#0e7490" },
};

/** 消除 Recharts 点击后出现的白框 / 默认 focus 描边 */
const chartShellClass =
  "[&_.recharts-wrapper]:outline-none [&_.recharts-surface]:outline-none [&_.recharts-sector]:outline-none [&_.recharts-layer]:outline-none [&_svg]:outline-none [&_*]:outline-none";

const tooltipWrapperStyle: CSSProperties = {
  outline: "none",
  background: "transparent",
  border: "none",
  boxShadow: "none",
  padding: 0,
  zIndex: 40,
  pointerEvents: "none",
};

function formatCompact(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万`;
  if (n >= 1000) return `${(n / 1000).toFixed(n % 1000 === 0 ? 0 : 1)}k`;
  return String(n);
}

function shortDate(iso: string): string {
  const parts = iso.split("-");
  if (parts.length < 3) return iso;
  return `${Number(parts[1])}/${Number(parts[2])}`;
}

function sliceTrend(points: AdminStatsDailyPoint[] | undefined, days: number) {
  const list = points ?? [];
  return list.slice(Math.max(0, list.length - days));
}

function creditTypeLabel(type: string): string {
  return CREDIT_TYPE_META[type]?.label ?? type;
}

function creditTypeColor(type: string): string {
  return CREDIT_TYPE_META[type]?.color ?? "#64748b";
}

function ChartCard({
  title,
  subtitle,
  actions,
  children,
  className,
  highlighted,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  highlighted?: boolean;
}) {
  return (
    <section
      className={cn(
        "rounded-2xl border bg-card p-5 shadow-[0_1px_0_rgba(15,23,42,0.04)] transition-all duration-300",
        highlighted
          ? "border-teal-600/40 ring-2 ring-teal-600/15 shadow-md"
          : "border-border/80",
        className
      )}
    >
      <div className="mb-4 flex items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          {subtitle ? <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{
    name?: string;
    value?: number;
    color?: string;
    payload?: { color?: string; name?: string; fill?: string };
  }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const title = label || payload[0]?.name || payload[0]?.payload?.name || "";
  return (
    <div
      className="rounded-lg px-3 py-2 text-xs shadow-xl backdrop-blur-sm"
      style={{ background: CHART.tooltipBg, border: `1px solid ${CHART.tooltipBorder}`, color: "#f8fafc" }}
    >
      {title ? <p className="mb-1.5 font-medium text-slate-200">{title}</p> : null}
      {payload.map((item, idx) => {
        const color = item.color || item.payload?.color || item.payload?.fill || "#94a3b8";
        const name = item.name || item.payload?.name || "数值";
        return (
          <div key={`${name}-${idx}`} className="flex items-center justify-between gap-6 py-0.5">
            <span className="flex items-center gap-1.5 text-slate-300">
              <span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} />
              {name}
            </span>
            <span className="tabular-nums font-semibold">
              {(item.value ?? 0).toLocaleString("zh-CN")}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  delta,
  deltaHint,
  icon: Icon,
  accent,
  active,
  onClick,
}: {
  label: string;
  value: number | string;
  delta?: number;
  deltaHint?: string;
  icon: LucideIcon;
  accent: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const interactive = Boolean(onClick);
  return (
    <button
      type="button"
      disabled={!interactive}
      onClick={onClick}
      className={cn(
        "group relative w-full overflow-hidden rounded-2xl border bg-card p-5 text-left shadow-[0_1px_0_rgba(15,23,42,0.04)] transition-all duration-300",
        interactive && "cursor-pointer hover:-translate-y-0.5 hover:shadow-md",
        !interactive && "cursor-default",
        active ? "border-teal-600/45 ring-2 ring-teal-600/15" : "border-border/80"
      )}
    >
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-px opacity-80"
        style={{ background: `linear-gradient(90deg, transparent, ${accent}, transparent)` }}
      />
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
          <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums text-foreground">
            {typeof value === "number" ? value.toLocaleString("zh-CN") : value}
          </p>
          {deltaHint ? (
            <p className="mt-2 text-xs text-muted-foreground">
              {typeof delta === "number" ? (
                <span
                  className={cn(
                    "mr-1.5 inline-flex items-center rounded-md px-1.5 py-0.5 font-medium tabular-nums",
                    delta > 0
                      ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                      : delta < 0
                        ? "bg-rose-500/10 text-rose-700 dark:text-rose-400"
                        : "bg-muted text-muted-foreground"
                  )}
                >
                  {delta > 0 ? "+" : ""}
                  {delta.toLocaleString("zh-CN")}
                </span>
              ) : null}
              {deltaHint}
            </p>
          ) : null}
          {interactive ? (
            <p className="mt-2 text-[11px] text-muted-foreground/80">
              {active ? "已聚焦相关图表 · 再点取消" : "点击聚焦相关图表"}
            </p>
          ) : null}
        </div>
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl transition-transform duration-300 group-hover:scale-105"
          style={{ background: `${accent}18`, color: accent }}
        >
          <Icon className="h-5 w-5" strokeWidth={1.75} />
        </div>
      </div>
    </button>
  );
}

type SeriesDef = { key: string; name: string; color: string; points: AdminStatsDailyPoint[] };

export function TrendAreaChart({
  title,
  subtitle,
  series,
  days,
  highlighted,
  emptyHint = "暂无数据",
}: {
  title: string;
  subtitle?: string;
  series: SeriesDef[];
  days: number;
  highlighted?: boolean;
  emptyHint?: string;
}) {
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [hoverKey, setHoverKey] = useState<string | null>(null);

  const data = useMemo(() => {
    const lengths = series.map((s) => sliceTrend(s.points, days).length);
    const len = Math.max(0, ...lengths);
    return Array.from({ length: len }, (_, i) => {
      const row: Record<string, string | number> = {};
      for (const s of series) {
        const sliced = sliceTrend(s.points, days);
        const point = sliced[i];
        if (point) {
          row.date = shortDate(point.date);
          row.fullDate = point.date;
          row[s.key] = point.count;
        }
      }
      return row;
    });
  }, [series, days]);

  const visibleSeries = series.filter((s) => !hidden[s.key]);
  const hasData = data.some((row) =>
    series.some((s) => typeof row[s.key] === "number" && (row[s.key] as number) > 0)
  );

  return (
    <ChartCard
      title={title}
      subtitle={subtitle}
      highlighted={highlighted}
      actions={
        <div className="flex flex-wrap items-center gap-1.5">
          {series.map((s) => {
            const off = hidden[s.key];
            return (
              <button
                key={s.key}
                type="button"
                onClick={() => setHidden((prev) => ({ ...prev, [s.key]: !prev[s.key] }))}
                onMouseEnter={() => setHoverKey(s.key)}
                onMouseLeave={() => setHoverKey(null)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] transition-all",
                  off
                    ? "bg-muted/50 text-muted-foreground/50 line-through"
                    : "bg-muted/70 text-foreground hover:bg-muted"
                )}
              >
                <span
                  className="h-1.5 w-1.5 rounded-full transition-transform"
                  style={{
                    background: s.color,
                    transform: hoverKey === s.key && !off ? "scale(1.4)" : "scale(1)",
                  }}
                />
                {s.name}
              </button>
            );
          })}
        </div>
      }
    >
      {!hasData || visibleSeries.length === 0 ? (
        <p className="flex h-[260px] items-center justify-center text-sm text-muted-foreground">{emptyHint}</p>
      ) : (
        <div
          className={cn("h-[260px] w-full", chartShellClass)}
          onMouseDown={(e) => e.preventDefault()}
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
              <defs>
                {series.map((s) => (
                  <linearGradient key={s.key} id={`grad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={s.color} stopOpacity={0.32} />
                    <stop offset="100%" stopColor={s.color} stopOpacity={0.02} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid stroke={CHART.grid} vertical={false} strokeDasharray="3 6" />
              <XAxis
                dataKey="date"
                tick={{ fill: CHART.axis, fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
                minTickGap={28}
              />
              <YAxis
                tick={{ fill: CHART.axis, fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
                width={44}
                tickFormatter={formatCompact}
              />
              <Tooltip
                content={<ChartTooltip />}
                wrapperStyle={tooltipWrapperStyle}
                contentStyle={{ background: "transparent", border: "none", boxShadow: "none", padding: 0 }}
                cursor={{ stroke: CHART.axis, strokeDasharray: "4 4", strokeOpacity: 0.4 }}
                trigger="hover"
                isAnimationActive
                animationDuration={200}
              />
              {series.map((s) => {
                const dimmed = hoverKey != null && hoverKey !== s.key && !hidden[s.key];
                return (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.name}
                    stroke={s.color}
                    strokeWidth={hoverKey === s.key ? 2.75 : 2}
                    strokeOpacity={hidden[s.key] ? 0 : dimmed ? 0.25 : 1}
                    fill={`url(#grad-${s.key})`}
                    fillOpacity={hidden[s.key] ? 0 : dimmed ? 0.15 : 1}
                    hide={hidden[s.key]}
                    activeDot={{
                      r: 5,
                      strokeWidth: 2,
                      stroke: "#fff",
                      fill: s.color,
                    }}
                    isAnimationActive
                    animationDuration={650}
                    animationEasing="ease-out"
                  />
                );
              })}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </ChartCard>
  );
}

export function InteractiveDonut({
  title,
  subtitle,
  items,
  centerLabel = "总计",
  highlighted,
}: {
  title: string;
  subtitle?: string;
  items: Array<{ key: string; name: string; value: number; color: string }>;
  centerLabel?: string;
  highlighted?: boolean;
}) {
  const data = items.filter((d) => d.value > 0).sort((a, b) => b.value - a.value);
  const total = data.reduce((sum, d) => sum + d.value, 0);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const active = activeIndex != null ? data[activeIndex] : null;

  return (
    <ChartCard title={title} subtitle={subtitle} highlighted={highlighted}>
      {data.length === 0 ? (
        <p className="py-16 text-center text-sm text-muted-foreground">暂无数据</p>
      ) : (
        <div className="mt-1 grid items-center gap-4 sm:grid-cols-[1fr_1fr]">
          <div
            className={cn("relative mx-auto h-[210px] w-full max-w-[220px]", chartShellClass)}
            onMouseDown={(e) => e.preventDefault()}
          >
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="name"
                  innerRadius="60%"
                  outerRadius="84%"
                  paddingAngle={2.5}
                  strokeWidth={0}
                  onMouseEnter={(_, index) => setActiveIndex(index)}
                  onMouseLeave={() => setActiveIndex(null)}
                  onClick={(_, index) =>
                    setActiveIndex((prev) => (prev === index ? null : index))
                  }
                  isAnimationActive
                  animationDuration={700}
                  animationEasing="ease-out"
                  style={{ cursor: "pointer", outline: "none" }}
                  shape={(props) => {
                    const selected = activeIndex === props.index || props.isActive;
                    const dimmed = activeIndex != null && activeIndex !== props.index;
                    return (
                      <Sector
                        cx={props.cx}
                        cy={props.cy}
                        innerRadius={props.innerRadius}
                        outerRadius={selected ? props.outerRadius + 8 : props.outerRadius}
                        startAngle={props.startAngle}
                        endAngle={props.endAngle}
                        fill={props.fill}
                        opacity={dimmed ? 0.35 : 1}
                        stroke="transparent"
                        style={{ outline: "none", cursor: "pointer" }}
                      />
                    );
                  }}
                >
                  {data.map((entry) => (
                    <Cell key={entry.key} fill={entry.color} stroke="transparent" />
                  ))}
                </Pie>
                <Tooltip
                  content={<ChartTooltip />}
                  wrapperStyle={tooltipWrapperStyle}
                  trigger="hover"
                  cursor={false}
                />
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center transition-all">
              <span className="text-[11px] text-muted-foreground">
                {active ? active.name : centerLabel}
              </span>
              <span className="text-xl font-semibold tabular-nums tracking-tight">
                {(active ? active.value : total).toLocaleString("zh-CN")}
              </span>
              {active && total > 0 ? (
                <span className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
                  {((active.value / total) * 100).toFixed(1)}%
                </span>
              ) : null}
            </div>
          </div>
          <ul className="space-y-1.5">
            {data.map((item, index) => {
              const pct = total > 0 ? ((item.value / total) * 100).toFixed(1) : "0";
              const on = activeIndex === index;
              return (
                <li key={item.key}>
                  <button
                    type="button"
                    onMouseEnter={() => setActiveIndex(index)}
                    onMouseLeave={() => setActiveIndex(null)}
                    onClick={() => setActiveIndex((prev) => (prev === index ? null : index))}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm transition-colors",
                      on ? "bg-muted" : "hover:bg-muted/60"
                    )}
                  >
                    <span className="flex min-w-0 items-center gap-2 text-muted-foreground">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full transition-transform"
                        style={{
                          background: item.color,
                          transform: on ? "scale(1.35)" : "scale(1)",
                        }}
                      />
                      <span className="truncate">{item.name}</span>
                    </span>
                    <span className="shrink-0 tabular-nums text-foreground">
                      {item.value.toLocaleString("zh-CN")}
                      <span className="ml-1.5 text-[11px] text-muted-foreground">{pct}%</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </ChartCard>
  );
}

export function CreditTypeBarChart({
  creditsByType,
  highlighted,
}: {
  creditsByType: Record<string, number>;
  highlighted?: boolean;
}) {
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const data = Object.entries(creditsByType)
    .filter(([, count]) => count > 0)
    .map(([type, count]) => ({
      key: type,
      name: creditTypeLabel(type),
      value: count,
      fill: creditTypeColor(type),
      color: creditTypeColor(type),
    }))
    .sort((a, b) => b.value - a.value);

  return (
    <ChartCard
      title="算力库存分布"
      subtitle="各类型未过期活跃余额 · 悬停/点击高亮"
      highlighted={highlighted}
    >
      {data.length === 0 ? (
        <p className="py-16 text-center text-sm text-muted-foreground">暂无算力库存</p>
      ) : (
        <div
          className={cn("h-[220px] w-full", chartShellClass)}
          onMouseDown={(e) => e.preventDefault()}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
              <CartesianGrid stroke={CHART.grid} horizontal={false} strokeDasharray="3 6" />
              <XAxis
                type="number"
                tick={{ fill: CHART.axis, fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                allowDecimals={false}
                tickFormatter={formatCompact}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={64}
                tick={{ fill: CHART.axis, fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                content={<ChartTooltip />}
                wrapperStyle={tooltipWrapperStyle}
                contentStyle={{ background: "transparent", border: "none", boxShadow: "none", padding: 0 }}
                cursor={false}
                trigger="hover"
              />
              <Bar
                dataKey="value"
                name="余额"
                radius={[0, 6, 6, 0]}
                barSize={20}
                isAnimationActive
                animationDuration={650}
                onMouseEnter={(_, index) => setActiveKey(data[index]?.key ?? null)}
                onMouseLeave={() => setActiveKey(null)}
                onClick={(_, index) => {
                  const key = data[index]?.key ?? null;
                  setActiveKey((prev) => (prev === key ? null : key));
                }}
                style={{ cursor: "pointer", outline: "none" }}
              >
                {data.map((entry) => (
                  <Cell
                    key={entry.key}
                    fill={entry.fill}
                    opacity={activeKey == null || activeKey === entry.key ? 1 : 0.35}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </ChartCard>
  );
}

export function CreditFlowCard({
  consumedWeek,
  grantedWeek,
  remaining,
  highlighted,
}: {
  consumedWeek: number;
  grantedWeek: number;
  remaining: number;
  highlighted?: boolean;
}) {
  const flow = consumedWeek + grantedWeek;
  const consumePct = flow > 0 ? (consumedWeek / flow) * 100 : 50;

  return (
    <ChartCard
      title="近 7 日算力流转"
      subtitle="消耗 vs 发放 · 当前全站库存"
      highlighted={highlighted}
    >
      <div className="mt-1 space-y-4">
        <div>
          <p className="text-[11px] text-muted-foreground">全站可用余额</p>
          <p className="mt-1 text-3xl font-semibold tracking-tight tabular-nums">
            {remaining.toLocaleString("zh-CN")}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-xl bg-orange-500/8 px-3 py-2.5 transition-transform hover:scale-[1.02]">
            <p className="text-[11px] text-orange-800/80 dark:text-orange-300/80">消耗</p>
            <p className="mt-0.5 text-lg font-semibold tabular-nums text-orange-700 dark:text-orange-300">
              {consumedWeek.toLocaleString("zh-CN")}
            </p>
          </div>
          <div className="rounded-xl bg-teal-500/8 px-3 py-2.5 transition-transform hover:scale-[1.02]">
            <p className="text-[11px] text-teal-800/80 dark:text-teal-300/80">发放</p>
            <p className="mt-0.5 text-lg font-semibold tabular-nums text-teal-700 dark:text-teal-300">
              {grantedWeek.toLocaleString("zh-CN")}
            </p>
          </div>
        </div>
        {flow > 0 ? (
          <div>
            <div className="mb-1.5 flex justify-between text-[11px] text-muted-foreground">
              <span>消耗占比 {consumePct.toFixed(0)}%</span>
              <span>发放占比 {(100 - consumePct).toFixed(0)}%</span>
            </div>
            <div className="flex h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-gradient-to-r from-orange-600 to-amber-500 transition-all duration-700"
                style={{ width: `${consumePct}%` }}
              />
              <div
                className="h-full bg-gradient-to-r from-teal-600 to-emerald-500 transition-all duration-700"
                style={{ width: `${100 - consumePct}%` }}
              />
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">近 7 日暂无算力流水</p>
        )}
      </div>
    </ChartCard>
  );
}

export function JobStatusDonut({
  jobs,
  highlighted,
}: {
  jobs: Record<string, number>;
  highlighted?: boolean;
}) {
  const items = Object.entries(jobs).map(([status, count]) => ({
    key: status,
    name: jobStatusLabel(status),
    value: count,
    color: STATUS_COLORS[status] ?? "#94a3b8",
  }));
  return (
    <InteractiveDonut
      title="任务状态分布"
      subtitle="悬停扇区或点击图例联动中心数值"
      items={items}
      highlighted={highlighted}
    />
  );
}

export function buildGrowthSeries(stats: AdminStats) {
  return {
    users: { key: "users", name: "新增注册", color: CHART.users, points: stats.userTrend ?? [] },
    projects: {
      key: "projects",
      name: "新建项目",
      color: CHART.projects,
      points: stats.projectTrend ?? [],
    },
    jobs: { key: "jobs", name: "生成任务", color: CHART.jobs, points: stats.jobTrend ?? [] },
    creditConsume: {
      key: "creditConsume",
      name: "算力消耗",
      color: CHART.consume,
      points: stats.creditConsumeTrend ?? [],
    },
    creditGrant: {
      key: "creditGrant",
      name: "算力发放",
      color: CHART.grant,
      points: stats.creditGrantTrend ?? [],
    },
  };
}
