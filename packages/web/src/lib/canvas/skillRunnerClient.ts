/**
 * 前端 Skill Runner：按后端 skillRun.clientAction 调度现有流水线适配器。
 * 一期不重写切镜/拉片，只统一入口（对齐设计方案 Skill Runner）。
 */

import type { RunViralRemakePayload } from "@/lib/api/agentSessions";
import { runOverseasFromAgentSession } from "@/lib/canvas/overseasAgentBridge";
import {
  OVERSEAS_LOCALIZE_SKILL,
} from "@/lib/canvas/overseasSession";
import {
  runViralRemakeBatchFromAgentSession,
  runViralRemakeFromAgentSession,
  runViralRemakeSubjectsFromAgentSession,
} from "@/lib/canvas/viralRemakeAgentBridge";
import { runCinematicBatchFromAgentSession } from "@/lib/canvas/cinematicAgentBridge";

export type SkillRunPlan = {
  slug?: string;
  entryKind?: string;
  execution?: string;
  source?: string;
  pipeline?: Array<{
    agent: string;
    action: string;
    trigger?: string;
    clientAction?: string;
  }>;
  event?: string;
  clientAction?: string;
  activeSteps?: unknown[];
  message?: string;
};

export type AgentResultWithSkillRun = {
  sessionId?: string;
  projectId?: string | number;
  skillRun?: SkillRunPlan | null;
  runViralRemake?: RunViralRemakePayload | null;
  runViralRemakeBatch?: boolean;
  runCinematicBatch?: boolean;
  runViralRemakeSubjects?: boolean;
};

/** 执行后端规划的客户端动作；兼容旧 runViralRemake* 字段。 */
export async function dispatchSkillRun(opts: {
  projectId: string;
  sessionId: string;
  result: AgentResultWithSkillRun;
}): Promise<"analyze" | "batch" | "subjects" | "cinematic_batch" | "noop" | "none"> {
  const { projectId, sessionId, result } = opts;
  const plan = result.skillRun;
  const action =
    (plan?.clientAction || "").trim() ||
    (result.runViralRemake ? "run_analyze" : "") ||
    (result.runViralRemakeBatch ? "run_batch" : "") ||
    (result.runCinematicBatch ? "run_cinematic_batch" : "") ||
    (result.runViralRemakeSubjects ? "run_subjects" : "") ||
    "";

  if (!action || action === "noop") {
    if (plan?.message) {
      /* 提示类 noop：由会话消息已说明，此处不 toast */
    }
    return action === "noop" ? "noop" : "none";
  }

  if (action === "run_analyze") {
    const brief = result.runViralRemake;
    if (!brief?.videoAssetId) return "none";
    const entryKind = String(
      plan?.entryKind || brief.entryKind || ""
    ).trim();
    // 一键出海：走本地化拉片桥（须带 targetMarketId）
    if (entryKind === OVERSEAS_LOCALIZE_SKILL || entryKind === "overseas") {
      await runOverseasFromAgentSession({
        projectId: String(result.projectId || projectId),
        sessionId: result.sessionId || sessionId,
        brief,
      });
      return "analyze";
    }
    await runViralRemakeFromAgentSession({
      projectId: String(result.projectId || projectId),
      sessionId: result.sessionId || sessionId,
      brief,
    });
    return "analyze";
  }

  if (action === "run_batch") {
    await runViralRemakeBatchFromAgentSession({
      projectId: String(result.projectId || projectId),
      sessionId: result.sessionId || sessionId,
    });
    return "batch";
  }

  if (action === "run_cinematic_batch") {
    await runCinematicBatchFromAgentSession({
      projectId: String(result.projectId || projectId),
      sessionId: result.sessionId || sessionId,
    });
    return "cinematic_batch";
  }

  if (action === "run_subjects") {
    await runViralRemakeSubjectsFromAgentSession({
      projectId: String(result.projectId || projectId),
      sessionId: result.sessionId || sessionId,
    });
    return "subjects";
  }

  return "none";
}
