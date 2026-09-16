"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PROVIDER_GROUP_ORDER } from "@/lib/admin/modelCategories";
import {
  getAdminCanvasToolModels,
  listAdminModels,
  putAdminCanvasToolModels,
} from "@/lib/api/admin";
import { ApiError } from "@/lib/api/client";
import type { AdminModel } from "@/types/admin";
import { CREATIVE_GRID_CHILD_TOOL_IDS } from "@/lib/canvas/imageCreativeToolsCatalog";

/** 副模型「不配置」的占位值 */
const SECONDARY_NONE = "";

const CREATIVE_GRID_SECTION_IDS = new Set<string>([
  "grid_9",
  ...(CREATIVE_GRID_CHILD_TOOL_IDS as readonly string[]),
]);

type ModelOption = {
  value: string;
  label: string;
  group: string;
  available: boolean;
  sortOrder: number;
};

type GroupedOptions = {
  groups: { group: string; items: ModelOption[] }[];
  flat: ModelOption[];
};

/**
 * 将同一类别（image / text / video / audio）的模型按供应商分组。
 * 纳入全部模型（低价渠道、官方稳定版、停用模型都可选）。
 */
function groupModelOptions(models: AdminModel[]): GroupedOptions {
  const map = new Map<string, ModelOption[]>();
  for (const m of models) {
    const group = m.providerGroup || m.provider || "其他";
    const label = `${m.displayName || m.name}${m.isAvailable ? "" : "（已停用）"}`;
    const opt: ModelOption = {
      value: m.name,
      label,
      group,
      available: m.isAvailable,
      sortOrder: m.sortOrder ?? 0,
    };
    const list = map.get(group) ?? [];
    list.push(opt);
    map.set(group, list);
  }
  const order = [...PROVIDER_GROUP_ORDER];
  const groupKeys = [...map.keys()].sort((a, b) => {
    const ia = order.indexOf(a as (typeof order)[number]);
    const ib = order.indexOf(b as (typeof order)[number]);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
  const groups = groupKeys.map((group) => ({
    group,
    items: (map.get(group) ?? []).sort(
      (a, b) => a.sortOrder - b.sortOrder || a.label.localeCompare(b.label)
    ),
  }));
  return { groups, flat: groups.flatMap((g) => g.items) };
}

/** 原生 select：管理端更稳（避免 base-ui Select 受控/回显导致主模型选了却落不了库） */
function ModelNativeSelect({
  value,
  onChange,
  groups,
  flat,
  placeholder,
  allowNone,
  excludeValue,
  disabled,
  optionsReady,
}: {
  value: string;
  onChange: (v: string) => void;
  groups: GroupedOptions["groups"];
  flat: GroupedOptions["flat"];
  placeholder: string;
  allowNone?: boolean;
  excludeValue?: string;
  disabled?: boolean;
  optionsReady?: boolean;
}) {
  const inList = Boolean(value && flat.some((o) => o.value === value));
  const showOrphan = Boolean(optionsReady && value && !inList);

  return (
    <select
      className="flex h-8 w-full min-w-0 rounded-md border border-input bg-background px-2 text-xs"
      value={allowNone ? value || SECONDARY_NONE : value || ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      aria-label={placeholder}
    >
      {!allowNone ? (
        <option value="" disabled>
          {placeholder}
        </option>
      ) : (
        <option value={SECONDARY_NONE}>无（不配置副模型）</option>
      )}
      {showOrphan ? (
        <option value={value}>
          {value}（已移除）
        </option>
      ) : null}
      {groups.map((g) => {
        const rows = g.items.filter((o) => o.value !== excludeValue);
        if (!rows.length) return null;
        return (
          <optgroup key={g.group} label={g.group}>
            {rows.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </optgroup>
        );
      })}
    </select>
  );
}

/**
 * 画布/分镜各功能主副模型切换配置面板。
 * 后台配置为权威；用户侧先走主模型，失败后静默切副模型（无 toast / 无前端提示）。
 */
export function CanvasToolModelSwitchPanel() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["admin", "pricing", "canvas-tool-models"],
    queryFn: getAdminCanvasToolModels,
  });
  const modelsQuery = useQuery({
    queryKey: ["admin", "models", "all-for-tools"],
    queryFn: () => listAdminModels(),
  });

  const [draft, setDraft] = useState<
    Record<string, { primary: string; secondary: string }>
  >({});
  /** 本地有未保存改动时，避免 refetch 覆盖草稿 */
  const dirtyRef = useRef(false);
  const appliedVersionRef = useRef<number | null>(null);

  useEffect(() => {
    if (!data?.items) return;
    // 本地未保存改动时不覆盖草稿（保存成功会先清 dirty 再 refetch）
    if (dirtyRef.current) return;
    const next: Record<string, { primary: string; secondary: string }> = {};
    for (const item of data.items) {
      if (!item.toolId) continue;
      next[item.toolId] = {
        primary: item.primary || "",
        secondary: item.secondary || "",
      };
    }
    setDraft(next);
    appliedVersionRef.current = data.version;
  }, [data]);

  const optionsByCategory = useMemo(() => {
    const all = modelsQuery.data ?? [];
    return {
      image: groupModelOptions(all.filter((m) => m.category === "image")),
      text: groupModelOptions(all.filter((m) => m.category === "text")),
      video: groupModelOptions(all.filter((m) => m.category === "video")),
      audio: groupModelOptions(all.filter((m) => m.category === "audio")),
    } as Record<string, GroupedOptions>;
  }, [modelsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => {
      // 以服务端 items 为全集，合并草稿，避免漏传导致「主模型不能为空」
      const tools: Record<string, { primary: string; secondary: string }> = {};
      for (const item of data?.items ?? []) {
        const cfg = draft[item.toolId] ?? {
          primary: item.primary || "",
          secondary: item.secondary || "",
        };
        tools[item.toolId] = {
          primary: (cfg.primary || "").trim(),
          secondary: (cfg.secondary || "").trim(),
        };
      }
      return putAdminCanvasToolModels(tools);
    },
    onSuccess: async (saved) => {
      dirtyRef.current = false;
      appliedVersionRef.current = saved.version;
      toast.success("功能模型切换已保存");
      await refetch();
    },
    onError: (err: unknown) => {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "功能模型切换保存失败";
      toast.error(msg);
    },
  });

  const setPrimary = (toolId: string, v: string) => {
    dirtyRef.current = true;
    setDraft((prev) => {
      const cur = prev[toolId] ?? { primary: "", secondary: "" };
      const secondary = cur.secondary === v ? "" : cur.secondary;
      return { ...prev, [toolId]: { primary: v, secondary } };
    });
  };

  const setSecondary = (toolId: string, v: string) => {
    dirtyRef.current = true;
    setDraft((prev) => {
      const cur = prev[toolId] ?? { primary: "", secondary: "" };
      return { ...prev, [toolId]: { ...cur, secondary: v } };
    });
  };

  const renderRow = (item: { toolId: string; label: string; category: string }) => {
    const cat =
      item.category === "text" ||
      item.category === "video" ||
      item.category === "audio"
        ? item.category
        : "image";
    const { groups, flat } = optionsByCategory[cat] ?? { groups: [], flat: [] };
    const cfg = draft[item.toolId] ?? { primary: "", secondary: "" };
    return (
      <div
        key={item.toolId}
        className="grid grid-cols-[minmax(6rem,1fr)_minmax(10rem,1.4fr)_minmax(10rem,1.4fr)] items-center gap-3 text-sm"
      >
        <span className="text-muted-foreground">{item.label}</span>
        <ModelNativeSelect
          value={cfg.primary}
          onChange={(v) => setPrimary(item.toolId, v)}
          groups={groups}
          flat={flat}
          placeholder="选择主模型"
          disabled={modelsQuery.isLoading}
          optionsReady={!modelsQuery.isLoading}
        />
        <ModelNativeSelect
          value={cfg.secondary}
          onChange={(v) => setSecondary(item.toolId, v)}
          groups={groups}
          flat={flat}
          placeholder="副模型（可选）"
          allowNone
          excludeValue={cfg.primary}
          disabled={modelsQuery.isLoading}
          optionsReady={!modelsQuery.isLoading}
        />
      </div>
    );
  };

  const imageItems = useMemo(
    () => (data?.items ?? []).filter((item) => item.category === "image"),
    [data?.items]
  );
  const creativeGridItems = useMemo(
    () => imageItems.filter((item) => CREATIVE_GRID_SECTION_IDS.has(item.toolId)),
    [imageItems]
  );
  const otherImageItems = useMemo(
    () => imageItems.filter((item) => !CREATIVE_GRID_SECTION_IDS.has(item.toolId)),
    [imageItems]
  );
  const videoItems = useMemo(
    () => (data?.items ?? []).filter((item) => item.category === "video"),
    [data?.items]
  );
  const audioItems = useMemo(
    () => (data?.items ?? []).filter((item) => item.category === "audio"),
    [data?.items]
  );
  const textItems = useMemo(
    () => (data?.items ?? []).filter((item) => item.category === "text"),
    [data?.items]
  );

  return (
    <section className="rounded-xl border border-border bg-card px-4 py-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-foreground">
            功能模型切换（主 / 副模型）
          </h3>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-muted-foreground">
            为解析剧本、爆款拉片、一键出海、分镜工具，以及多角度 / 高清 / 视频画面编辑 / 智能去字幕 / 人声分离与消除人声等分别设置主模型与副模型。
            「九宫格」弹层内各功能（25宫格、四宫格、推演、设定图等）已拆开，可分别指定主/副模型；未单独配置时会继承原九宫格配置。
            后台配置为权威，会覆盖前端传入的模型。用户使用该功能时优先走主模型；主模型上游失败后
            <strong className="font-medium text-foreground/80">自动静默切换副模型重试</strong>
            ，不对用户弹出提示。
            画面编辑均在视频类；智能抠像为本地处理（模型名仅结算），主体消除 / 修改 / 替换走后台配置的视频上游模型。
            可选模型按供应商分类，涵盖低价渠道与官方稳定版（含默认停用模型）。
            {data?.version != null ? ` 当前版本 v${data.version}` : null}
          </p>
        </div>
        <Button
          size="sm"
          disabled={saveMutation.isPending || isLoading || modelsQuery.isLoading}
          onClick={() => saveMutation.mutate()}
        >
          {saveMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            "保存模型切换"
          )}
        </Button>
      </div>
      {isLoading ? (
        <p className="text-xs text-muted-foreground">加载中…</p>
      ) : isError ? (
        <div className="text-xs text-destructive">
          加载失败
          <Button
            variant="ghost"
            size="sm"
            className="ml-2 h-7"
            onClick={() => void refetch()}
          >
            重试
          </Button>
        </div>
      ) : (
        <div className="space-y-5">
          <div>
            <div className="grid grid-cols-[minmax(6rem,1fr)_minmax(10rem,1.4fr)_minmax(10rem,1.4fr)] gap-3 pb-1.5 text-[11px] font-semibold text-muted-foreground">
              <span>图片类功能</span>
              <span>主模型</span>
              <span>副模型（兜底，静默）</span>
            </div>
            <div className="space-y-2">{otherImageItems.map(renderRow)}</div>
          </div>
          {creativeGridItems.length > 0 ? (
            <div>
              <div className="grid grid-cols-[minmax(6rem,1fr)_minmax(10rem,1.4fr)_minmax(10rem,1.4fr)] gap-3 pb-1.5 text-[11px] font-semibold text-muted-foreground">
                <span>九宫格子功能</span>
                <span>主模型</span>
                <span>副模型（兜底，静默）</span>
              </div>
              <p className="mb-2 text-[11px] text-muted-foreground">
                对应画布图片节点顶部「九宫格」弹层各项；可分别切换文生图 / 图生图等模型。
              </p>
              <div className="space-y-2">{creativeGridItems.map(renderRow)}</div>
            </div>
          ) : null}
          {videoItems.length > 0 ? (
            <div>
              <div className="grid grid-cols-[minmax(6rem,1fr)_minmax(10rem,1.4fr)_minmax(10rem,1.4fr)] gap-3 pb-1.5 text-[11px] font-semibold text-muted-foreground">
                <span>视频类功能</span>
                <span>主模型</span>
                <span>副模型（兜底，静默）</span>
              </div>
              <div className="space-y-2">{videoItems.map(renderRow)}</div>
            </div>
          ) : null}
          {audioItems.length > 0 ? (
            <div>
              <div className="grid grid-cols-[minmax(6rem,1fr)_minmax(10rem,1.4fr)_minmax(10rem,1.4fr)] gap-3 pb-1.5 text-[11px] font-semibold text-muted-foreground">
                <span>音频类功能</span>
                <span>主模型</span>
                <span>副模型（兜底，静默）</span>
              </div>
              <div className="space-y-2">{audioItems.map(renderRow)}</div>
            </div>
          ) : null}
          <div>
            <div className="grid grid-cols-[minmax(6rem,1fr)_minmax(10rem,1.4fr)_minmax(10rem,1.4fr)] gap-3 pb-1.5 text-[11px] font-semibold text-muted-foreground">
              <span>文本类功能</span>
              <span>主模型</span>
              <span>副模型（兜底，静默）</span>
            </div>
            <div className="space-y-2">{textItems.map(renderRow)}</div>
          </div>
        </div>
      )}
    </section>
  );
}
