import { apiFetch } from "./client";
import type { TextGenerationRequest, TextGenerationResponse } from "./textGeneration.types";

export type { TextGenerationRequest, TextGenerationResponse } from "./textGeneration.types";
export type { GenerationSubmitOptions } from "./mediaGeneration";

import type { GenerationSubmitOptions } from "./mediaGeneration";

export async function generateFromTextNode(
  request: TextGenerationRequest,
  options?: GenerationSubmitOptions
): Promise<TextGenerationResponse> {
  return apiFetch<TextGenerationResponse>("/api/v1/text/generate", {
    method: "POST",
    headers: options?.idempotencyKey
      ? { "Idempotency-Key": options.idempotencyKey }
      : undefined,
    body: JSON.stringify({
      projectId: request.projectId,
      nodeId: request.nodeId,
      workflowId: request.workflowId,
      content: request.content,
      model: request.model,
      ...(request.textPromptKind ? { textPromptKind: request.textPromptKind } : {}),
      references: request.references ?? [],
      ...(request.submitSource ? { submitSource: request.submitSource } : {}),
      ...(request.canvasTool ? { canvasTool: request.canvasTool } : {}),
      ...(options?.quoteToken ? { quoteToken: options.quoteToken } : {}),
      ...(options?.expectedPricingVersion != null
        ? { expectedPricingVersion: options.expectedPricingVersion }
        : {}),
    }),
  });
}
