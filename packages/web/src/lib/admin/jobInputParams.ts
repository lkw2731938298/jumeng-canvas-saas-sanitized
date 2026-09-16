/** Split generation job inputParams for admin display (avoid conflating refs with billing meta). */

const BILLING_META_KEYS = new Set([
  "actorUserId",
  "pricingVersion",
  "optionSnapshot",
]);

const GENERATION_OPTION_KEYS = new Set(["generationOptions", "visualStyleId"]);

const CONTEXT_KEYS = new Set(["projectId", "nodeId", "model", "category", "assetTitle", "assetSubcategory"]);

export interface SplitJobInputParams {
  prompt?: string;
  sourceUrl?: string;
  references: unknown[];
  generationOptions: Record<string, unknown>;
  billingMeta: Record<string, unknown>;
  context: Record<string, unknown>;
}

export function splitJobInputParams(raw: Record<string, unknown> | undefined | null): SplitJobInputParams {
  const params = raw ?? {};
  const references = Array.isArray(params.references) ? params.references : [];

  const generationOptions: Record<string, unknown> = {};
  const billingMeta: Record<string, unknown> = {};
  const context: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(params)) {
    if (key === "prompt" || key === "sourceUrl" || key === "references") continue;
    if (GENERATION_OPTION_KEYS.has(key)) {
      generationOptions[key] = value;
      continue;
    }
    if (BILLING_META_KEYS.has(key)) {
      billingMeta[key] = value;
      continue;
    }
    if (CONTEXT_KEYS.has(key)) {
      context[key] = value;
      continue;
    }
    context[key] = value;
  }

  const snapshot = params.optionSnapshot;
  if (
    snapshot &&
    typeof snapshot === "object" &&
    !Array.isArray(snapshot) &&
    !generationOptions.generationOptions
  ) {
    generationOptions.optionSnapshot = snapshot;
  }

  return {
    prompt: typeof params.prompt === "string" ? params.prompt : undefined,
    sourceUrl: typeof params.sourceUrl === "string" ? params.sourceUrl : undefined,
    references,
    generationOptions,
    billingMeta,
    context,
  };
}
