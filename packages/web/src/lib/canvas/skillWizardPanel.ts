/**
 * 爆款复刻 / 一键出海：画布右下角技能控制面板定位（fixed，避开缩放工具条）。
 */

/** 画布右下角缩放工具条大约高度 + 间距 */
export const SKILL_PANEL_TOOLBAR_CLEARANCE = 72;
export const SKILL_PANEL_EDGE = 16;
export const SKILL_PANEL_WIDTH = 380;
/** 估算展开高度，用于默认落点 */
export const SKILL_PANEL_EST_HEIGHT = 360;

/** 默认展开位置：视口右下角、工具条上方 */
export function defaultSkillPanelPos(
  width = SKILL_PANEL_WIDTH,
  height = SKILL_PANEL_EST_HEIGHT
): { x: number; y: number } {
  if (typeof window === "undefined") {
    return { x: SKILL_PANEL_EDGE, y: SKILL_PANEL_EDGE + 56 };
  }
  return {
    x: Math.max(SKILL_PANEL_EDGE, window.innerWidth - width - SKILL_PANEL_EDGE),
    y: Math.max(
      56,
      window.innerHeight - height - SKILL_PANEL_TOOLBAR_CLEARANCE
    ),
  };
}

/** 收纳条：贴在右下角工具条上方（可再叠一层出海） */
export const skillPanelCollapsedClass =
  "fixed right-4 bottom-[4.75rem] z-40 flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium shadow-lg backdrop-blur-md transition-colors";

export const skillPanelCollapsedOverseasClass =
  "fixed right-4 bottom-[8.25rem] z-40 flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-medium shadow-lg backdrop-blur-md transition-colors";
