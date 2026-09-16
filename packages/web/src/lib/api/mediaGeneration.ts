import { apiFetch, ApiError } from "./client";
import type { GenerationReference } from "./textGeneration.types";
import type { AssetCategory } from "./assets";

const VIDEO_GENERATE_TIMEOUT_MS = 25 * 60 * 1000;
/** 人声分离等上游音频任务可能较久 */
const AUDIO_GENERATE_TIMEOUT_MS = 15 * 60 * 1000;
const DEFAULT_GENERATE_TIMEOUT_MS = 8 * 60 * 1000;
const RECOVER_POLL_INTERVAL_MS = 3000;
const RECOVER_POLL_TIMEOUT_MS = 3 * 60 * 1000;

export interface GenerationJobStatus {
  id: number;
  status: string;
  assetId?: string;
  resultUrl?: string;
  outputAssets?: Array<{ url?: string; id?: string; assetId?: string }>;
  errorMessage?: string;
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function fetchLatestNodeJob(
  projectId: string,
  nodeId: string
): Promise<GenerationJobStatus | null> {
  try {
    const url = `/api/v1/generations/latest/by-node?projectId=${encodeURIComponent(projectId)}&nodeId=${encodeURIComponent(nodeId)}`;
    return await apiFetch<GenerationJobStatus>(url);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
}

/** After proxy disconnect, backend may still finish — poll latest node job. */
export async function recoverLatestNodeGeneration(
  projectId: string,
  nodeId: string,
  options?: { timeoutMs?: number; intervalMs?: number }
): Promise<MediaGenerationResponse | null> {
  const deadline = Date.now() + (options?.timeoutMs ?? RECOVER_POLL_TIMEOUT_MS);
  const intervalMs = options?.intervalMs ?? RECOVER_POLL_INTERVAL_MS;

  while (Date.now() < deadline) {
    const job = await fetchLatestNodeJob(projectId, nodeId);
    if (!job) return null;

    if (job.status === "succeeded") {
      const assetId = job.assetId ?? job.outputAssets?.[0]?.assetId ?? job.outputAssets?.[0]?.id;
      const resultUrl = job.resultUrl ?? job.outputAssets?.[0]?.url;
      return {
        jobId: job.id,
        status: "succeeded",
        assetId: assetId ? String(assetId) : undefined,
        resultUrl: resultUrl ? String(resultUrl) : undefined,
        outputAssets: job.outputAssets,
        message: `任务已完成（#${job.id}）`,
      };
    }

    if (job.status === "failed") {
      return null;
    }

    await sleep(intervalMs);
  }

  return null;
}

function shouldAttemptRecovery(err: unknown): boolean {
  if (err instanceof DOMException && err.name === "AbortError") return true;
  if (err instanceof ApiError) {
    return err.status === 502 || err.status === 504 || err.message.includes("无法连接");
  }
  return false;
}

export interface MediaGenerationRequest {
  projectId: string;
  nodeId: string;
  workflowId?: string;
  /** `tool` = admin「其他模型」分类，用于多角度等画布工具 */
  category: AssetCategory | "tool";
  prompt: string;
  model: string;
  references?: GenerationReference[];
  sourceUrl?: string;
  generationOptions?: Record<string, string>;
  visualStyleId?: string;
  /** 写入项目素材库时的子分类（如 人物/场景/物品） */
  assetSubcategory?: string;
  /** 写入项目素材库时的标题 */
  assetTitle?: string;
  /** 管理端生成日志：分镜表等批量场景传 auto */
  submitSource?: "manual" | "auto" | "dedupe";
  /** 画布工具固定算力（多角度/打光/全景等） */
  canvasTool?: string;
  /** 画面编辑：源视频素材 ID（服务端优先 OSS 内网校验时长） */
  sourceAssetId?: string;
}

export interface MediaGenerationResponse {
  jobId: number;
  status: "pending" | "awaiting_approval" | "succeeded" | "failed";
  resultUrl?: string;
  assetId?: string;
  /** 多张生成时含全部产物；首张与 assetId 一致 */
  outputAssets?: Array<{ url?: string; id?: string; assetId?: string }>;
  message?: string;
  creditCost?: number;
}

export interface GenerationSubmitOptions {
  idempotencyKey?: string;
  quoteToken?: string;
  expectedPricingVersion?: number;
}

/** 从 PRICING_CHANGED 错误体取出可重试的报价凭证与选项快照 */
export function pricingChangedRetryPayload(err: unknown): {
  quoteToken: string;
  optionSnapshot: Record<string, string>;
} | null {
  if (!(err instanceof ApiError) || err.code !== "PRICING_CHANGED") return null;
  const raw = (err.content ?? err.detail) as Record<string, unknown> | undefined;
  if (!raw || typeof raw !== "object") return null;
  const quoteToken = typeof raw.quoteToken === "string" ? raw.quoteToken.trim() : "";
  if (!quoteToken) return null;
  const snap = raw.optionSnapshot;
  const optionSnapshot =
    snap && typeof snap === "object" && !Array.isArray(snap)
      ? Object.fromEntries(
          Object.entries(snap as Record<string, unknown>).map(([k, v]) => [k, String(v ?? "")])
        )
      : {};
  return { quoteToken, optionSnapshot };
}

async function postMediaGenerate(
  request: MediaGenerationRequest,
  options?: GenerationSubmitOptions,
  signal?: AbortSignal
): Promise<MediaGenerationResponse> {
  return apiFetch<MediaGenerationResponse>("/api/v1/media/generate", {
    method: "POST",
    headers: options?.idempotencyKey
      ? { "Idempotency-Key": options.idempotencyKey }
      : undefined,
    body: JSON.stringify({
      projectId: request.projectId,
      nodeId: request.nodeId,
      workflowId: request.workflowId,
      category: request.category,
      prompt: request.prompt,
      model: request.model,
      references: request.references ?? [],
      sourceUrl: request.sourceUrl,
      generationOptions: request.generationOptions ?? {},
      ...(request.visualStyleId ? { visualStyleId: request.visualStyleId } : {}),
      ...(request.assetSubcategory ? { assetSubcategory: request.assetSubcategory } : {}),
      ...(request.assetTitle ? { assetTitle: request.assetTitle } : {}),
      ...(request.submitSource ? { submitSource: request.submitSource } : {}),
      ...(request.canvasTool ? { canvasTool: request.canvasTool } : {}),
      ...(request.sourceAssetId ? { sourceAssetId: request.sourceAssetId } : {}),
      ...(options?.quoteToken ? { quoteToken: options.quoteToken } : {}),
      ...(options?.expectedPricingVersion != null
        ? { expectedPricingVersion: options.expectedPricingVersion }
        : {}),
    }),
    signal,
  });
}

export async function generateFromMediaNode(
  request: MediaGenerationRequest,
  options?: GenerationSubmitOptions
): Promise<MediaGenerationResponse> {
  const controller = new AbortController();
  const timeoutMs =
    request.category === "video"
      ? VIDEO_GENERATE_TIMEOUT_MS
      : request.category === "audio"
        ? AUDIO_GENERATE_TIMEOUT_MS
        : DEFAULT_GENERATE_TIMEOUT_MS;
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    try {
      return await postMediaGenerate(request, options, controller.signal);
    } catch (err) {
      // 服务端权威用量与前端 quoteToken 不一致时：用 409 content 内新凭证静默重试一次
      const retry = pricingChangedRetryPayload(err);
      if (!retry) throw err;
      const retryKey = options?.idempotencyKey
        ? `${options.idempotencyKey}-pr`
        : undefined;
      return await postMediaGenerate(
        {
          ...request,
          generationOptions: {
            ...(request.generationOptions ?? {}),
            ...retry.optionSnapshot,
          },
        },
        {
          ...options,
          idempotencyKey: retryKey,
          quoteToken: retry.quoteToken,
          expectedPricingVersion: undefined,
        },
        controller.signal
      );
    }
  } catch (err) {
    if (shouldAttemptRecovery(err)) {
      const recovered = await recoverLatestNodeGeneration(request.projectId, request.nodeId);
      if (recovered) return recovered;
    }
    throw err;
  } finally {
    window.clearTimeout(timer);
  }
}
