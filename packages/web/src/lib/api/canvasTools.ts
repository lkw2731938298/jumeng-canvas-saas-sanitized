import { apiFetch } from "./client";

/** 画布工具主/副模型（后台「模型开关」配置） */
export interface CanvasToolModelItem {
  toolId: string;
  category: string;
  primary: string;
  secondary: string;
}

export interface CanvasToolModelsResponse {
  version: number;
  tools: Record<string, CanvasToolModelItem>;
}

/** 与 apiFetch.convertKeys 相同：下划线字段转驼峰（工具 id 会被误转） */
function toolIdToCamelKey(toolId: string): string {
  return toolId.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

/**
 * 按工具 id 取后台主/副模型配置。
 * apiFetch 会递归把对象 key 转驼峰，`video_subject_edit` 会变成 `videoSubjectEdit`，
 * 直接 `tools[toolId]` 会读空，助手路径就会误报「后台未配置模型」。
 */
export function canvasToolModelEntry(
  tools: Record<string, CanvasToolModelItem> | undefined,
  toolId: string
): CanvasToolModelItem | undefined {
  if (!tools || !toolId) return undefined;
  const direct = tools[toolId];
  if (direct) return direct;
  const camel = toolIdToCamelKey(toolId);
  if (camel !== toolId) {
    const byCamel = tools[camel];
    if (byCamel) return byCamel;
  }
  return Object.values(tools).find((item) => item?.toolId === toolId);
}

/** 读取某画布工具的后台主模型名；未配置返回空串 */
export function canvasToolModelPrimary(
  tools: Record<string, CanvasToolModelItem> | undefined,
  toolId: string
): string {
  return String(canvasToolModelEntry(tools, toolId)?.primary || "").trim();
}

/** 读取后台配置的画布工具模型 */
export async function getCanvasToolModels(): Promise<CanvasToolModelsResponse> {
  return apiFetch<CanvasToolModelsResponse>("/api/v1/canvas-tools/models");
}

/** 拉取后台主模型；读失败或未配置时返回 fallback（与人手顶栏兜底对齐） */
export async function resolveCanvasToolPrimaryModel(
  toolId: string,
  fallback = ""
): Promise<string> {
  try {
    const data = await getCanvasToolModels();
    return canvasToolModelPrimary(data.tools, toolId) || fallback;
  } catch {
    return fallback;
  }
}

/** 本地画布工具扣费结果（不经上游，立即结单）。 */
export interface LocalCanvasToolConsumeResult {
  jobId: number;
  status: string;
  message?: string;
  requestId?: string;
  creditCost: number;
}

/** 宫格切分等本地工具：校验 quoteToken 后扣算力并立即成功。 */
export async function consumeLocalCanvasTool(
  body: {
    projectId: string;
    nodeId: string;
    workflowId?: string;
    model: string;
    canvasTool: string;
    quoteToken?: string;
    expectedPricingVersion?: number;
    rows?: number;
    cols?: number;
    submitSource?: string;
    /** 视频本地工具计费秒数 */
    inputVideoSeconds?: number;
    outputVideoSeconds?: number;
    durationSec?: number;
  },
  opts?: { idempotencyKey?: string }
): Promise<LocalCanvasToolConsumeResult> {
  return apiFetch<LocalCanvasToolConsumeResult>("/api/v1/canvas-tools/local-consume", {
    method: "POST",
    body: JSON.stringify({
      projectId: body.projectId,
      nodeId: body.nodeId,
      workflowId: body.workflowId,
      model: body.model,
      canvasTool: body.canvasTool,
      quoteToken: body.quoteToken,
      expectedPricingVersion: body.expectedPricingVersion,
      rows: body.rows,
      cols: body.cols,
      submitSource: body.submitSource,
      ...(body.inputVideoSeconds != null
        ? { inputVideoSeconds: body.inputVideoSeconds }
        : {}),
      ...(body.outputVideoSeconds != null
        ? { outputVideoSeconds: body.outputVideoSeconds }
        : {}),
      ...(body.durationSec != null ? { durationSec: body.durationSec } : {}),
    }),
    headers: opts?.idempotencyKey
      ? { "Idempotency-Key": opts.idempotencyKey }
      : undefined,
  });
}
