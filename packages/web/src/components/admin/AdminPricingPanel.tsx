"use client";

import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import {
  patchAdminPricing,
  resetAdminPricing,
  getAdminCanvasToolPricing,
  putAdminCanvasToolPricing,
  type AdminPricingItem,
} from "@/lib/api/admin";
import { estimateCreditFromPricing } from "@/lib/canvas/generationCreditHelpers";
import { formatCreditAmount, roundCreditAmount } from "@/lib/api/credits";

export type PricingSettingsTab =
  | "general"
  | "image_pricing"
  | "ref_video_pricing"
  | "image_to_video"
  | "agent_control"
  | "model_switches";

export const PRICING_TABS: Array<{ id: PricingSettingsTab; label: string }> = [
  { id: "general", label: "通用算力价格" },
  { id: "image_pricing", label: "图片生成价格设置" },
  { id: "ref_video_pricing", label: "参考生视频价格设置" },
  { id: "image_to_video", label: "生图转视频价格设置" },
  { id: "agent_control", label: "AI 操控设置" },
  { id: "model_switches", label: "模型开关设置" },
];

const TAB_META: Record<
  Exclude<PricingSettingsTab, "model_switches" | "agent_control">,
  { panelTitle: string; hint: string; groupLabel?: string }
> = {
  general: {
    panelTitle: "算力消耗配置（全局默认）",
    hint: "文本、音频模型基础算力；并配置画布图片工具与分镜表各生成步骤的固定算力。成本价（元/次）用于任务导出时估算上游成本，与面向用户的算力扣费分开配置。",
    groupLabel: "文本 / 音频 / 工具",
  },
  image_pricing: {
    panelTitle: "图片生成价格设置",
    hint: "有画质/清晰度档时展开设档位算力；无档位模型（如悠船 Niji 7）在「基础算力」列按次设价。成本价（元/张）用于导出上游成本估算。",
    groupLabel: "图片生成",
  },
  ref_video_pricing: {
    panelTitle: "参考生视频价格设置",
    hint: "用户扣费 = 所选秒数 × 所选清晰度每秒算力。成本价（元/秒）用于任务导出时自动计算上游成本 = 成本价 × 视频秒数。",
    groupLabel: "参考生视频",
  },
  image_to_video: {
    panelTitle: "生图转视频价格设置",
    hint: "与参考生视频相同：只设各清晰度每秒算力，总价 = 秒数 × 每秒算力。成本价（元/秒）用于导出上游成本估算。",
    groupLabel: "生图转视频",
  },
};

const IMAGE_TIER_GROUP_IDS = new Set(["quality", "resolution", "size"]);

function imagePricingGroups(item: AdminPricingItem) {
  const groups = item.presetGroups.filter((g) => IMAGE_TIER_GROUP_IDS.has(g.id));
  const hasResolution = groups.some((g) => g.id === "resolution");
  return groups.filter((g) => !(g.id === "size" && hasResolution));
}

function imageGroupSectionLabel(groupId: string): string {
  if (groupId === "quality") return "画质（算力）";
  if (groupId === "resolution" || groupId === "size") return "清晰度（算力）";
  return groupId;
}

// —— 二维矩阵定价（画质 × 清晰度，9 档独立）辅助 ——
function matrixQualityGroup(item: AdminPricingItem) {
  return item.presetGroups.find((g) => g.id === "quality");
}

function matrixResolutionGroup(item: AdminPricingItem) {
  return (
    item.presetGroups.find((g) => g.id === "resolution") ??
    item.presetGroups.find((g) => g.id === "size")
  );
}

function isImageMatrixItem(item: AdminPricingItem): boolean {
  // 仅当 mode=image_matrix 且预设同时有画质+清晰度时，才走二维矩阵编辑；
  // 否则（如全能图片 G 低价版仅 1K/2K/4K）回退为清晰度档位设价，避免无法展开输入。
  if (item.category !== "image" || item.mode !== "image_matrix") return false;
  return !!matrixQualityGroup(item) && !!matrixResolutionGroup(item);
}

// 从模型现有 matrix + 预设选项构建可编辑的 {画质id: {清晰度id: 字符串算力}}
function buildMatrixCosts(item: AdminPricingItem): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {};
  const qGroup = matrixQualityGroup(item);
  const rGroup = matrixResolutionGroup(item);
  if (!qGroup || !rGroup) return out;
  const existing = item.matrix ?? {};
  for (const q of qGroup.items) {
    out[q.id] = {};
    for (const r of rGroup.items) {
      out[q.id][r.id] = String(existing[q.id]?.[r.id] ?? 0);
    }
  }
  return out;
}

function isRefVideoModel(item: AdminPricingItem): boolean {
  if (item.category !== "video") return false;
  const mode = item.videoMode?.toLowerCase();
  if (mode === "r2v" || mode === "t2v") return true;
  if (mode === "i2v" || mode === "lip_sync") return false;
  return /r2v|ref|reference|turbo_r2v|q3_turbo|wan27_r2v|pixverse.*r2v|kling.*r2v|vidu.*r2v/i.test(
    item.name
  );
}

function isImageToVideoModel(item: AdminPricingItem): boolean {
  if (item.category !== "video") return false;
  const mode = item.videoMode?.toLowerCase();
  if (mode === "i2v" || mode === "lip_sync") return true;
  if (mode === "r2v" || mode === "t2v") return false;
  return /i2v|frame|lip|seedance|start_end|first_frame|pixverse_v6_i2v|kling_v3_omni_i2v/i.test(
    item.name
  );
}

export function filterPricingByTab(
  items: AdminPricingItem[],
  tab: PricingSettingsTab
): AdminPricingItem[] {
  switch (tab) {
    case "general":
      return items.filter((item) => ["text", "audio", "tool"].includes(item.category));
    case "image_pricing":
      return items.filter((item) => item.category === "image");
    case "ref_video_pricing":
      return items.filter((item) => item.category === "video" && isRefVideoModel(item));
    case "image_to_video":
      return items.filter((item) => item.category === "video" && isImageToVideoModel(item));
    default:
      return [];
  }
}

function parseCostInput(raw: string): number {
  const trimmed = raw.trim();
  if (!trimmed) return 0;
  const num = Number(trimmed);
  return Number.isFinite(num) && num >= 0 ? num : 0;
}

function usePricingRowState(item: AdminPricingItem) {
  const [baseCost, setBaseCost] = useState(String(item.baseCost ?? 0));
  const [mode, setMode] = useState(item.mode || "additive");
  const [minCost, setMinCost] = useState(String(item.minCost ?? 0));
  const [primaryGroupId, setPrimaryGroupId] = useState(item.primaryGroupId ?? "");
  const [videoYuanPerSecond, setVideoYuanPerSecond] = useState(
    item.videoYuanPerSecond != null ? String(item.videoYuanPerSecond) : ""
  );
  const [yuanPerCall, setYuanPerCall] = useState(
    item.yuanPerCall != null ? String(item.yuanPerCall) : ""
  );
  const [previewTotal, setPreviewTotal] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [optionCosts, setOptionCosts] = useState<Record<string, Record<string, string>>>(() =>
    buildOptionCosts(item)
  );
  const [optionCostsWithRef, setOptionCostsWithRef] = useState<
    Record<string, Record<string, string>>
  >(() => buildOptionCostsWithRef(item));
  const [matrixCosts, setMatrixCosts] = useState<Record<string, Record<string, string>>>(() =>
    buildMatrixCosts(item)
  );
  // MiniMax-H3：超额参考图免费张数 / 单价（算力点）
  const [extraImageFreeCount, setExtraImageFreeCount] = useState(
    String(item.extraImageBilling?.freeCount ?? 5)
  );
  const [extraImageCost, setExtraImageCost] = useState(
    String(item.extraImageBilling?.costPerImage ?? 0)
  );

  useEffect(() => {
    setBaseCost(String(item.baseCost ?? 0));
    setMode(item.mode || "additive");
    setMinCost(String(item.minCost ?? 0));
    setPrimaryGroupId(item.primaryGroupId ?? "");
    setVideoYuanPerSecond(item.videoYuanPerSecond != null ? String(item.videoYuanPerSecond) : "");
    setYuanPerCall(item.yuanPerCall != null ? String(item.yuanPerCall) : "");
    setPreviewTotal(null);
    setOptionCosts(buildOptionCosts(item));
    setOptionCostsWithRef(buildOptionCostsWithRef(item));
    setMatrixCosts(buildMatrixCosts(item));
    setExtraImageFreeCount(String(item.extraImageBilling?.freeCount ?? 5));
    setExtraImageCost(String(item.extraImageBilling?.costPerImage ?? 0));
  }, [item]);

  return {
    baseCost,
    setBaseCost,
    mode,
    setMode,
    minCost,
    setMinCost,
    primaryGroupId,
    setPrimaryGroupId,
    videoYuanPerSecond,
    setVideoYuanPerSecond,
    yuanPerCall,
    setYuanPerCall,
    previewTotal,
    setPreviewTotal,
    expanded,
    setExpanded,
    optionCosts,
    setOptionCosts,
    optionCostsWithRef,
    setOptionCostsWithRef,
    matrixCosts,
    setMatrixCosts,
    extraImageFreeCount,
    setExtraImageFreeCount,
    extraImageCost,
    setExtraImageCost,
  };
}

function buildOptionCosts(item: AdminPricingItem) {
  const out: Record<string, Record<string, string>> = {};
  for (const group of item.presetGroups) {
    const existing = item.options[group.id] ?? {};
    out[group.id] = {};
    for (const opt of group.items) {
      out[group.id][opt.id] = String(existing[opt.id] ?? 0);
    }
  }
  return out;
}

/** 是否支持「有参考视频」双单价表（多模态 Seedance 等） */
function supportsWithRefVideoPricing(item: AdminPricingItem): boolean {
  if (item.refVideoGroupId) return true;
  if (item.presetGroups.some((g) => g.id === "refVideo")) return true;
  const withRef = item.optionsWithVideoReference;
  if (withRef && Object.keys(withRef).length > 0) return true;
  return /^((rh|huahu)_seedance_20(_fast|_mini|_4k)?_r2v)$/i.test(item.name);
}

/** 从 optionsWithVideoReference 构建可编辑清晰度单价（缺省 0） */
function buildOptionCostsWithRef(item: AdminPricingItem) {
  const out: Record<string, Record<string, string>> = {};
  const withRoot = item.optionsWithVideoReference ?? {};
  for (const group of visiblePresetGroups(item)) {
    const existing = withRoot[group.id] ?? {};
    out[group.id] = {};
    for (const opt of group.items) {
      out[group.id][opt.id] = String(existing[opt.id] ?? 0);
    }
  }
  return out;
}

function parseOptionCostsMap(
  raw: Record<string, Record<string, string>>
): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {};
  for (const [groupId, values] of Object.entries(raw)) {
    out[groupId] = {};
    for (const [itemId, text] of Object.entries(values)) {
      out[groupId][itemId] = roundCreditAmount(text);
    }
  }
  return out;
}

type DraftPricing = {
  baseCost: number;
  mode: string;
  minCost: number;
  primaryGroupId?: string;
  rateGroupId?: string;
  durationGroupId?: string;
  tierGroupIds?: string[];
  matrixGroupIds?: string[];
  options?: Record<string, Record<string, number>>;
  matrix?: Record<string, Record<string, number>>;
};

type PricingDraftState = {
  baseCost: string;
  mode: string;
  minCost: string;
  primaryGroupId: string;
  optionCosts: Record<string, Record<string, string>>;
  matrixCosts?: Record<string, Record<string, string>>;
};

function buildDraftPricing(state: PricingDraftState, item?: AdminPricingItem): DraftPricing {
  const isVideo = item?.category === "video";
  const options: Record<string, Record<string, number>> = {};
  const rateGroupIds = isVideo ? new Set(["resolution", "size"]) : null;

  for (const [groupId, values] of Object.entries(state.optionCosts)) {
    if (rateGroupIds && !rateGroupIds.has(groupId)) continue;
    options[groupId] = {};
    for (const [itemId, raw] of Object.entries(values)) {
      options[groupId][itemId] = roundCreditAmount(raw);
    }
  }

  if (isVideo) {
    return {
      baseCost: 0,
      mode: "video_per_second",
      minCost: 0,
      rateGroupId: options.resolution ? "resolution" : "size",
      durationGroupId: "duration",
      options,
    };
  }

  if (item?.category === "image") {
    // 二维矩阵定价：画质 × 清晰度 9 档独立
    if (isImageMatrixItem(item)) {
      const rGroup = matrixResolutionGroup(item);
      const resGroupId = rGroup?.id === "size" ? "size" : "resolution";
      const matrix: Record<string, Record<string, number>> = {};
      for (const [qId, cols] of Object.entries(state.matrixCosts ?? {})) {
        matrix[qId] = {};
        for (const [rId, raw] of Object.entries(cols)) {
          matrix[qId][rId] = roundCreditAmount(raw);
        }
      }
      return {
        baseCost: 0,
        mode: "image_matrix",
        minCost: 0,
        matrixGroupIds: ["quality", resGroupId],
        matrix,
      };
    }

    // 无画质/清晰度档：按次基础算力（悠船 Niji 7 等）
    const tierGroups = imagePricingGroups(item);
    if (tierGroups.length === 0) {
      return {
        baseCost: roundCreditAmount(state.baseCost),
        mode: "additive",
        minCost: 0,
        options: {},
      };
    }

    const tierOptions: Record<string, Record<string, number>> = {};
    for (const groupId of IMAGE_TIER_GROUP_IDS) {
      const values = state.optionCosts[groupId];
      if (!values) continue;
      tierOptions[groupId] = {};
      for (const [itemId, raw] of Object.entries(values)) {
        tierOptions[groupId][itemId] = roundCreditAmount(raw);
      }
    }
    return {
      baseCost: 0,
      mode: "image_by_tier",
      minCost: 0,
      tierGroupIds: ["quality", "resolution", "size"],
      options: tierOptions,
    };
  }

  return {
    baseCost: roundCreditAmount(state.baseCost),
    mode: state.mode,
    minCost: roundCreditAmount(state.minCost),
    primaryGroupId: state.primaryGroupId.trim() || undefined,
    options,
  };
}

function buildPatchPayload(
  item: AdminPricingItem,
  state: {
    baseCost: string;
    mode: string;
    minCost: string;
    primaryGroupId: string;
    optionCosts: Record<string, Record<string, string>>;
  }
) {
  return buildDraftPricing(state, item);
}

function previewDraftTotal(item: AdminPricingItem, state: Parameters<typeof buildDraftPricing>[0]) {
  const pricing = buildDraftPricing(state, item);
  return estimateCreditFromPricing(pricing, item.defaultOptionSnapshot ?? {});
}

function pricingGroupLabel(
  item: AdminPricingItem,
  group: { id: string; label: string },
  mode: string
): string {
  if (mode === "video_per_second" && group.id === "resolution") {
    return "清晰度（每秒算力）";
  }
  if (item.category === "image" && ["size", "resolution", "quality"].includes(group.id)) {
    return `${group.label}（画质档位算力）`;
  }
  return group.label;
}

function visiblePresetGroups(item: AdminPricingItem) {
  if (item.category === "video") {
    return item.presetGroups.filter((g) => g.id === "resolution" || g.id === "size");
  }
  if (item.category === "image") {
    return imagePricingGroups(item);
  }
  return item.presetGroups;
}

function ImagePricingModelRow({
  item,
  stripe,
  onSaved,
}: {
  item: AdminPricingItem;
  stripe: boolean;
  onSaved: () => void;
}) {
  const state = usePricingRowState(item);
  const isMatrix = isImageMatrixItem(item);
  const qGroup = matrixQualityGroup(item);
  const rGroup = matrixResolutionGroup(item);
  const tierGroups = imagePricingGroups(item);
  const hasMatrix = isMatrix && !!qGroup && !!rGroup;
  const hasTiers = isMatrix ? hasMatrix : tierGroups.length > 0;

  // 无画质/清晰度档：按次基础算力（悠船 Niji 7 等）
  const useBaseCostOnly = !isMatrix && tierGroups.length === 0;

  const saveMutation = useMutation({
    mutationFn: () => {
      const draft = buildDraftPricing(state, item);
      if (isMatrix) {
        return patchAdminPricing(item.modelId, {
          baseCost: 0,
          mode: "image_matrix",
          minCost: 0,
          matrix: draft.matrix,
          yuanPerCall: parseCostInput(state.yuanPerCall),
        });
      }
      if (useBaseCostOnly) {
        return patchAdminPricing(item.modelId, {
          baseCost: draft.baseCost,
          mode: "additive",
          minCost: 0,
          options: {},
          yuanPerCall: parseCostInput(state.yuanPerCall),
        });
      }
      return patchAdminPricing(item.modelId, {
        baseCost: 0,
        mode: "image_by_tier",
        minCost: 0,
        options: draft.options,
        yuanPerCall: parseCostInput(state.yuanPerCall),
      });
    },
    onSuccess: () => {
      toast.success(
        useBaseCostOnly
          ? `${item.displayName} 已保存（基础 ${state.baseCost || 0} 算力）`
          : `${item.displayName} 已保存`
      );
      onSaved();
    },
    onError: () => toast.error(`${item.displayName} 保存失败`),
  });

  const resetMutation = useMutation({
    mutationFn: () => resetAdminPricing(item.modelId),
    onSuccess: () => {
      toast.success(`${item.displayName} 已恢复默认`);
      onSaved();
    },
    onError: () => toast.error("恢复默认失败"),
  });

  const previewMutation = useMutation({
    mutationFn: () => Promise.resolve(previewDraftTotal(item, state)),
    onSuccess: (total) => {
      state.setPreviewTotal(total);
      const snap = item.defaultOptionSnapshot ?? {};
      const quality = snap.quality ?? "—";
      const clarity = snap.resolution ?? snap.size ?? "—";
      if (total <= 0) {
        toast.message(`${item.displayName} 预览为 0`, {
          description: isMatrix
            ? "请为画质 × 清晰度 9 档分别设置算力。"
            : useBaseCostOnly
              ? "请填写「基础算力」（按次扣费）。"
              : "请为画质与清晰度档位设置算力。",
        });
      } else {
        toast.message(`${item.displayName} 预览 ${total} 算力`, {
          description: isMatrix
            ? `示例：画质 ${quality} × 清晰度 ${clarity}`
            : useBaseCostOnly
              ? "按次基础算力"
              : `示例：画质 ${quality} + 清晰度 ${clarity}`,
        });
      }
    },
  });

  return (
    <div className={cn("rounded-md", stripe && "bg-muted/30")}>
      <div className="grid grid-cols-[minmax(160px,1.4fr)_88px_88px_minmax(120px,1fr)_auto] items-center gap-x-3 px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            {hasTiers ? (
              <button
                type="button"
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => state.setExpanded((v) => !v)}
                aria-label={state.expanded ? "收起" : "展开画质/清晰度算力"}
              >
                {state.expanded ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </button>
            ) : (
              <span className="inline-block w-4 shrink-0" />
            )}
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{item.displayName}</p>
              {state.previewTotal != null ? (
                <p className="text-[11px] text-muted-foreground">预览：{state.previewTotal} 算力</p>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  {isMatrix
                    ? "总价 = 画质 × 清晰度（9 档独立）"
                    : useBaseCostOnly
                      ? "总价 = 基础算力（按次）"
                      : tierGroups.length === 1 && tierGroups[0]?.id === "resolution"
                        ? "总价 = 所选清晰度算力（1K / 2K / 4K）"
                        : "总价 = 画质 + 清晰度"}
                </p>
              )}
            </div>
          </div>
        </div>
        {useBaseCostOnly ? (
          <Input
            className="h-8 tabular-nums"
            value={state.baseCost}
            onChange={(e) => state.setBaseCost(e.target.value)}
            inputMode="decimal"
            placeholder="0"
            aria-label="基础算力"
            title="基础算力（按次）"
          />
        ) : (
          <span className="text-center text-xs text-muted-foreground" title="档位定价见展开行">
            —
          </span>
        )}
        <Input
          className="h-8 tabular-nums"
          value={state.yuanPerCall}
          onChange={(e) => state.setYuanPerCall(e.target.value)}
          inputMode="decimal"
          placeholder="0"
          aria-label="成本价元每张"
        />
        <span className="truncate font-mono text-xs text-muted-foreground" title={item.name}>
          {item.name}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending}
          >
            预览
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending}
          >
            默认
          </Button>
          <Button
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
          >
            保存
          </Button>
        </div>
      </div>

      {state.expanded && hasTiers ? (
        <div className="border-t border-border/60 bg-muted/10 px-3 py-3 pl-10">
          {isMatrix && qGroup && rGroup ? (
            <div className="space-y-2">
              <p className="text-xs font-semibold text-muted-foreground">
                画质 × 清晰度矩阵（每格独立算力，共 {qGroup.items.length}×{rGroup.items.length} 档）
              </p>
              <div className="overflow-x-auto">
                <table className="border-separate border-spacing-1 text-xs">
                  <thead>
                    <tr>
                      <th className="px-2 py-1 text-left font-medium text-muted-foreground">
                        画质＼清晰度
                      </th>
                      {rGroup.items.map((res) => (
                        <th
                          key={res.id}
                          className="px-2 py-1 text-center font-medium text-muted-foreground"
                        >
                          {res.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {qGroup.items.map((q) => (
                      <tr key={q.id}>
                        <td className="px-2 py-1 text-muted-foreground">{q.label}</td>
                        {rGroup.items.map((res) => (
                          <td key={res.id} className="px-1 py-1">
                            <Input
                              className="h-7 w-20 tabular-nums text-center"
                              value={state.matrixCosts[q.id]?.[res.id] ?? "0"}
                              onChange={(e) =>
                                state.setMatrixCosts((prev) => ({
                                  ...prev,
                                  [q.id]: {
                                    ...(prev[q.id] ?? {}),
                                    [res.id]: e.target.value,
                                  },
                                }))
                              }
                              inputMode="decimal"
                              aria-label={`画质${q.label} 清晰度${res.label} 算力`}
                            />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-[10px] text-muted-foreground">
                单位：算力/次。每格为该（画质 × 清晰度）组合的最终扣费，不再累加。
              </p>
            </div>
          ) : (
          <div className="space-y-3">
            {tierGroups.map((group) => (
              <div key={group.id}>
                <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
                  {imageGroupSectionLabel(group.id)}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {group.items.map((opt) => (
                    <label key={opt.id} className="flex items-center gap-2 text-xs">
                      <span className="min-w-[3.5rem] text-muted-foreground">{opt.label}</span>
                      <Input
                        className="h-7 w-16 tabular-nums"
                        value={state.optionCosts[group.id]?.[opt.id] ?? "0"}
                        onChange={(e) =>
                          state.setOptionCosts((prev) => ({
                            ...prev,
                            [group.id]: {
                              ...(prev[group.id] ?? {}),
                              [opt.id]: e.target.value,
                            },
                          }))
                        }
                        inputMode="decimal"
                        aria-label={`${opt.label} 算力`}
                      />
                      <span className="text-[10px] text-muted-foreground">算力</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function VideoPricingModelRow({
  item,
  stripe,
  onSaved,
}: {
  item: AdminPricingItem;
  stripe: boolean;
  onSaved: () => void;
}) {
  const state = usePricingRowState(item);
  const rateGroups = visiblePresetGroups(item);
  const hasRates = rateGroups.length > 0;
  const showWithRef = supportsWithRefVideoPricing(item);
  const showMaterialUsage =
    Boolean(item.billInputVideoSeconds) || Boolean(item.extraImageBilling);

  const saveMutation = useMutation({
    mutationFn: () => {
      const draft = buildDraftPricing(state, item);
      const payload: Parameters<typeof patchAdminPricing>[1] = {
        baseCost: 0,
        mode: "video_per_second",
        minCost: 0,
        options: draft.options,
        videoYuanPerSecond: parseCostInput(state.videoYuanPerSecond),
      };
      // 多模态：同时保存有参考视频每秒算力
      if (showWithRef) {
        payload.optionsWithVideoReference = parseOptionCostsMap(state.optionCostsWithRef);
      }
      // MiniMax-H3：保留输入视频秒价开关，并保存超额参考图计费
      if (showMaterialUsage) {
        payload.billInputVideoSeconds = Boolean(item.billInputVideoSeconds);
        payload.extraImageBilling = {
          freeCount: Math.max(0, parseInt(state.extraImageFreeCount, 10) || 0),
          costPerImage: roundCreditAmount(state.extraImageCost),
        };
      }
      return patchAdminPricing(item.modelId, payload);
    },
    onSuccess: () => {
      toast.success(`${item.displayName} 已保存`);
      onSaved();
    },
    onError: () => toast.error(`${item.displayName} 保存失败`),
  });

  const resetMutation = useMutation({
    mutationFn: () => resetAdminPricing(item.modelId),
    onSuccess: () => {
      toast.success(`${item.displayName} 已恢复默认`);
      onSaved();
    },
    onError: () => toast.error("恢复默认失败"),
  });

  const previewMutation = useMutation({
    mutationFn: async () => {
      const draft = buildDraftPricing(state, item);
      const snap = item.defaultOptionSnapshot ?? {};
      const noneTotal = estimateCreditFromPricing(draft, snap);
      let withTotal: number | null = null;
      if (showWithRef) {
        withTotal = estimateCreditFromPricing(
          {
            ...draft,
            options: parseOptionCostsMap(state.optionCostsWithRef),
            optionsWithVideoReference: parseOptionCostsMap(state.optionCostsWithRef),
            refVideoGroupId: "refVideo",
          },
          { ...snap, refVideo: "with" }
        );
      }
      return { noneTotal, withTotal, snap };
    },
    onSuccess: (result: {
      noneTotal: number;
      withTotal: number | null;
      snap: Record<string, string>;
    }) => {
      const { noneTotal, withTotal, snap } = result;
      state.setPreviewTotal(noneTotal);
      const sec = snap.duration ?? "?";
      const res = snap.resolution ?? snap.size ?? "";
      if (showWithRef && withTotal != null) {
        toast.message(`${item.displayName} 预览`, {
          description: `无参考 ${noneTotal} 算力 · 有参考 ${withTotal} 算力（示例 ${sec}s × ${res}）`,
        });
      } else if (noneTotal <= 0) {
        toast.message(`${item.displayName} 预览为 0`, {
          description: "请为各清晰度设置每秒算力（如 720p=10，1080p=20）。",
        });
      } else {
        toast.message(`${item.displayName} 预览 ${noneTotal} 算力`, {
          description: `示例：${sec}s × ${res} 每秒算力`,
        });
      }
    },
  });

  return (
    <div className={cn("rounded-md", stripe && "bg-muted/30")}>
      <div className="grid grid-cols-[minmax(160px,1.4fr)_88px_minmax(120px,1fr)_auto] items-center gap-x-3 px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            {hasRates ? (
              <button
                type="button"
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => state.setExpanded((v) => !v)}
                aria-label={state.expanded ? "收起" : "展开每秒算力"}
              >
                {state.expanded ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </button>
            ) : (
              <span className="inline-block w-4 shrink-0" />
            )}
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{item.displayName}</p>
              {state.previewTotal != null ? (
                <p className="text-[11px] text-muted-foreground">预览：{state.previewTotal} 算力</p>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  {showMaterialUsage
                    ? "总价 = 出片秒×单价 + 输入视频秒×单价 + 超额图"
                    : showWithRef
                      ? "总价 = 秒数 × 每秒算力（无/有参考视频分档）"
                      : "总价 = 秒数 × 每秒算力"}
                </p>
              )}
            </div>
          </div>
        </div>
        <Input
          className="h-8 tabular-nums"
          value={state.videoYuanPerSecond}
          onChange={(e) => state.setVideoYuanPerSecond(e.target.value)}
          inputMode="decimal"
          placeholder="0"
          aria-label="成本价元每秒"
        />
        <span className="truncate font-mono text-xs text-muted-foreground" title={item.name}>
          {item.name}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending}
          >
            预览
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending}
          >
            默认
          </Button>
          <Button
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
          >
            保存
          </Button>
        </div>
      </div>

      {state.expanded && hasRates ? (
        <div className="border-t border-border/60 bg-muted/10 px-3 py-3 pl-10">
          <div className="space-y-4">
            {showMaterialUsage ? (
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                成本对齐：出片与输入视频按清晰度秒价；参考图前 N 张免费，超出按下表计费；音频免费。
              </p>
            ) : null}
            {rateGroups.map((group) => (
              <div key={`none-${group.id}`}>
                <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
                  {showWithRef ? `${group.label} · 无参考视频（每秒算力）` : `${group.label}（每秒算力）`}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {group.items.map((opt) => (
                    <label key={opt.id} className="flex items-center gap-2 text-xs">
                      <span className="min-w-[3.5rem] text-muted-foreground">{opt.label}</span>
                      <Input
                        className="h-7 w-16 tabular-nums"
                        value={state.optionCosts[group.id]?.[opt.id] ?? "0"}
                        onChange={(e) =>
                          state.setOptionCosts((prev) => ({
                            ...prev,
                            [group.id]: {
                              ...(prev[group.id] ?? {}),
                              [opt.id]: e.target.value,
                            },
                          }))
                        }
                        inputMode="decimal"
                        aria-label={`${opt.label} 无参考每秒算力`}
                      />
                      <span className="text-[10px] text-muted-foreground">算力/秒</span>
                    </label>
                  ))}
                </div>
              </div>
            ))}
            {showWithRef
              ? rateGroups.map((group) => (
                  <div key={`with-${group.id}`}>
                    <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
                      {group.label} · 有参考视频（每秒算力）
                    </p>
                    <div className="flex flex-wrap gap-x-4 gap-y-2">
                      {group.items.map((opt) => (
                        <label key={opt.id} className="flex items-center gap-2 text-xs">
                          <span className="min-w-[3.5rem] text-muted-foreground">{opt.label}</span>
                          <Input
                            className="h-7 w-16 tabular-nums"
                            value={state.optionCostsWithRef[group.id]?.[opt.id] ?? "0"}
                            onChange={(e) =>
                              state.setOptionCostsWithRef((prev) => ({
                                ...prev,
                                [group.id]: {
                                  ...(prev[group.id] ?? {}),
                                  [opt.id]: e.target.value,
                                },
                              }))
                            }
                            inputMode="decimal"
                            aria-label={`${opt.label} 有参考每秒算力`}
                          />
                          <span className="text-[10px] text-muted-foreground">算力/秒</span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))
              : null}
            {item.extraImageBilling || item.billInputVideoSeconds ? (
              <div>
                <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
                  参考图超额计费
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  <label className="flex items-center gap-2 text-xs">
                    <span className="text-muted-foreground">免费张数</span>
                    <Input
                      className="h-7 w-16 tabular-nums"
                      value={state.extraImageFreeCount}
                      onChange={(e) => state.setExtraImageFreeCount(e.target.value)}
                      inputMode="decimal"
                      aria-label="参考图免费张数"
                    />
                  </label>
                  <label className="flex items-center gap-2 text-xs">
                    <span className="text-muted-foreground">超出单价</span>
                    <Input
                      className="h-7 w-16 tabular-nums"
                      value={state.extraImageCost}
                      onChange={(e) => state.setExtraImageCost(e.target.value)}
                      inputMode="decimal"
                      aria-label="超额参考图算力单价"
                    />
                    <span className="text-[10px] text-muted-foreground">算力/张</span>
                  </label>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PricingModelRow({
  item,
  stripe,
  onSaved,
  videoOnly = false,
  imageOnly = false,
}: {
  item: AdminPricingItem;
  stripe: boolean;
  onSaved: () => void;
  videoOnly?: boolean;
  imageOnly?: boolean;
}) {
  if (videoOnly || item.category === "video") {
    return <VideoPricingModelRow item={item} stripe={stripe} onSaved={onSaved} />;
  }
  if (imageOnly || item.category === "image") {
    return <ImagePricingModelRow item={item} stripe={stripe} onSaved={onSaved} />;
  }
  const state = usePricingRowState(item);
  const hasOptions = item.presetGroups.length > 0;

  const saveMutation = useMutation({
    mutationFn: () =>
      patchAdminPricing(item.modelId, {
        ...buildPatchPayload(item, state),
        yuanPerCall: parseCostInput(state.yuanPerCall),
      }),
    onSuccess: (data) => {
      const savedBase = (
        data.parameters?.pricing as { baseCost?: number } | undefined
      )?.baseCost;
      toast.success(
        savedBase != null
          ? `${item.displayName} 已保存（基础 ${savedBase} 算力）`
          : `${item.displayName} 已保存`
      );
      onSaved();
    },
    onError: () => toast.error(`${item.displayName} 保存失败`),
  });

  const resetMutation = useMutation({
    mutationFn: () => resetAdminPricing(item.modelId),
    onSuccess: () => {
      toast.success(`${item.displayName} 已恢复默认`);
      onSaved();
    },
    onError: () => toast.error("恢复默认失败"),
  });

  const previewMutation = useMutation({
    mutationFn: () => Promise.resolve(previewDraftTotal(item, state)),
    onSuccess: (total) => {
      state.setPreviewTotal(total);
      if (total <= 0) {
        toast.message(`${item.displayName} 预览为 0`, {
          description:
            state.mode === "video_per_second"
              ? "按秒×清晰度 = 基础 + 时长(秒) × 清晰度每秒算力。请检查基础与清晰度行。"
              : "累加模式 = 基础 + 各选项；主组覆盖 = 主选项价格（为 0 时用基础价）。请检查基础算力与展开项价格。",
        });
      }
    },
  });

  return (
    <div className={cn("rounded-md", stripe && "bg-muted/30")}>
      <div className="grid grid-cols-[minmax(160px,1.4fr)_88px_88px_88px_130px_minmax(120px,1fr)_auto] items-center gap-x-3 gap-y-2 px-3 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-1">
            {hasOptions ? (
              <button
                type="button"
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => state.setExpanded((v) => !v)}
                aria-label={state.expanded ? "收起选项" : "展开选项"}
              >
                {state.expanded ? (
                  <ChevronDown className="h-3.5 w-3.5" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5" />
                )}
              </button>
            ) : (
              <span className="inline-block w-4 shrink-0" />
            )}
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{item.displayName}</p>
              {state.previewTotal != null ? (
                <p className="text-[11px] text-muted-foreground">预览：{state.previewTotal} 算力</p>
              ) : null}
            </div>
          </div>
        </div>
        <Input
          className="h-8 tabular-nums"
          value={state.baseCost}
          onChange={(e) => state.setBaseCost(e.target.value)}
          inputMode="decimal"
          aria-label="基础算力"
        />
        <Input
          className="h-8 tabular-nums"
          value={state.minCost}
          onChange={(e) => state.setMinCost(e.target.value)}
          inputMode="decimal"
          aria-label="最低算力"
        />
        <Input
          className="h-8 tabular-nums"
          value={state.yuanPerCall}
          onChange={(e) => state.setYuanPerCall(e.target.value)}
          inputMode="decimal"
          placeholder="0"
          aria-label="成本价元每次"
        />
        <Select value={state.mode} onValueChange={(value) => value && state.setMode(value)}>
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="additive">累加</SelectItem>
            <SelectItem value="primary_group">主组覆盖</SelectItem>
            {item.category === "video" ? (
              <SelectItem value="video_per_second">按秒×清晰度</SelectItem>
            ) : null}
          </SelectContent>
        </Select>
        <span
          className="truncate font-mono text-xs text-muted-foreground"
          title={item.name}
        >
          {item.name}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => previewMutation.mutate()}
            disabled={previewMutation.isPending}
          >
            预览
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending}
          >
            默认
          </Button>
          <Button
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
          >
            保存
          </Button>
        </div>
      </div>

      {state.expanded && hasOptions ? (
        <div className="border-t border-border/60 bg-muted/10 px-3 py-3 pl-10">
          {state.mode === "primary_group" ? (
            <label className="mb-3 flex max-w-xs items-center gap-2 text-xs">
              <span className="shrink-0 text-muted-foreground">主选项组</span>
              <Select
                value={state.primaryGroupId || item.presetGroups[0]?.id || ""}
                onValueChange={(value) => value && state.setPrimaryGroupId(value)}
              >
                <SelectTrigger className="h-8">
                  <SelectValue placeholder="选择主组" />
                </SelectTrigger>
                <SelectContent>
                  {item.presetGroups.map((group) => (
                    <SelectItem key={group.id} value={group.id}>
                      {group.label} ({group.id})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          ) : null}
          <div className="space-y-3">
            {visiblePresetGroups(item).map((group) => (
              <div key={group.id}>
                <p className="mb-1.5 text-xs font-semibold text-muted-foreground">
                  {pricingGroupLabel(item, group, state.mode)}
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {group.items.map((opt) => (
                    <label key={opt.id} className="flex items-center gap-2 text-xs">
                      <span className="min-w-[3.5rem] text-muted-foreground">{opt.label}</span>
                      <Input
                        className="h-7 w-16 tabular-nums"
                        value={state.optionCosts[group.id]?.[opt.id] ?? "0"}
                        onChange={(e) =>
                          state.setOptionCosts((prev) => ({
                            ...prev,
                            [group.id]: {
                              ...(prev[group.id] ?? {}),
                              [opt.id]: e.target.value,
                            },
                          }))
                        }
                        inputMode="decimal"
                        aria-label={
                          state.mode === "video_per_second" && group.id === "resolution"
                            ? `${opt.label} 每秒算力`
                            : `${opt.label} 算力`
                        }
                      />
                      {state.mode === "video_per_second" && group.id === "resolution" ? (
                        <span className="text-[10px] text-muted-foreground">/秒</span>
                      ) : null}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function CanvasToolPricingSection({ onSaved }: { onSaved: () => void }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["admin", "pricing", "canvas-tools"],
    queryFn: getAdminCanvasToolPricing,
  });
  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!data?.items) return;
    const next: Record<string, string> = {};
    for (const item of data.items) {
      // 仅本地可编辑工具进入草稿（如宫格切分）
      if (item.editable !== false && item.priceSource !== "model") {
        next[item.toolId] = String(item.creditCost);
      }
    }
    setDraft(next);
  }, [data]);

  const canvasImageItems = useMemo(
    () => (data?.items ?? []).filter((item) => (item.group ?? "canvas_image") === "canvas_image"),
    [data?.items]
  );
  const canvasVideoItems = useMemo(
    () => (data?.items ?? []).filter((item) => item.group === "canvas_video"),
    [data?.items]
  );
  const storyboardItems = useMemo(
    () => (data?.items ?? []).filter((item) => item.group === "storyboard"),
    [data?.items]
  );

  const editableCount = useMemo(
    () => (data?.items ?? []).filter((item) => item.editable !== false && item.priceSource !== "model").length,
    [data?.items]
  );

  const saveMutation = useMutation({
    mutationFn: () => {
      const tools: Record<string, number> = {};
      for (const [toolId, raw] of Object.entries(draft)) {
        tools[toolId] = roundCreditAmount(raw);
      }
      return putAdminCanvasToolPricing(tools);
    },
    onSuccess: () => {
      toast.success("本地工具算力已保存（主模型价请改模型定价页）");
      void refetch();
      onSaved();
    },
    onError: () => toast.error("工具算力保存失败"),
  });

  const renderToolGrid = (
    items: typeof canvasImageItems,
    defaultUnitLabel: string
  ) => (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((item) => {
        const followModel = item.priceSource === "model" || item.editable === false;
        const unitLabel =
          item.billingMode === "video_input_plus_output_per_second"
            ? "算力/秒"
            : defaultUnitLabel;
        const modelTitle =
          item.primaryModelDisplayName?.trim() || item.primaryModel || "";
        const hasPrice = Number(item.creditCost) > 0;
        return (
          <label key={item.toolId} className="flex flex-col gap-0.5 text-sm">
            <span className="flex items-center gap-2">
              <span className="min-w-[5.5rem] text-muted-foreground">{item.label}</span>
              {followModel ? (
                <span
                  className={cn(
                    "inline-flex h-8 min-w-[6rem] items-center rounded-md border border-border bg-muted/40 px-2 font-mono text-sm tabular-nums",
                    hasPrice ? "text-foreground" : "text-destructive"
                  )}
                  title={
                    hasPrice
                      ? `用户侧将按此价预扣（${unitLabel}）`
                      : "定价页尚未配置该主模型算力"
                  }
                >
                  {hasPrice ? formatCreditAmount(item.creditCost) : "未设价"}
                </span>
              ) : (
                <Input
                  className="h-8 w-24 tabular-nums"
                  value={draft[item.toolId] ?? String(item.creditCost)}
                  onChange={(e) =>
                    setDraft((prev) => ({ ...prev, [item.toolId]: e.target.value }))
                  }
                  inputMode="decimal"
                  aria-label={`${item.label} ${unitLabel}`}
                />
              )}
              <span className="text-xs text-muted-foreground">{unitLabel}</span>
            </span>
            {followModel && item.primaryModel ? (
              <span className="pl-[5.5rem] text-[11px] leading-snug text-muted-foreground">
                跟随主模型{" "}
                <span className="font-medium text-foreground/80">{modelTitle}</span>
                {item.primaryModelDisplayName && item.primaryModelDisplayName !== item.primaryModel ? (
                  <span className="ml-1 font-mono text-[10px] opacity-70">
                    {item.primaryModel}
                  </span>
                ) : null}
                {hasPrice
                  ? item.billingMode === "video_input_plus_output_per_second"
                    ? " · 用户侧按 (输入+输出)秒 × 单价 预扣"
                    : " · 用户侧按此价预扣"
                  : " · 请到对应模型定价 Tab 设价"}
              </span>
            ) : null}
          </label>
        );
      })}
    </div>
  );

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border bg-muted/10 px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold">画布图片节点工具算力</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              已在「功能模型切换」配置主模型的功能，算力跟该主模型在定价页的配置（只读预览）；
              改价请到对应模型定价 Tab，换模型请到「模型管理 → 功能模型切换」。
              未纳管模型的本地工具（如宫格切分）仍可在此改固定价。
              {data?.version != null ? ` 当前版本 v${data.version}` : null}
            </p>
          </div>
          <Button
            size="sm"
            disabled={saveMutation.isPending || isLoading || editableCount === 0}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              "保存本地工具算力"
            )}
          </Button>
        </div>
        {isLoading ? (
          <p className="text-xs text-muted-foreground">加载中…</p>
        ) : isError ? (
          <div className="text-xs text-destructive">
            加载失败
            <Button variant="ghost" size="sm" className="ml-2 h-7" onClick={() => void refetch()}>
              重试
            </Button>
          </div>
        ) : (
          renderToolGrid(canvasImageItems, "算力")
        )}
      </div>

      <div className="rounded-lg border border-border bg-muted/10 px-4 py-3">
        <div className="mb-2">
          <h3 className="text-sm font-semibold">视频画面 / 音频工具算力</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            主模型为按秒计价时：总价 = (输入参考视频秒数 + 生成视频秒数) × 主模型默认每秒算力；
            主模型为按次计价时：按该模型单次报价。画面编辑源片须先剪辑至 ≤12 秒。
            价格跟随「功能模型切换」主模型，此处只读。
          </p>
        </div>
        {isLoading ? (
          <p className="text-xs text-muted-foreground">加载中…</p>
        ) : isError ? null : (
          renderToolGrid(canvasVideoItems, "算力")
        )}
      </div>

      <div className="rounded-lg border border-border bg-muted/10 px-4 py-3">
        <div className="mb-2">
          <h3 className="text-sm font-semibold">分镜表生成算力</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            解析剧本、爆款拉片、一键出海、主体提取、运镜/视频提示词、草图与主体生图等，
            单次算力跟随各自主模型定价（批量按镜头数累加）。此处只读预览。
          </p>
        </div>
        {isLoading ? (
          <p className="text-xs text-muted-foreground">加载中…</p>
        ) : isError ? null : (
          renderToolGrid(storyboardItems, "算力")
        )}
      </div>
    </div>
  );
}

export function AdminPricingTabPanel({
  tab,
  items,
  onSaved,
}: {
  tab: Exclude<PricingSettingsTab, "model_switches" | "agent_control">;
  items: AdminPricingItem[];
  onSaved: () => void;
}) {
  const meta = TAB_META[tab];
  const isVideoTab = tab === "ref_video_pricing" || tab === "image_to_video";
  const isImageTab = tab === "image_pricing";
  const compactLayout = isVideoTab || isImageTab;

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <h2 className="text-sm font-bold">{meta.panelTitle}</h2>
      </div>
      <div className="space-y-3 px-4 py-3">
        <p className="text-xs leading-relaxed text-muted-foreground">{meta.hint}</p>
        {tab === "general" ? <CanvasToolPricingSection onSaved={onSaved} /> : null}
        {items.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">本分类暂无模型</p>
        ) : (
          <div className="overflow-x-auto">
            <div className={cn(compactLayout ? "min-w-[620px]" : "min-w-[820px]")}>
              <div
                className={cn(
                  "gap-x-3 px-3 pb-2 text-xs font-bold text-muted-foreground",
                  isImageTab
                    ? "grid grid-cols-[minmax(160px,1.4fr)_88px_88px_minmax(120px,1fr)_auto]"
                    : compactLayout
                      ? "grid grid-cols-[minmax(160px,1.4fr)_88px_minmax(120px,1fr)_auto]"
                      : "grid grid-cols-[minmax(160px,1.4fr)_88px_88px_88px_130px_minmax(120px,1fr)_auto]"
                )}
              >
                <span>{meta.groupLabel ?? "模型"}</span>
                {isImageTab ? (
                  <>
                    <span>基础算力</span>
                    <span>成本(元/张)</span>
                    <span>模型标识</span>
                    <span className="text-right">操作</span>
                  </>
                ) : compactLayout ? (
                  <>
                    <span>{isVideoTab ? "成本(元/秒)" : "成本(元/张)"}</span>
                    <span>模型标识</span>
                    <span className="text-right">操作</span>
                  </>
                ) : (
                  <>
                    <span>基础算力</span>
                    <span>最低算力</span>
                    <span>成本(元/次)</span>
                    <span>计价模式</span>
                    <span>模型标识</span>
                    <span className="text-right">操作</span>
                  </>
                )}
              </div>
              <div className="space-y-0.5">
                {items.map((item, index) => (
                  <PricingModelRow
                    key={item.modelId}
                    item={item}
                    stripe={index % 2 === 1}
                    onSaved={onSaved}
                    videoOnly={isVideoTab}
                    imageOnly={isImageTab}
                  />
                ))}
              </div>
            </div>
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          修改后点击行内「保存」生效；画布 Quote 与提交扣费即时同步。列表仅显示画布实际使用的模型（不含
          rh_* 等历史遗留条目）。
        </p>
      </div>
    </section>
  );
}

export function AdminModelSwitchesPanel() {
  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-bold">模型开关设置</h2>
      </div>
      <div className="space-y-4 px-4 py-4">
        <p className="text-xs leading-relaxed text-muted-foreground">
          控制画布用户端可见的模型线路、上下架与生成预设。与算力价格分开管理；关闭模型后用户将无法选择，但已配置的价格仍保留。
        </p>
        <Link
          href="/admin/models"
          className="inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          前往模型开关设置
          <ExternalLink className="h-4 w-4" />
        </Link>
      </div>
    </section>
  );
}

export function AdminPricingPageHead({
  activeTab,
  onTabChange,
}: {
  activeTab: PricingSettingsTab;
  onTabChange: (tab: PricingSettingsTab) => void;
}) {
  return (
    <header className="mb-6">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <h1 className="text-2xl font-bold tracking-tight">算力价格设置</h1>
        <nav
          className="flex flex-wrap items-center gap-1 rounded-lg border border-border bg-muted/30 p-1"
          role="tablist"
          aria-label="算力价格子页面"
        >
          {PRICING_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors sm:text-sm",
                activeTab === tab.id
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground"
              )}
              onClick={() => onTabChange(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>
      <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
        各任务类型与模型的算力扣费配置；变更后画布 Quote 与提交扣费即时生效。
      </p>
    </header>
  );
}

export function usePricingTabItems(items: AdminPricingItem[], activeTab: PricingSettingsTab) {
  return useMemo(() => {
    if (activeTab === "model_switches" || activeTab === "agent_control") return [];
    return filterPricingByTab(items, activeTab);
  }, [items, activeTab]);
}
