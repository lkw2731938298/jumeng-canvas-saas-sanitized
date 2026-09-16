import { apiFetch } from "./client";

export interface CreditBreakdownItem {
  groupId: string;
  itemId: string;
  label: string;
  cost: number;
}

export interface CreditQuote {
  model: string;
  total: number;
  base: number;
  breakdown: CreditBreakdownItem[];
  pricingVersion: number;
  optionSnapshot: Record<string, string>;
  creditsEnabled: boolean;
  quoteToken: string;
  expiresAt: string;
}

export interface CreditTypeBreakdown {
  activity: number;
  modelSpecific: number;
  subscription: number;
  general: number;
}

export interface ModelSpecificBalance {
  model: string;
  /** 模型目录展示名 */
  modelDisplayName?: string | null;
  balance: number;
  expiresAt?: string | null;
}

export interface ExpiringCredit {
  type: string;
  amount: number;
  expiresAt: string;
  model?: string | null;
  modelDisplayName?: string | null;
}

export interface CreditBalance {
  balance: number | null;
  availableForModel?: number | null;
  creditsEnabled: boolean;
  breakdown?: CreditTypeBreakdown | null;
  modelSpecific?: ModelSpecificBalance[];
  expiringSoon?: ExpiringCredit[];
  consumePriority?: string[];
}

export const CREDIT_TYPE_LABELS: Record<string, string> = {
  model_specific: "模型专用算力",
  activity: "活动算力",
  subscription: "会员订阅算力",
  general: "通用算力",
};

export const DEFAULT_CONSUME_PRIORITY = [
  "model_specific",
  "activity",
  "subscription",
  "general",
] as const;

export function getCreditQuote(params: {
  model: string;
  category?: string;
  generationOptions?: Record<string, string>;
  /** 画布工具固定算力（多角度/打光/全景等） */
  canvasTool?: string;
}) {
  const search = new URLSearchParams();
  search.set("model", params.model);
  if (params.category) search.set("category", params.category);
  if (params.generationOptions && Object.keys(params.generationOptions).length > 0) {
    search.set("generationOptions", JSON.stringify(params.generationOptions));
  }
  if (params.canvasTool) search.set("canvasTool", params.canvasTool);
  return apiFetch<CreditQuote>(`/api/v1/credits/quote?${search.toString()}`);
}

export interface CanvasToolPricingItem {
  toolId: string;
  label: string;
  group?: string;
  /** fixed | video_input_plus_output_per_second */
  billingMode?: string;
  /** 固定价，或视频工具的「每秒算力」 */
  creditCost: number;
  creditsPerSecond?: number | null;
  priceSource?: string;
  primaryModel?: string | null;
  /** 主模型中文名，便于管理端对照定价页 */
  primaryModelDisplayName?: string | null;
}

export interface CanvasToolPricingResponse {
  version: number;
  items: CanvasToolPricingItem[];
  creditsEnabled: boolean;
}

/** 画布顶栏：拉取各工具算力（含视频每秒价） */
export function getCanvasToolPricing() {
  return apiFetch<CanvasToolPricingResponse>("/api/v1/credits/canvas-tool-pricing");
}

export function postCreditQuotesBatch(items: Array<{
  model: string;
  category?: string;
  generationOptions?: Record<string, string>;
}>) {
  return apiFetch<{ items: CreditQuote[] }>("/api/v1/credits/quotes", {
    method: "POST",
    body: JSON.stringify({
      items: items.map((item) => ({
        model: item.model,
        category: item.category,
        generationOptions: item.generationOptions ?? {},
      })),
    }),
  });
}

export function getCreditBalance(model?: string) {
  const search = model ? `?model=${encodeURIComponent(model)}` : "";
  return apiFetch<CreditBalance>(`/api/v1/credits/balance${search}`);
}

export function getConsumePriority() {
  return apiFetch<{ priority: string[]; labels: Record<string, string> }>(
    "/api/v1/credits/consume-priority"
  );
}

export function putConsumePriority(priority: string[]) {
  return apiFetch<{ priority: string[]; labels: Record<string, string> }>(
    "/api/v1/credits/consume-priority",
    {
      method: "PUT",
      body: JSON.stringify({ priority }),
    }
  );
}

export interface CreditTransaction {
  id: string;
  delta: number;
  balanceAfter: number;
  source: string;
  sourceLabel?: string;
  creditType?: string;
  modelName?: string;
  modelDisplayName?: string;
  jobId?: string;
  reason?: string;
  createdAt: string;
  /** 入账批次过期时间；充值通用算力为 null + expiresLabel=永久 */
  expiresAt?: string | null;
  expiresLabel?: string | null;
}

export interface CreditTransactionList {
  items: CreditTransaction[];
  total: number;
  page: number;
  pageSize: number;
}

export function listCreditTransactions(page = 1, pageSize = 20, source?: string) {
  const search = new URLSearchParams();
  search.set("page", String(page));
  search.set("pageSize", String(pageSize));
  if (source?.trim()) search.set("source", source.trim());
  return apiFetch<CreditTransactionList>(`/api/v1/credits/transactions?${search.toString()}`);
}

export interface CreditActivity {
  id: string;
  title: string;
  description: string;
  coverUrl?: string;
  creditType: string;
  amount: number;
  modelName?: string | null;
  /** 模型目录展示名；用户侧仅展示此字段，不展示 modelName */
  modelDisplayName?: string | null;
  validDays: number;
  startsAt?: string | null;
  endsAt?: string | null;
  perUserLimit: number;
  totalQuota?: number | null;
  claimedCount: number;
  status: string;
  userClaimCount: number;
  canClaim: boolean;
  remainingQuota?: number | null;
  claimRules?: {
    registeredFrom?: string | null;
    registeredTo?: string | null;
    minRechargeFen?: number | null;
    minRechargeCredits?: number | null;
    requireActiveMember?: boolean | null;
  } | null;
  claimRuleSummary?: string[];
  eligible?: boolean;
  eligibilityReasons?: string[];
}

export function listCreditActivities() {
  return apiFetch<{ items: CreditActivity[] }>("/api/v1/credits/activities");
}

export function claimCreditActivity(activityId: string) {
  return apiFetch<{
    ok: boolean;
    activityId: string;
    amount: number;
    creditType: string;
    balance: number;
    expiresAt: string;
  }>(`/api/v1/credits/activities/${activityId}/claim`, { method: "POST" });
}

export function resolveQuoteTotal(data: {
  total: number;
  base: number;
  breakdown?: Array<{ cost: number }>;
}): number {
  if (data.total > 0) return roundCreditAmount(data.total);
  const sum = data.breakdown?.reduce((s, row) => s + (Number(row.cost) || 0), 0) ?? 0;
  if (sum > 0) return roundCreditAmount(sum);
  return roundCreditAmount(Math.max(Number(data.base) || 0, 0));
}

/** 算力金额规范为非负一位小数 */
export function roundCreditAmount(raw: unknown): number {
  const n =
    typeof raw === "number"
      ? raw
      : parseFloat(String(raw ?? "").replace(",", ".").trim());
  if (!Number.isFinite(n) || n < 0) return 0;
  return Math.round(n * 10) / 10;
}

/** 展示用：整数不显示小数点，否则一位小数 */
export function formatCreditAmount(cost: number): string {
  const n = roundCreditAmount(cost);
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function formatCreditLabel(cost: number, creditsEnabled = true): string {
  // 价格为 0 视为「未设置价格」：不再显示为免费，点击生成时后端会拦截提示配置价格。
  if (cost <= 0) return "未设置价格";
  const label = formatCreditAmount(cost);
  if (!creditsEnabled) return `${label} 算力（免扣费）`;
  return `${label} 算力`;
}

/** Agent 编排轨 S 报价（创作框 / AI 操控） */
export type AgentSkillPricing = {
  version: number;
  sessionStart: number;
  /** 一次对话算力（用户发一条并触发 AI） */
  conversationTurn: number;
  /** 创建 Skill「按类型 AI 填充」单次算力 */
  skillAiFill?: number;
  skills: Record<string, number>;
  controllerModels?: string[];
  creditsEnabled: boolean;
  quote: {
    total: number;
    base: number;
    pricingVersion: number;
    skillSlug?: string | null;
  };
  skillAiFillQuote?: {
    total: number;
    base: number;
    pricingVersion: number;
  };
};

export async function getAgentSkillPricing(skillSlug?: string): Promise<AgentSkillPricing> {
  const q = skillSlug ? `?skillSlug=${encodeURIComponent(skillSlug)}` : "";
  return apiFetch<AgentSkillPricing>(`/api/v1/credits/agent-skill-pricing${q}`);
}

export function getOptionCreditCost(
  pricing: Record<string, unknown> | undefined,
  groupId: string,
  itemId: string,
  optionSnapshot?: Record<string, string>
): number {
  if (!pricing || typeof pricing !== "object") return 0;
  const refGid = String(pricing.refVideoGroupId || "refVideo");
  const useWithRef = optionSnapshot?.[refGid] === "with";
  const root = (
    useWithRef
      ? (pricing.optionsWithVideoReference as Record<string, Record<string, number>> | undefined)
      : undefined
  ) ?? (pricing.options as Record<string, Record<string, number>> | undefined);
  const group = root?.[groupId];
  if (!group) return 0;
  const raw = group[itemId];
  return typeof raw === "number" && raw > 0 ? raw : 0;
}

export function formatCreditTypeLabel(type: string): string {
  return CREDIT_TYPE_LABELS[type] ?? type;
}
