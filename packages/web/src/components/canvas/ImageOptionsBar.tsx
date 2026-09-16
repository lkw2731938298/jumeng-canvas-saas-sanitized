"use client";

import type { GenerationOptions, GenerationPresetsConfig } from "@/types/generationPresets";
import { GenerationOptionsDropdowns } from "./GenerationOptionsDropdowns";
import { VideoGenerationOptionsCollapsible } from "./VideoGenerationOptionsCollapsible";

interface ImageOptionsBarProps {
  presets: GenerationPresetsConfig;
  value: GenerationOptions;
  onChange: (next: GenerationOptions) => void;
  pricing?: Record<string, unknown>;
  /** audio：与视频相同的折叠摘要条（歌曲 · 采样率 · …） */
  variant?: "image" | "video" | "audio";
  layout?: "block" | "inline";
  /** Current model name — which models use screenshot options panel */
  modelName?: string;
}

function isVideoPresets(presets: GenerationPresetsConfig): boolean {
  return presets.groups.some((g) => g.id === "duration" || g.id === "ratio");
}

/** 「全能图片 G/Pro …（官方稳定）」显示创作工具宫格菜单 */
export function isNanoOfficialCreativeToolsModel(modelName: string | undefined): boolean {
  if (!modelName || !modelName.includes("_official")) return false;
  return modelName.startsWith("nano_g_") || modelName.startsWith("nano_pro_");
}

/** @deprecated use isNanoOfficialCreativeToolsModel */
export const isNanoGOfficialModel = isNanoOfficialCreativeToolsModel;

/** 图片模型统一使用截图风格参数面板，保证不同模型的交互一致 */
export function isScreenshotOptionsModel(modelName: string | undefined): boolean {
  return Boolean(modelName);
}

/** @deprecated use isScreenshotOptionsModel */
export const isNanoScreenshotOptionsModel = isScreenshotOptionsModel;

/** Inline generation options on node editor popup (image / video / audio). */
export function ImageOptionsBar({
  presets,
  value,
  onChange,
  pricing,
  variant,
  layout = "block",
  modelName,
}: ImageOptionsBarProps) {
  const useCollapsiblePanel =
    variant === "video" ||
    variant === "audio" ||
    (variant === "image" && isScreenshotOptionsModel(modelName)) ||
    (variant !== "image" && variant !== "video" && variant !== "audio" && isVideoPresets(presets));

  if (useCollapsiblePanel) {
    const summaryFallback =
      variant === "image"
        ? "点击设置图片参数"
        : variant === "audio"
          ? "点击设置音频参数"
          : "点击设置视频参数";
    return (
      <VideoGenerationOptionsCollapsible
        presets={presets}
        value={value}
        onChange={onChange}
        pricing={pricing}
        layout={layout}
        summaryFallback={summaryFallback}
        leadingIcon={variant === "audio" ? "music" : "ratio"}
      />
    );
  }

  if (layout === "inline") {
    return (
      <GenerationOptionsDropdowns presets={presets} value={value} onChange={onChange} pricing={pricing} />
    );
  }

  return (
    <div className="border-b border-white/10 px-3 py-2.5">
      <GenerationOptionsDropdowns presets={presets} value={value} onChange={onChange} pricing={pricing} />
    </div>
  );
}
