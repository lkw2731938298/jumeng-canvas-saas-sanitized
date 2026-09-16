/** 创作框 / Agent 面板共用的技能参数选项（爆款复刻 · 一键出海）。 */

import type { ViralRemakeAspect, ViralRemakeClarity } from "@/lib/canvas/viralRemakeSession";
import { OVERSEAS_MARKETS, type OverseasMarket } from "@/lib/canvas/overseasMarkets";

export type SkillComposerAspect = ViralRemakeAspect;
export type SkillComposerClarity = ViralRemakeClarity;

export const SKILL_COMPOSER_ASPECT_OPTIONS: SkillComposerAspect[] = ["9:16", "16:9", "1:1"];
export const SKILL_COMPOSER_CLARITY_OPTIONS: SkillComposerClarity[] = ["1080p", "720p"];

export { OVERSEAS_MARKETS };
export type { OverseasMarket };

export function overseasMarketLabel(id: string): string {
  const hit = OVERSEAS_MARKETS.find((m) => m.id === id);
  return hit?.label ?? "美国";
}
