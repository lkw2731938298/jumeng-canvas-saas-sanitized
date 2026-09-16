import { apiFetch } from "../client";
import { withBasePath } from "@/lib/basePath";
import { buildAdminJobQuery, type AdminJobFilterParams } from "@/lib/admin/adminJobQuery";
import type {
  AdminJobDetail,
  AdminJobList,
  AdminModel,
  AdminProjectDetail,
  AdminProjectList,
  AdminStats,
  AdminUser,
  AdminUserCredits,
  AdminUserDetail,
  AdminUserList,
  AdminCreditTransactionList,
  AdminRechargeOrderList,
  AdminRechargeTiersConfig,
  AdminRechargeTierItem,
  AdminAuthSettings,
  AdminAuthEventList,
  AdminGenerationLogList,
  AdminHomepageSettings,
} from "@/types/admin";
import type { PromptTemplate, PromptTemplateList } from "@/lib/api/promptTemplates";
import type { PromptToolConfig } from "@/lib/canvas/renderToolPrompt";

export function getAdminMe() {
  return apiFetch<AdminUser>("/api/v1/admin/me");
}

export type AdminPermissionCatalogItem = {
  key: string;
  label: string;
  group: string;
};

export type AdminAccountPermissions = {
  id: string;
  userNo?: string;
  displayName: string;
  phone?: string;
  role: string;
  isSuperAdmin: boolean;
  isActive: boolean;
  permissions: string[];
};

export function getAdminPermissionCatalog() {
  return apiFetch<{ items: AdminPermissionCatalogItem[] }>(
    "/api/v1/admin/admin-permissions/catalog"
  );
}

export function listAdminAccountsWithPermissions() {
  return apiFetch<{ items: AdminAccountPermissions[] }>(
    "/api/v1/admin/admin-permissions/admins"
  );
}

export function putAdminAccountPermissions(userId: string, permissions: string[]) {
  return apiFetch<AdminAccountPermissions>(
    `/api/v1/admin/admin-permissions/admins/${userId}`,
    {
      method: "PUT",
      body: JSON.stringify({ permissions }),
    }
  );
}

export function getAdminStats() {
  return apiFetch<AdminStats>("/api/v1/admin/stats");
}

export function listAdminJobs(params?: AdminJobFilterParams) {
  const qs = buildAdminJobQuery(params ?? {});
  return apiFetch<AdminJobList>(`/api/v1/admin/jobs${qs ? `?${qs}` : ""}`);
}

export async function exportAdminJobs(params?: Omit<AdminJobFilterParams, "page" | "pageSize">) {
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const qs = buildAdminJobQuery(params ?? {});
  const token = typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/jobs/export${qs ? `?${qs}` : ""}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = (body as { detail?: string }).detail;
    throw new Error(typeof detail === "string" ? detail : "导出失败");
  }
  const blob = await res.blob();
  const total = res.headers.get("X-Export-Total");
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match?.[1] || "generation_jobs.csv";
  return { blob, filename, total: total ? Number(total) : undefined };
}

export function getAdminJobDetail(jobId: string) {
  return apiFetch<AdminJobDetail>(`/api/v1/admin/jobs/${jobId}`);
}

export function syncAdminActiveJobs(limit = 100) {
  return apiFetch<{
    interruptedRecovered: number;
    staleRecovered: number;
    exhaustedFailed: number;
    exhaustedPendingFailed?: number;
    upstreamSynced: Array<Record<string, unknown>>;
    upstreamSkipped: Array<Record<string, unknown>>;
    statusCounts: Record<string, number>;
  }>(`/api/v1/admin/jobs/sync-active?limit=${limit}`, {
    method: "POST",
  });
}

export function postAdminJobAction(
  jobId: string,
  data: {
    action: "sync_upstream" | "force_succeed" | "force_fail" | "requeue" | "reconcile_credits";
    note: string;
    payload?: Record<string, unknown>;
  }
) {
  return apiFetch<import("@/types/admin").AdminJobActionResult>(
    `/api/v1/admin/jobs/${jobId}/actions`,
    {
      method: "POST",
      body: JSON.stringify(data),
    }
  );
}

export function listAdminModels(params?: {
  category?: string;
  provider?: string;
  search?: string;
}) {
  const search = new URLSearchParams();
  if (params?.category) search.set("category", params.category);
  if (params?.provider) search.set("provider", params.provider);
  if (params?.search) search.set("search", params.search);
  const qs = search.toString();
  return apiFetch<AdminModel[]>(`/api/v1/admin/models${qs ? `?${qs}` : ""}`);
}

/** 全局模型 UI 标签（管理端） */
export interface AdminModelUiTag {
  id: string;
  label: string;
  sortOrder: number;
  enabled: boolean;
  categories: string[];
}

export interface AdminModelUiTagsResponse {
  version: number;
  tags: AdminModelUiTag[];
}

export function getAdminModelUiTags() {
  return apiFetch<AdminModelUiTagsResponse>("/api/v1/admin/models/ui-tags");
}

export function putAdminModelUiTags(body: {
  version?: number;
  tags: Array<{
    id: string;
    label: string;
    sortOrder?: number;
    enabled?: boolean;
    categories?: string[];
  }>;
}) {
  return apiFetch<AdminModelUiTagsResponse>("/api/v1/admin/models/ui-tags", {
    method: "PUT",
    body: JSON.stringify({
      version: body.version,
      tags: body.tags.map((t) => ({
        id: t.id,
        label: t.label,
        sort_order: t.sortOrder ?? 0,
        enabled: t.enabled !== false,
        categories: t.categories ?? [],
      })),
    }),
  });
}

/** 画布选模左侧「系列」展示顺序（管理端） */
export interface AdminModelUiSeriesItem {
  label: string;
  sortOrder: number;
  enabled: boolean;
  categories: string[];
}

export interface AdminModelUiSeriesResponse {
  version: number;
  series: AdminModelUiSeriesItem[];
}

export function getAdminModelUiSeries() {
  return apiFetch<AdminModelUiSeriesResponse>("/api/v1/admin/models/ui-series");
}

export function putAdminModelUiSeries(body: {
  version?: number;
  series: Array<{
    label: string;
    sortOrder?: number;
    enabled?: boolean;
    categories?: string[];
  }>;
}) {
  return apiFetch<AdminModelUiSeriesResponse>("/api/v1/admin/models/ui-series", {
    method: "PUT",
    body: JSON.stringify({
      version: body.version,
      series: body.series.map((s) => ({
        label: s.label,
        sort_order: s.sortOrder ?? 0,
        enabled: s.enabled !== false,
        categories: s.categories ?? [],
      })),
    }),
  });
}

export function patchAdminModel(
  modelId: string,
  data: {
    isAvailable?: boolean;
    sortOrder?: number;
    displayName?: string;
    provider?: string;
    modelType?: string;
    category?: string;
    description?: string;
    coverUrl?: string;
    parameters?: Record<string, unknown>;
    generationPresets?: Record<string, unknown>;
    upstreamModel?: string;
    capabilities?: string[];
    implementation?: string;
    channels?: Array<Record<string, unknown>>;
  }
) {
  const body: Record<string, unknown> = {};
  // 与 AdminModelPatchIn 别名对齐：优先发 camelCase（isAvailable），避免仅认 alias 的校验丢字段
  if (data.isAvailable !== undefined) body.isAvailable = data.isAvailable;
  if (data.sortOrder !== undefined) body.sortOrder = data.sortOrder;
  if (data.displayName !== undefined) body.displayName = data.displayName;
  if (data.provider !== undefined) body.provider = data.provider;
  if (data.modelType !== undefined) body.modelType = data.modelType;
  if (data.category !== undefined) body.category = data.category;
  if (data.description !== undefined) body.description = data.description;
  if (data.coverUrl !== undefined) body.coverUrl = data.coverUrl;
  if (data.parameters !== undefined) body.parameters = data.parameters;
  if (data.generationPresets !== undefined) body.generationPresets = data.generationPresets;
  if (data.upstreamModel !== undefined) body.upstreamModel = data.upstreamModel;
  if (data.capabilities !== undefined) body.capabilities = data.capabilities;
  if (data.implementation !== undefined) body.implementation = data.implementation;
  if (data.channels !== undefined) body.channels = data.channels;
  return apiFetch<AdminModel>(`/api/v1/admin/models/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function createAdminModel(data: {
  name: string;
  displayName: string;
  provider: string;
  modelType?: string;
  category: string;
  description?: string;
  sortOrder?: number;
  isAvailable?: boolean;
  coverUrl?: string;
  parameters?: Record<string, unknown>;
  upstreamModel?: string;
  capabilities?: string[];
  implementation?: string;
  channels?: Array<Record<string, unknown>>;
}) {
  return apiFetch<AdminModel>("/api/v1/admin/models", {
    method: "POST",
    body: JSON.stringify({
      name: data.name,
      display_name: data.displayName,
      provider: data.provider,
      model_type: data.modelType ?? "checkpoint",
      category: data.category,
      description: data.description,
      sort_order: data.sortOrder ?? 0,
      is_available: data.isAvailable ?? false,
      cover_url: data.coverUrl,
      parameters: data.parameters,
      upstream_model: data.upstreamModel,
      capabilities: data.capabilities,
      implementation: data.implementation ?? "reserved",
      channels: data.channels,
    }),
  });
}

export function deleteAdminModel(modelId: string) {
  return apiFetch<AdminModel>(`/api/v1/admin/models/${modelId}`, { method: "DELETE" });
}

export function testAdminModel(modelId: string) {
  return apiFetch<import("@/types/admin").AdminModelTestResult>(
    `/api/v1/admin/models/${modelId}/test`,
    { method: "POST" }
  );
}

export function resetAdminModelPresets(modelId: string) {
  return apiFetch<AdminModel>(`/api/v1/admin/models/${modelId}/presets/reset`, {
    method: "POST",
  });
}

export function listAdminModelProviders() {
  return apiFetch<import("@/types/admin").AdminModelProvider[]>("/api/v1/admin/model-providers");
}

export function listAdminProviderReferencedModels(providerCode: string) {
  return apiFetch<import("@/types/admin").AdminProviderReferencedModel[]>(
    `/api/v1/admin/model-providers/${encodeURIComponent(providerCode)}/models`
  );
}

export function patchAdminModelProvider(
  code: string,
  data: { defaultApiBase?: string; isEnabled?: boolean }
) {
  const body: Record<string, unknown> = {};
  if (data.defaultApiBase !== undefined) body.default_api_base = data.defaultApiBase;
  if (data.isEnabled !== undefined) body.is_enabled = data.isEnabled;
  return apiFetch<import("@/types/admin").AdminModelProvider>(
    `/api/v1/admin/model-providers/${encodeURIComponent(code)}`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
}

export function listAdminProviderCredentials(providerCode: string) {
  return apiFetch<import("@/types/admin").AdminProviderCredential[]>(
    `/api/v1/admin/model-providers/${encodeURIComponent(providerCode)}/credentials`
  );
}

export function putAdminProviderCredential(
  providerCode: string,
  profileKey: string,
  data: { apiKey?: string; apiBase?: string; endpointId?: string }
) {
  const body: Record<string, unknown> = {};
  if (data.apiKey !== undefined && data.apiKey.trim()) body.api_key = data.apiKey.trim();
  if (data.apiBase !== undefined) body.api_base = data.apiBase;
  if (data.endpointId !== undefined) body.endpoint_id = data.endpointId;
  return apiFetch<import("@/types/admin").AdminProviderCredential>(
    `/api/v1/admin/model-providers/${encodeURIComponent(providerCode)}/credentials/${encodeURIComponent(profileKey)}`,
    {
      method: "PUT",
      body: JSON.stringify(body),
    }
  );
}

export function patchAdminProviderCredential(
  providerCode: string,
  profileKey: string,
  data: { apiKey?: string; apiBase?: string; endpointId?: string }
) {
  const body: Record<string, unknown> = {};
  if (data.apiKey !== undefined && data.apiKey.trim()) body.api_key = data.apiKey.trim();
  if (data.apiBase !== undefined) body.api_base = data.apiBase;
  if (data.endpointId !== undefined) body.endpoint_id = data.endpointId;
  return apiFetch<import("@/types/admin").AdminProviderCredential>(
    `/api/v1/admin/model-providers/${encodeURIComponent(providerCode)}/credentials/${encodeURIComponent(profileKey)}`,
    {
      method: "PATCH",
      body: JSON.stringify(body),
    }
  );
}

export function testAdminProviderCredential(providerCode: string, profileKey: string) {
  return apiFetch<import("@/types/admin").AdminProviderCredentialTestResult>(
    `/api/v1/admin/model-providers/${encodeURIComponent(providerCode)}/credentials/${encodeURIComponent(profileKey)}/test`,
    { method: "POST" }
  );
}

export function listAdminProjects(params?: { search?: string; page?: number; pageSize?: number }) {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.pageSize) qs.set("page_size", String(params.pageSize));
  const query = qs.toString();
  return apiFetch<AdminProjectList>(`/api/v1/admin/projects${query ? `?${query}` : ""}`);
}

export function getAdminProjectDetail(projectId: string) {
  return apiFetch<AdminProjectDetail>(`/api/v1/admin/projects/${projectId}`);
}

export function listAdminUsers(params?: {
  search?: string;
  role?: string;
  isActive?: boolean;
  page?: number;
  pageSize?: number;
  sort?: "created_at" | "last_login_at" | "compute_power";
  order?: "asc" | "desc";
}) {
  const qs = new URLSearchParams();
  if (params?.search) qs.set("search", params.search);
  if (params?.role) qs.set("role", params.role);
  if (params?.isActive !== undefined) qs.set("is_active", String(params.isActive));
  if (params?.page) qs.set("page", String(params.page));
  if (params?.pageSize) qs.set("page_size", String(params.pageSize));
  if (params?.sort) qs.set("sort", params.sort);
  if (params?.order) qs.set("order", params.order);
  const query = qs.toString();
  return apiFetch<AdminUserList>(`/api/v1/admin/users${query ? `?${query}` : ""}`);
}

export function getAdminUserDetail(userId: string) {
  return apiFetch<AdminUserDetail>(`/api/v1/admin/users/${userId}`);
}

export function patchAdminUser(
  userId: string,
  data: { displayName?: string; role?: string; isActive?: boolean }
) {
  const body: Record<string, unknown> = {};
  if (data.displayName !== undefined) body.display_name = data.displayName;
  if (data.role !== undefined) body.role = data.role;
  if (data.isActive !== undefined) body.is_active = data.isActive;
  return apiFetch<AdminUserDetail>(`/api/v1/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function revokeAdminUserSessions(userId: string) {
  return apiFetch<{ revoked: number }>(`/api/v1/admin/users/${userId}/revoke-sessions`, {
    method: "POST",
  });
}

export function getAdminAuthSettings() {
  return apiFetch<AdminAuthSettings>("/api/v1/admin/settings/auth");
}

export function patchAdminAuthSettings(registrationEnabled: boolean) {
  return apiFetch<AdminAuthSettings>("/api/v1/admin/settings/auth", {
    method: "PATCH",
    body: JSON.stringify({ registration_enabled: registrationEnabled }),
  });
}

export function getAdminRegisterBonusSettings() {
  return apiFetch<import("@/types/admin").AdminRegisterBonusSettings>(
    "/api/v1/admin/register-bonus"
  );
}

export function putAdminRegisterBonusSettings(payload: { enabled: boolean; amount: number }) {
  return apiFetch<import("@/types/admin").AdminRegisterBonusSettings>(
    "/api/v1/admin/register-bonus",
    {
      method: "PUT",
      body: JSON.stringify(payload),
    }
  );
}

export function listAdminRegisterBonusGrants(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
}) {
  const q = new URLSearchParams();
  if (params?.page) q.set("page", String(params.page));
  if (params?.pageSize) q.set("page_size", String(params.pageSize));
  if (params?.search) q.set("search", params.search);
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return apiFetch<import("@/types/admin").AdminRegisterBonusGrantList>(
    `/api/v1/admin/register-bonus/grants${suffix}`
  );
}

export function getAdminHomepageSettings() {
  return apiFetch<AdminHomepageSettings>("/api/v1/admin/content/homepage");
}

export async function uploadAdminAuthGridImage(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token = typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/content/homepage/auth-grid-image`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      typeof body?.message === "string"
        ? body.message
        : typeof body?.detail === "string"
          ? body.detail
          : "上传失败";
    throw new Error(message);
  }
  return body as { item: import("@/types/admin").AuthGridImageItem; authGridImages: import("@/types/admin").AuthGridImageItem[] };
}

export function deleteAdminAuthGridImage(imageId: string) {
  return apiFetch<AdminHomepageSettings>(
    `/api/v1/admin/content/homepage/auth-grid-images/${encodeURIComponent(imageId)}`,
    { method: "DELETE" }
  );
}

export function clearAdminAuthGridImages() {
  return apiFetch<AdminHomepageSettings>("/api/v1/admin/content/homepage/auth-grid-images/clear", {
    method: "POST",
  });
}

/** 一键重压已上传的登录背景大图为 WebP 小图 */
export function recompressAdminAuthGridImages() {
  return apiFetch<
    AdminHomepageSettings & {
      rewritten: number;
      skipped: number;
      failed: number;
      total: number;
    }
  >("/api/v1/admin/content/homepage/auth-grid-images/recompress", {
    method: "POST",
  });
}

/** 上传登录弹窗左半边图片 */
export async function uploadAdminLoginModalImage(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token = typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/content/homepage/login-modal-image`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      typeof body?.message === "string"
        ? body.message
        : typeof body?.detail === "string"
          ? body.detail
          : "上传失败";
    throw new Error(message);
  }
  return body as AdminHomepageSettings;
}

export function deleteAdminLoginModalImage() {
  return apiFetch<AdminHomepageSettings>("/api/v1/admin/content/homepage/login-modal-image", {
    method: "DELETE",
  });
}

export type AdminLegalDocType = "user-agreement" | "privacy-policy";

/** 上传用户协议或隐私政策（.md / .docx） */
export async function uploadAdminLegalDocument(docType: AdminLegalDocType, file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token = typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(
    `${API_BASE}/api/v1/admin/content/homepage/legal/${encodeURIComponent(docType)}`,
    {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      body: fd,
    },
  );
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      typeof body?.message === "string"
        ? body.message
        : typeof body?.detail === "string"
          ? body.detail
          : "上传失败";
    throw new Error(message);
  }
  return body as AdminHomepageSettings;
}

export function deleteAdminLegalDocument(docType: AdminLegalDocType) {
  return apiFetch<AdminHomepageSettings>(
    `/api/v1/admin/content/homepage/legal/${encodeURIComponent(docType)}`,
    { method: "DELETE" },
  );
}

/** 读取发现页运营配置 */
export function getAdminDiscoverPage() {
  return apiFetch<import("@/types/admin").DiscoverPageSettings>("/api/v1/admin/content/discover");
}

/** 整表保存发现页运营配置 */
export function putAdminDiscoverPage(payload: import("@/types/admin").DiscoverPageSettings) {
  return apiFetch<import("@/types/admin").DiscoverPageSettings>("/api/v1/admin/content/discover", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

async function postAdminDiscoverMediaUpload<T>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token =
    typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const raw = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      typeof raw?.message === "string"
        ? raw.message
        : typeof raw?.detail === "string"
          ? raw.detail
          : "上传失败";
    throw new Error(message);
  }
  const body =
    raw && typeof raw === "object" && raw.content && typeof raw.content === "object"
      ? raw.content
      : raw;
  return body as T;
}

/** 上传发现页运营图片到平台 OSS 目录。 */
export async function uploadAdminDiscoverImage(file: File) {
  return postAdminDiscoverMediaUpload<{ id: string; ossKey: string; imageUrl: string }>(
    "/api/v1/admin/content/discover/image",
    file
  );
}

/** 上传发现页 Hero 背景视频到平台 OSS 目录。 */
export async function uploadAdminDiscoverVideo(file: File) {
  return postAdminDiscoverMediaUpload<{ id: string; ossKey: string; videoUrl: string }>(
    "/api/v1/admin/content/discover/video",
    file
  );
}

/** 平台素材库条目（管理端） */
export type AdminMaterialLibraryItem = {
  id: string;
  category: "style" | "effect" | "character" | "prompt";
  title: string;
  mediaType: "image" | "video";
  ossKey: string;
  mediaUrl: string;
  /** 提示词库正文 */
  promptText?: string;
  promptCategoryId?: string | null;
  promptCategoryName?: string;
  sortOrder: number;
  isActive: boolean;
  createdBy?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type AdminPromptLibraryCategory = {
  id: string;
  name: string;
  sortOrder: number;
  isActive: boolean;
  createdAt?: string | null;
  updatedAt?: string | null;
};

/** 列出平台素材库（可含下架） */
export function listAdminMaterialLibrary(params?: {
  category?: "style" | "effect" | "character" | "prompt";
  includeInactive?: boolean;
}) {
  const q = new URLSearchParams();
  if (params?.category) q.set("category", params.category);
  if (params?.includeInactive) q.set("includeInactive", "true");
  const qs = q.toString();
  return apiFetch<{ items: AdminMaterialLibraryItem[]; promptCategories?: AdminPromptLibraryCategory[] }>(
    `/api/v1/admin/content/material-library${qs ? `?${qs}` : ""}`
  );
}

/** 列出提示词库分类 */
export function listAdminPromptLibraryCategories(includeInactive = true) {
  const q = includeInactive ? "?includeInactive=true" : "?includeInactive=false";
  return apiFetch<{ items: AdminPromptLibraryCategory[] }>(
    `/api/v1/admin/content/material-library/prompt-categories${q}`
  );
}

/** 新建提示词库分类 */
export function createAdminPromptLibraryCategory(payload: { name: string; sortOrder?: number }) {
  return apiFetch<AdminPromptLibraryCategory>(
    "/api/v1/admin/content/material-library/prompt-categories",
    { method: "POST", body: JSON.stringify(payload) }
  );
}

/** 更新提示词库分类 */
export function updateAdminPromptLibraryCategory(
  categoryId: string,
  payload: { name?: string; sortOrder?: number; isActive?: boolean }
) {
  return apiFetch<AdminPromptLibraryCategory>(
    `/api/v1/admin/content/material-library/prompt-categories/${encodeURIComponent(categoryId)}`,
    { method: "PATCH", body: JSON.stringify(payload) }
  );
}

/** 删除提示词库分类（条目变为未分类） */
export function deleteAdminPromptLibraryCategory(categoryId: string) {
  return apiFetch<{ deleted: boolean }>(
    `/api/v1/admin/content/material-library/prompt-categories/${encodeURIComponent(categoryId)}`,
    { method: "DELETE" }
  );
}

/** 管理员上传并创建素材库条目 */
export async function createAdminMaterialLibraryItem(payload: {
  category: "style" | "effect" | "character" | "prompt";
  title: string;
  file: File;
  /** 提示词库必填 */
  promptText?: string;
  promptCategoryId?: string;
  sortOrder?: number;
}) {
  const fd = new FormData();
  fd.append("category", payload.category);
  fd.append("title", payload.title);
  fd.append("sort_order", String(payload.sortOrder ?? 0));
  if (payload.promptText != null) {
    fd.append("prompt_text", payload.promptText);
  }
  if (payload.promptCategoryId) {
    fd.append("prompt_category_id", payload.promptCategoryId);
  }
  fd.append("file", payload.file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token =
    typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/content/material-library`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const raw = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      typeof raw?.message === "string"
        ? raw.message
        : typeof raw?.detail === "string"
          ? raw.detail
          : "上传失败";
    throw new Error(message);
  }
  const body =
    raw && typeof raw === "object" && raw.content && typeof raw.content === "object"
      ? raw.content
      : raw;
  return body as AdminMaterialLibraryItem;
}

/** 更新素材库标题 / 排序 / 上下架 / 提示词 */
export function updateAdminMaterialLibraryItem(
  itemId: string,
  payload: {
    title?: string;
    sortOrder?: number;
    isActive?: boolean;
    promptText?: string;
    promptCategoryId?: string | null;
  }
) {
  return apiFetch<AdminMaterialLibraryItem>(
    `/api/v1/admin/content/material-library/${encodeURIComponent(itemId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    }
  );
}

/** 删除素材库条目 */
export function deleteAdminMaterialLibraryItem(itemId: string) {
  return apiFetch<{ deleted: boolean }>(
    `/api/v1/admin/content/material-library/${encodeURIComponent(itemId)}`,
    { method: "DELETE" }
  );
}

/** 读取首页 Footer 运营配置 */
export function getAdminSiteFooter() {
  return apiFetch<import("@/types/admin").SiteFooterSettings>("/api/v1/admin/content/footer");
}

/** 整表保存首页 Footer 运营配置 */
export function putAdminSiteFooter(payload: import("@/types/admin").SiteFooterSettings) {
  return apiFetch<import("@/types/admin").SiteFooterSettings>("/api/v1/admin/content/footer", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

/** 上传 Footer 二维码等到平台 OSS 目录。 */
export async function uploadAdminFooterImage(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token =
    typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/content/footer/image`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const raw = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      typeof raw?.message === "string"
        ? raw.message
        : typeof raw?.detail === "string"
          ? raw.detail
          : "上传失败";
    throw new Error(message);
  }
  const body =
    raw && typeof raw === "object" && raw.content && typeof raw.content === "object"
      ? raw.content
      : raw;
  return body as { id: string; ossKey: string; imageUrl: string };
}

export function listAdminUserAuthEvents(userId: string, page = 1, pageSize = 20) {
  return apiFetch<AdminAuthEventList>(
    `/api/v1/admin/users/${userId}/auth-events?page=${page}&page_size=${pageSize}`
  );
}

export function clearAdminUserAuthRisk(userId: string) {
  return apiFetch<{ ok: boolean; phone: string }>(
    `/api/v1/admin/users/${userId}/clear-auth-risk`,
    { method: "POST" }
  );
}

export function listAdminUserCreditHistory(userId: string, page = 1, pageSize = 10) {
  return apiFetch<AdminCreditTransactionList>(
    `/api/v1/admin/users/${userId}/credit-history?page=${page}&page_size=${pageSize}`
  );
}

export function listAdminCreditTransactions(params?: {
  page?: number;
  pageSize?: number;
  userId?: string;
  source?: string;
  createdFrom?: string;
  createdTo?: string;
}) {
  const search = new URLSearchParams();
  search.set("page", String(params?.page ?? 1));
  search.set("page_size", String(params?.pageSize ?? 20));
  if (params?.userId?.trim()) search.set("userId", params.userId.trim());
  if (params?.source?.trim()) search.set("source", params.source.trim());
  if (params?.createdFrom) search.set("createdFrom", params.createdFrom);
  if (params?.createdTo) search.set("createdTo", params.createdTo);
  return apiFetch<AdminCreditTransactionList>(
    `/api/v1/admin/credits/transactions?${search.toString()}`
  );
}

export function listAdminRechargeOrders(params?: {
  page?: number;
  pageSize?: number;
  userId?: string;
  orderType?: string;
  status?: string;
  paymentChannel?: string;
  outTradeNo?: string;
  createdFrom?: string;
  createdTo?: string;
}) {
  const search = new URLSearchParams();
  search.set("page", String(params?.page ?? 1));
  search.set("page_size", String(params?.pageSize ?? 20));
  if (params?.userId?.trim()) search.set("userId", params.userId.trim());
  if (params?.orderType?.trim()) search.set("orderType", params.orderType.trim());
  if (params?.status?.trim()) search.set("status", params.status.trim());
  if (params?.paymentChannel?.trim()) search.set("paymentChannel", params.paymentChannel.trim());
  if (params?.outTradeNo?.trim()) search.set("outTradeNo", params.outTradeNo.trim());
  if (params?.createdFrom) search.set("createdFrom", params.createdFrom);
  if (params?.createdTo) search.set("createdTo", params.createdTo);
  return apiFetch<AdminRechargeOrderList>(
    `/api/v1/admin/recharge-orders?${search.toString()}`
  );
}

export function getAdminRechargeTiers() {
  return apiFetch<AdminRechargeTiersConfig>("/api/v1/admin/recharge-tiers");
}

export function putAdminRechargeTiers(items: AdminRechargeTierItem[]) {
  return apiFetch<AdminRechargeTiersConfig>("/api/v1/admin/recharge-tiers", {
    method: "PUT",
    body: JSON.stringify({ items }),
  });
}

export type { AdminRechargeTierItem } from "@/types/admin";

/** 敏感词列表项（MySQL 权威；启用词同步到 Redis 单 key） */
export type AdminSensitiveWordItem = {
  id: string;
  word: string;
  wordNorm: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
};

export type AdminSensitiveWordList = {
  items: AdminSensitiveWordItem[];
  total: number;
  page: number;
  pageSize: number;
};

export function listAdminSensitiveWords(params?: {
  q?: string;
  enabled?: boolean;
  page?: number;
  pageSize?: number;
}) {
  const search = new URLSearchParams();
  search.set("page", String(params?.page ?? 1));
  search.set("pageSize", String(params?.pageSize ?? 50));
  if (params?.q?.trim()) search.set("q", params.q.trim());
  if (typeof params?.enabled === "boolean") search.set("enabled", String(params.enabled));
  return apiFetch<AdminSensitiveWordList>(`/api/v1/admin/sensitive-words?${search.toString()}`);
}

export function createAdminSensitiveWord(word: string, enabled = true) {
  return apiFetch<AdminSensitiveWordItem>("/api/v1/admin/sensitive-words", {
    method: "POST",
    body: JSON.stringify({ word, enabled }),
  });
}

export function updateAdminSensitiveWord(
  id: string,
  body: { word?: string; enabled?: boolean }
) {
  return apiFetch<AdminSensitiveWordItem>(`/api/v1/admin/sensitive-words/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteAdminSensitiveWord(id: string) {
  return apiFetch<{ ok: boolean }>(`/api/v1/admin/sensitive-words/${id}`, {
    method: "DELETE",
  });
}

export function importAdminSensitiveWords(payload: { words?: string[]; text?: string }) {
  return apiFetch<{ added: number; skipped: number }>("/api/v1/admin/sensitive-words/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function testAdminSensitiveText(text: string) {
  return apiFetch<{ ok: boolean; matchedWords: string[] }>("/api/v1/admin/sensitive-words/test", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function listAdminGenerationLogs(params?: {
  page?: number;
  pageSize?: number;
  jobId?: string;
  projectId?: string;
  actorUserId?: string;
  model?: string;
  category?: string;
  phase?: string;
  submitSource?: string;
  outcome?: string;
  createdFrom?: string;
  createdTo?: string;
}) {
  const search = new URLSearchParams();
  search.set("page", String(params?.page ?? 1));
  search.set("page_size", String(params?.pageSize ?? 20));
  if (params?.jobId?.trim()) search.set("jobId", params.jobId.trim());
  if (params?.projectId?.trim()) search.set("projectId", params.projectId.trim());
  if (params?.actorUserId?.trim()) search.set("actorUserId", params.actorUserId.trim());
  if (params?.model?.trim()) search.set("model", params.model.trim());
  if (params?.category?.trim()) search.set("category", params.category.trim());
  if (params?.phase?.trim()) search.set("phase", params.phase.trim());
  if (params?.submitSource?.trim()) search.set("submitSource", params.submitSource.trim());
  if (params?.outcome?.trim()) search.set("outcome", params.outcome.trim());
  if (params?.createdFrom) search.set("createdFrom", params.createdFrom);
  if (params?.createdTo) search.set("createdTo", params.createdTo);
  return apiFetch<AdminGenerationLogList>(`/api/v1/admin/generation-logs?${search.toString()}`);
}

export function purgeAdminGenerationLogs(before: string) {
  const search = new URLSearchParams({ before });
  return apiFetch<{ deleted: number; before: string }>(
    `/api/v1/admin/generation-logs?${search.toString()}`,
    { method: "DELETE" }
  );
}

export function getAdminUserCredits(userId: string) {
  return apiFetch<AdminUserCredits>(`/api/v1/admin/credits/users/${userId}`);
}

export function adjustAdminUserCredits(userId: string, delta: number, reason = "") {
  return apiFetch<AdminUserCredits>(`/api/v1/admin/credits/users/${userId}/adjust`, {
    method: "POST",
    body: JSON.stringify({ delta, reason }),
  });
}

export function getAdminPromptDocument() {
  return apiFetch<{ document: string; help: string }>("/api/v1/admin/prompt-templates/document");
}

export function getAdminPromptConfig() {
  return apiFetch<{ version: number; tools: Record<string, PromptToolConfig> }>(
    "/api/v1/admin/prompt-config"
  );
}

export function getAdminPromptTool(toolId: string) {
  return apiFetch<{ tool: string; config: PromptToolConfig }>(
    `/api/v1/admin/prompt-config/tools/${encodeURIComponent(toolId)}`
  );
}

export function saveAdminPromptTool(toolId: string, config: PromptToolConfig) {
  return apiFetch<{ tool: string; config: PromptToolConfig }>(
    `/api/v1/admin/prompt-config/tools/${encodeURIComponent(toolId)}`,
    { method: "PUT", body: JSON.stringify({ config }) }
  );
}

export function resetAdminPromptTool(toolId: string) {
  return apiFetch<{ tool: string; config: PromptToolConfig }>(
    `/api/v1/admin/prompt-config/tools/${encodeURIComponent(toolId)}/reset`,
    { method: "POST" }
  );
}

export function previewAdminPromptTool(
  tool: string,
  runtime: Record<string, unknown>,
  config?: Record<string, unknown>
) {
  return apiFetch<{ tool: string; prompt: string; errors: string[] }>(
    "/api/v1/admin/prompt-config/preview",
    {
      method: "POST",
      body: JSON.stringify({ tool, runtime, ...(config ? { config } : {}) }),
    }
  );
}

export async function uploadAdminVisualStyleImage(styleId: string, file: File) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("styleId", styleId);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token = typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/prompt-config/visual-style-image`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      typeof body?.message === "string"
        ? body.message
        : typeof body?.detail === "string"
          ? body.detail
          : "上传失败";
    throw new Error(message);
  }
  // 兼容 { imageUrl } 与 SEC-042 { code, content: { imageUrl } }
  const payload =
    body && typeof body === "object" && body.content && typeof body.content === "object"
      ? body.content
      : body;
  const imageUrl = String(payload?.imageUrl ?? payload?.image_url ?? "").trim();
  const ossKey = String(payload?.ossKey ?? payload?.oss_key ?? "").trim();
  if (!imageUrl) {
    throw new Error("上传成功但未返回图片地址，请重试");
  }
  return { imageUrl, ossKey };
}

export function saveAdminPromptDocument(document: string) {
  return apiFetch<{ document: string; help: string }>("/api/v1/admin/prompt-templates/document", {
    method: "PUT",
    body: JSON.stringify({ document }),
  });
}

export function resetAdminPromptDocument() {
  return apiFetch<{ document: string; help: string }>(
    "/api/v1/admin/prompt-templates/document/reset",
    { method: "POST" }
  );
}

export function listAdminPromptTemplates(tool: string) {
  return apiFetch<PromptTemplateList>(
    `/api/v1/admin/prompt-templates?tool=${encodeURIComponent(tool)}`
  );
}

export function createAdminPromptTemplate(data: {
  tool: string;
  category: string;
  key: string;
  label?: string;
  content?: string;
  enabled?: boolean;
  sortOrder?: number;
}) {
  return apiFetch<PromptTemplate>("/api/v1/admin/prompt-templates", {
    method: "POST",
    body: JSON.stringify({
      tool: data.tool,
      category: data.category,
      key: data.key,
      label: data.label ?? "",
      content: data.content ?? "",
      enabled: data.enabled ?? true,
      sort_order: data.sortOrder ?? 0,
    }),
  });
}

export function patchAdminPromptTemplate(
  templateId: string,
  data: {
    tool?: string;
    category?: string;
    key?: string;
    label?: string;
    content?: string;
    enabled?: boolean;
    sortOrder?: number;
  }
) {
  const body: Record<string, unknown> = {};
  if (data.tool !== undefined) body.tool = data.tool;
  if (data.category !== undefined) body.category = data.category;
  if (data.key !== undefined) body.key = data.key;
  if (data.label !== undefined) body.label = data.label;
  if (data.content !== undefined) body.content = data.content;
  if (data.enabled !== undefined) body.enabled = data.enabled;
  if (data.sortOrder !== undefined) body.sort_order = data.sortOrder;
  return apiFetch<PromptTemplate>(`/api/v1/admin/prompt-templates/${templateId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteAdminPromptTemplate(templateId: string) {
  return apiFetch<void>(`/api/v1/admin/prompt-templates/${templateId}`, {
    method: "DELETE",
  });
}

export function resetAdminPromptTemplates(tool: string) {
  return apiFetch<PromptTemplateList>(
    `/api/v1/admin/prompt-templates/reset?tool=${encodeURIComponent(tool)}`,
    { method: "POST" }
  );
}

export interface AdminPricingItem {
  modelId: string;
  name: string;
  displayName: string;
  category: string;
  isAvailable: boolean;
  pricingVersion: number;
  baseCost: number;
  mode: string;
  minCost: number;
  primaryGroupId?: string;
  options: Record<string, Record<string, number>>;
  /** 多模态有参考视频时的每秒算力（与 options 同结构） */
  optionsWithVideoReference?: Record<string, Record<string, number>>;
  refVideoGroupId?: string | null;
  matrixGroupIds?: string[];
  matrix?: Record<string, Record<string, number>>;
  videoMode?: string;
  presetGroups: Array<{
    id: string;
    label: string;
    items: Array<{ id: string; label: string }>;
  }>;
  defaultOptionSnapshot?: Record<string, string>;
  videoYuanPerSecond?: number | null;
  yuanPerCall?: number | null;
  /** MiniMax-H3：输入参考视频按同档秒价另计 */
  billInputVideoSeconds?: boolean;
  /** 参考图超额：前 freeCount 张免费，超出 costPerImage 算力/张 */
  extraImageBilling?: { freeCount: number; costPerImage: number } | null;
}

export function listAdminPricing(params?: { category?: string; search?: string }) {
  const search = new URLSearchParams();
  if (params?.category) search.set("category", params.category);
  if (params?.search) search.set("search", params.search);
  const qs = search.toString();
  return apiFetch<AdminPricingItem[]>(`/api/v1/admin/pricing${qs ? `?${qs}` : ""}`);
}

export function patchAdminPricing(
  modelId: string,
  data: {
    baseCost?: number;
    mode?: string;
    minCost?: number;
    primaryGroupId?: string;
    options?: Record<string, Record<string, number>>;
    optionsWithVideoReference?: Record<string, Record<string, number>>;
    matrix?: Record<string, Record<string, number>>;
    videoYuanPerSecond?: number;
    yuanPerCall?: number;
    billInputVideoSeconds?: boolean;
    extraImageBilling?: { freeCount: number; costPerImage: number };
  }
) {
  const body: Record<string, unknown> = {};
  if (data.baseCost !== undefined) body.base_cost = data.baseCost;
  if (data.mode !== undefined) body.mode = data.mode;
  if (data.minCost !== undefined) body.min_cost = data.minCost;
  if (data.primaryGroupId !== undefined) body.primary_group_id = data.primaryGroupId;
  if (data.options !== undefined) body.options = data.options;
  if (data.optionsWithVideoReference !== undefined) {
    body.optionsWithVideoReference = data.optionsWithVideoReference;
  }
  if (data.matrix !== undefined) body.matrix = data.matrix;
  if (data.videoYuanPerSecond !== undefined) body.video_yuan_per_second = data.videoYuanPerSecond;
  if (data.yuanPerCall !== undefined) body.yuan_per_call = data.yuanPerCall;
  if (data.billInputVideoSeconds !== undefined) {
    body.billInputVideoSeconds = data.billInputVideoSeconds;
  }
  if (data.extraImageBilling !== undefined) {
    body.extraImageBilling = data.extraImageBilling;
  }
  return apiFetch<AdminModel>(`/api/v1/admin/pricing/${modelId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function resetAdminPricing(modelId: string) {
  return apiFetch<AdminModel>(`/api/v1/admin/pricing/${modelId}/reset`, { method: "POST" });
}

export interface AdminPricingQuoteDraft {
  generationOptions?: Record<string, string>;
  baseCost?: number;
  mode?: string;
  minCost?: number;
  primaryGroupId?: string;
  options?: Record<string, Record<string, number>>;
}

export function previewAdminPricingQuote(modelId: string, draft: AdminPricingQuoteDraft = {}) {
  return apiFetch<{
    total: number;
    base: number;
    pricingVersion: number;
    optionSnapshot: Record<string, string>;
    breakdown: Array<{ groupId: string; itemId: string; label: string; cost: number }>;
    creditsEnabled: boolean;
  }>(`/api/v1/admin/pricing/${modelId}/quote`, {
    method: "POST",
    body: JSON.stringify({
      generationOptions: draft.generationOptions ?? {},
      baseCost: draft.baseCost,
      mode: draft.mode,
      minCost: draft.minCost,
      primaryGroupId: draft.primaryGroupId,
      options: draft.options,
    }),
  });
}

export interface AdminCanvasToolPricingItem {
  toolId: string;
  label: string;
  group?: string;
  billingMode?: string;
  creditCost: number;
  creditsPerSecond?: number | null;
  /** model=跟随功能模型切换主模型；platform=本地固定价可编辑 */
  priceSource?: string;
  primaryModel?: string | null;
  /** 主模型展示名（定价页对照） */
  primaryModelDisplayName?: string | null;
  editable?: boolean;
}

export interface AdminCanvasToolPricing {
  version: number;
  items: AdminCanvasToolPricingItem[];
}

/** 管理端：读取画布工具算力（固定价 / 视频每秒） */
export function getAdminCanvasToolPricing() {
  return apiFetch<AdminCanvasToolPricing>("/api/v1/admin/pricing/canvas-tools");
}

/** 管理端：保存画布工具算力 */
export function putAdminCanvasToolPricing(tools: Record<string, number>) {
  return apiFetch<AdminCanvasToolPricing>("/api/v1/admin/pricing/canvas-tools", {
    method: "PUT",
    body: JSON.stringify({ tools }),
  });
}

export interface AdminCanvasToolModelItem {
  toolId: string;
  label: string;
  group?: string;
  /** 模型目录类别：image | text（决定可选模型范围） */
  category: string;
  /** 主模型（后台配置为权威） */
  primary: string;
  /** 副模型（主模型上游失败时自动兜底），空表示不配置 */
  secondary: string;
}

export interface AdminCanvasToolModels {
  version: number;
  items: AdminCanvasToolModelItem[];
}

/** 管理端：读取各画布/分镜功能的主副模型切换配置 */
export function getAdminCanvasToolModels() {
  return apiFetch<AdminCanvasToolModels>("/api/v1/admin/pricing/canvas-tool-models");
}

/** 管理端：保存各画布/分镜功能的主副模型（校验模型有效并递增 version） */
export function putAdminCanvasToolModels(
  tools: Record<string, { primary: string; secondary: string }>
) {
  return apiFetch<AdminCanvasToolModels>("/api/v1/admin/pricing/canvas-tool-models", {
    method: "PUT",
    body: JSON.stringify({ tools }),
  });
}

/** 管理端：AI 操控模型白名单 + 一次对话算力 */
export interface AdminAgentControllerModel {
  id: string;
  label: string;
  configured: boolean;
  supportsFiles: boolean;
  enabled: boolean;
  provider?: string;
}

export interface AdminAgentSkillPricing {
  version: number;
  conversationTurn: number;
  sessionStart: number;
  /** 创建 Skill「按类型 AI 填充」单次算力 */
  skillAiFill: number;
  skills: Record<string, number>;
  controllerModels: string[];
  catalog: AdminAgentControllerModel[];
}

export function getAdminAgentSkillPricing() {
  return apiFetch<AdminAgentSkillPricing>("/api/v1/admin/pricing/agent-control");
}

export function putAdminAgentSkillPricing(body: {
  conversationTurn: number;
  skillAiFill?: number;
  controllerModels: string[];
  skills?: Record<string, number>;
}) {
  return apiFetch<AdminAgentSkillPricing>("/api/v1/admin/pricing/agent-control", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export interface AdminCreditPendingJob {
  id: number;
  status: string;
  creditStatus?: string;
  creditCost: number;
  model?: string;
  nodeId?: string;
  projectId?: string;
  createdAt: string;
  completedAt?: string;
}

export interface AdminCreditReconcileResult {
  committed: number;
  released: number;
  failedStale: number;
}

export function listAdminCreditPending(limit = 50) {
  return apiFetch<AdminCreditPendingJob[]>(
    `/api/v1/admin/credits/pending?limit=${limit}`
  );
}

export function runAdminCreditReconcile() {
  return apiFetch<AdminCreditReconcileResult>("/api/v1/admin/credits/reconcile", {
    method: "POST",
  });
}

export interface AdminCreditActivityClaimRules {
  registeredFrom?: string | null;
  registeredTo?: string | null;
  /** 累计实付金额（分） */
  minRechargeFen?: number | null;
  /** 累计入账算力点 */
  minRechargeCredits?: number | null;
  requireActiveMember?: boolean | null;
}

export interface AdminCreditActivity {
  id: string;
  title: string;
  description: string;
  coverUrl: string;
  creditType: string;
  amount: number;
  modelName?: string | null;
  validDays: number;
  startsAt?: string | null;
  endsAt?: string | null;
  perUserLimit: number;
  totalQuota?: number | null;
  claimedCount: number;
  status: string;
  claimRules?: AdminCreditActivityClaimRules | null;
  claimRuleSummary?: string[];
}

export function listAdminCreditActivities() {
  return apiFetch<{ items: AdminCreditActivity[] }>("/api/v1/admin/credit-activities");
}

export function createAdminCreditActivity(data: {
  title: string;
  description?: string;
  coverUrl?: string;
  creditType: string;
  amount: number;
  modelName?: string;
  validDays: number;
  startsAt: string;
  endsAt: string;
  perUserLimit?: number;
  totalQuota?: number | null;
  status?: string;
  claimRules?: AdminCreditActivityClaimRules | null;
}) {
  return apiFetch<AdminCreditActivity>("/api/v1/admin/credit-activities", {
    method: "POST",
    body: JSON.stringify({
      title: data.title,
      description: data.description ?? "",
      coverUrl: data.coverUrl ?? "",
      creditType: data.creditType,
      amount: data.amount,
      modelName: data.modelName,
      validDays: data.validDays,
      startsAt: data.startsAt,
      endsAt: data.endsAt,
      perUserLimit: data.perUserLimit ?? 1,
      totalQuota: data.totalQuota ?? null,
      status: data.status ?? "draft",
      claimRules: data.claimRules ?? null,
    }),
  });
}

export function patchAdminCreditActivity(
  activityId: string,
  data: Partial<{
    title: string;
    description: string;
    coverUrl: string;
    creditType: string;
    amount: number;
    modelName: string | null;
    validDays: number;
    startsAt: string;
    endsAt: string;
    perUserLimit: number;
    totalQuota: number | null;
    status: string;
    claimRules: AdminCreditActivityClaimRules | null;
  }>
) {
  return apiFetch<AdminCreditActivity>(`/api/v1/admin/credit-activities/${activityId}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...(data.title !== undefined ? { title: data.title } : {}),
      ...(data.description !== undefined ? { description: data.description } : {}),
      ...(data.coverUrl !== undefined ? { coverUrl: data.coverUrl } : {}),
      ...(data.creditType !== undefined ? { creditType: data.creditType } : {}),
      ...(data.amount !== undefined ? { amount: data.amount } : {}),
      ...(data.modelName !== undefined ? { modelName: data.modelName } : {}),
      ...(data.validDays !== undefined ? { validDays: data.validDays } : {}),
      ...(data.startsAt !== undefined ? { startsAt: data.startsAt } : {}),
      ...(data.endsAt !== undefined ? { endsAt: data.endsAt } : {}),
      ...(data.perUserLimit !== undefined ? { perUserLimit: data.perUserLimit } : {}),
      ...(data.totalQuota !== undefined ? { totalQuota: data.totalQuota } : {}),
      ...(data.status !== undefined ? { status: data.status } : {}),
      ...(data.claimRules !== undefined ? { claimRules: data.claimRules } : {}),
    }),
  });
}

export async function uploadAdminCreditActivityImage(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token =
    typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/credit-activities/image`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const raw = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      typeof raw?.message === "string"
        ? raw.message
        : typeof raw?.detail === "string"
          ? raw.detail
          : "上传失败",
    );
  }
  const body =
    raw && typeof raw === "object" && raw.content && typeof raw.content === "object"
      ? raw.content
      : raw;
  return body as { id: string; ossKey: string; imageUrl: string };
}

export function deleteAdminCreditActivity(activityId: string) {
  return apiFetch<{ ok: boolean }>(`/api/v1/admin/credit-activities/${activityId}`, {
    method: "DELETE",
  });
}

/** —— 邀请活动 —— */
export interface AdminInviteCampaign {
  id: string;
  title: string;
  description: string;
  coverUrl: string;
  inviterRewardAmount: number;
  inviteeRewardAmount: number;
  rewardCreditType: string;
  rewardValidDays: number;
  inviterRewardOn: string;
  maxRewardsPerInviter?: number | null;
  totalInviteQuota?: number | null;
  remainingQuota?: number | null;
  rewardedInviteeCount?: number;
  rewardedInviterCount?: number;
  startsAt?: string | null;
  endsAt?: string | null;
  status: string;
}

export function listAdminInviteCampaigns() {
  return apiFetch<{ items: AdminInviteCampaign[] }>("/api/v1/admin/invite-campaigns");
}

export function createAdminInviteCampaign(data: {
  title: string;
  description?: string;
  coverUrl?: string;
  inviterRewardAmount: number;
  inviteeRewardAmount: number;
  rewardCreditType: string;
  rewardValidDays: number;
  inviterRewardOn: string;
  maxRewardsPerInviter?: number | null;
  totalInviteQuota?: number | null;
  startsAt: string;
  endsAt: string;
  status?: string;
}) {
  return apiFetch<AdminInviteCampaign>("/api/v1/admin/invite-campaigns", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function patchAdminInviteCampaign(
  campaignId: string,
  data: Partial<{
    title: string;
    description: string;
    coverUrl: string;
    inviterRewardAmount: number;
    inviteeRewardAmount: number;
    rewardCreditType: string;
    rewardValidDays: number;
    inviterRewardOn: string;
    maxRewardsPerInviter: number | null;
    totalInviteQuota: number | null;
    startsAt: string;
    endsAt: string;
    status: string;
  }>
) {
  return apiFetch<AdminInviteCampaign>(`/api/v1/admin/invite-campaigns/${campaignId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function uploadAdminInviteCampaignImage(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || withBasePath("/api/proxy");
  const token =
    typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  const res = await fetch(`${API_BASE}/api/v1/admin/invite-campaigns/image`, {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  const raw = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      typeof raw?.message === "string"
        ? raw.message
        : typeof raw?.detail === "string"
          ? raw.detail
          : "上传失败"
    );
  }
  const body =
    raw && typeof raw === "object" && raw.content && typeof raw.content === "object"
      ? raw.content
      : raw;
  return body as { id: string; ossKey: string; imageUrl: string; url?: string };
}

export function listAdminInviteBindings(params?: {
  inviterUserId?: string;
  limit?: number;
  offset?: number;
}) {
  const q = new URLSearchParams();
  if (params?.inviterUserId) q.set("inviterUserId", params.inviterUserId);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  return apiFetch<{
    total: number;
    items: Array<{
      id: string;
      inviteeUserId: string;
      inviterUserId: string;
      inviteCode: string;
      campaignId?: string | null;
      inviteeDisplayName: string;
      inviteePhoneMasked: string;
      inviteeRewardStatus: string;
      inviterRewardStatus: string;
      createdAt?: string | null;
    }>;
  }>(`/api/v1/admin/invite-bindings${qs ? `?${qs}` : ""}`);
}

export function reconcileAdminInviteBinding(bindingId: string) {
  return apiFetch<{ id: string; inviteeRewardStatus: string; inviterRewardStatus: string }>(
    `/api/v1/admin/invite-bindings/${bindingId}/reconcile`,
    { method: "POST" }
  );
}

export interface AdminSubscriptionPlan {
  id: string;
  code: string;
  name: string;
  description: string;
  monthlyCredits: number;
  storageGb: number;
  periodDays: number;
  priceCents: number;
  sortOrder: number;
  isActive: boolean;
}

export function listAdminSubscriptionPlans() {
  return apiFetch<{ items: AdminSubscriptionPlan[] }>("/api/v1/admin/subscription-plans");
}

export function createAdminSubscriptionPlan(data: {
  code: string;
  name: string;
  description?: string;
  monthlyCredits: number;
  storageGb?: number;
  periodDays?: number;
  priceCents?: number;
  sortOrder?: number;
  isActive?: boolean;
}) {
  return apiFetch<AdminSubscriptionPlan>("/api/v1/admin/subscription-plans", {
    method: "POST",
    body: JSON.stringify({
      code: data.code,
      name: data.name,
      description: data.description ?? "",
      monthlyCredits: data.monthlyCredits,
      storageGb: data.storageGb ?? 0,
      periodDays: data.periodDays ?? 30,
      priceCents: data.priceCents ?? 0,
      sortOrder: data.sortOrder ?? 0,
      isActive: data.isActive ?? true,
    }),
  });
}

export function patchAdminSubscriptionPlan(
  planId: string,
  data: Partial<{
    name: string;
    description: string;
    monthlyCredits: number;
    storageGb: number;
    periodDays: number;
    priceCents: number;
    sortOrder: number;
    isActive: boolean;
  }>
) {
  return apiFetch<AdminSubscriptionPlan>(`/api/v1/admin/subscription-plans/${planId}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...(data.name !== undefined ? { name: data.name } : {}),
      ...(data.description !== undefined ? { description: data.description } : {}),
      ...(data.monthlyCredits !== undefined ? { monthlyCredits: data.monthlyCredits } : {}),
      ...(data.storageGb !== undefined ? { storageGb: data.storageGb } : {}),
      ...(data.periodDays !== undefined ? { periodDays: data.periodDays } : {}),
      ...(data.priceCents !== undefined ? { priceCents: data.priceCents } : {}),
      ...(data.sortOrder !== undefined ? { sortOrder: data.sortOrder } : {}),
      ...(data.isActive !== undefined ? { isActive: data.isActive } : {}),
    }),
  });
}

export function deleteAdminSubscriptionPlan(planId: string) {
  return apiFetch<{ ok: boolean }>(`/api/v1/admin/subscription-plans/${planId}`, {
    method: "DELETE",
  });
}

// userRef 支持手机号 / 展示 ID / 用户主键，后端 resolve_user_by_ref 统一解析
export function grantAdminUserSubscription(userRef: string, planId: string, autoRenew = true) {
  return apiFetch<{ ok: boolean; subscription: Record<string, unknown> }>(
    `/api/v1/admin/subscriptions/users/${encodeURIComponent(userRef)}/grant`,
    {
      method: "POST",
      body: JSON.stringify({ planId, autoRenew }),
    }
  );
}

export interface AdminStorageSettings {
  defaultStorageGb: number;
  envDefaultStorageGb: number;
  updatedAt: string;
}

export function getAdminStorageSettings() {
  return apiFetch<AdminStorageSettings>("/api/v1/admin/storage/settings");
}

export function updateAdminStorageSettings(defaultStorageGb: number) {
  return apiFetch<AdminStorageSettings>("/api/v1/admin/storage/settings", {
    method: "PUT",
    body: JSON.stringify({ defaultStorageGb }),
  });
}

/** Skill MD 文档列表（白名单平台 Skill） */
export type AdminSkillDocListItem = {
  slug: string;
  title: string;
  source: "override" | "file" | "missing" | string;
  hasOverride: boolean;
  pipelineStepCount: number;
  summary: string;
  package?: boolean;
  fileCount?: number;
};

export type AdminSkillDocFileNode = {
  name: string;
  path: string;
  type: "file" | "dir";
  children?: AdminSkillDocFileNode[];
};

export type AdminSkillDocFileMeta = {
  path: string;
  source: string;
  size: number;
};

export type AdminSkillDocDetail = {
  slug: string;
  title: string;
  description: string;
  summary: string;
  document: string;
  markdown: string;
  pipeline: unknown[];
  pipelineStepCount: number;
  execution: string;
  entryKind: string;
  source: "override" | "file" | string;
  hasOverride: boolean;
  package?: boolean;
  activePath?: string;
  fileSource?: string;
  fileCount?: number;
  files?: AdminSkillDocFileMeta[];
  tree?: AdminSkillDocFileNode[];
};

export function listAdminSkillDocs() {
  return apiFetch<{ items: AdminSkillDocListItem[] }>("/api/v1/admin/skill-docs");
}

export function getAdminSkillDoc(slug: string, path?: string) {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  return apiFetch<AdminSkillDocDetail>(
    `/api/v1/admin/skill-docs/${encodeURIComponent(slug)}${qs}`
  );
}

export function putAdminSkillDoc(slug: string, document: string, path?: string) {
  return apiFetch<AdminSkillDocDetail>(`/api/v1/admin/skill-docs/${encodeURIComponent(slug)}`, {
    method: "PUT",
    body: JSON.stringify(path ? { document, path } : { document }),
  });
}

export function resetAdminSkillDoc(slug: string) {
  return apiFetch<AdminSkillDocDetail>(
    `/api/v1/admin/skill-docs/${encodeURIComponent(slug)}/reset`,
    { method: "POST" }
  );
}

/** 审核时间线单条 */
export type AdminReviewHistoryItem = {
  action: string;
  note?: string | null;
  at?: string | null;
  by?: string | null;
  byName?: string | null;
};

/** 管理端 Skill 列表 / 审核 */
export type AdminSkillListItem = {
  id: string;
  slug: string;
  title: string;
  description?: string;
  category?: string;
  visibility: string;
  ownerUserId?: string | null;
  reviewStatus?: string;
  reviewNote?: string | null;
  reviewedAt?: string | null;
  reviewedBy?: string | null;
  reviewedByName?: string | null;
  reviewHistory?: AdminReviewHistoryItem[];
  displayStatus?: string;
  status?: string;
  coverUrl?: string | null;
  updatedAt?: string;
};

export function listAdminSkills(params?: {
  displayStatus?: string;
  reviewStatus?: string;
  visibility?: string;
  category?: string;
  q?: string;
  page?: number;
  pageSize?: number;
}) {
  const sp = new URLSearchParams();
  if (params?.displayStatus) sp.set("displayStatus", params.displayStatus);
  if (params?.reviewStatus) sp.set("reviewStatus", params.reviewStatus);
  if (params?.visibility) sp.set("visibility", params.visibility);
  if (params?.category) sp.set("category", params.category);
  if (params?.q) sp.set("q", params.q);
  if (params?.page) sp.set("page", String(params.page));
  if (params?.pageSize) sp.set("pageSize", String(params.pageSize));
  const qs = sp.toString();
  return apiFetch<{
    items: AdminSkillListItem[];
    total: number;
    page: number;
    pageSize: number;
    statusCounts?: Record<string, number>;
  }>(`/api/v1/admin/skills${qs ? `?${qs}` : ""}`);
}

export function getAdminManagedSkillDoc(slug: string) {
  return apiFetch<{
    slug: string;
    title: string;
    markdown: string;
    reviewStatus: string;
    visibility: string;
    displayStatus?: string;
    category?: string;
  }>(`/api/v1/admin/skills/${encodeURIComponent(slug)}/doc`);
}

export function getAdminSkillCategories() {
  return apiFetch<{ categories: string[] }>("/api/v1/admin/skills/categories");
}

export function putAdminSkillCategories(categories: string[]) {
  return apiFetch<{ categories: string[] }>("/api/v1/admin/skills/categories", {
    method: "PUT",
    body: JSON.stringify({ categories }),
  });
}

export function patchAdminSkillCategory(slug: string, category: string) {
  return apiFetch<AdminSkillListItem>(
    `/api/v1/admin/skills/${encodeURIComponent(slug)}/category`,
    {
      method: "PATCH",
      body: JSON.stringify({ category }),
    }
  );
}

/** 管理端修改 Skill 名称 / 封面 / 分类 */
export function patchAdminSkillMeta(
  slug: string,
  input: {
    title?: string;
    coverUrl?: string | null;
    clearCover?: boolean;
    category?: string;
  }
) {
  return apiFetch<AdminSkillListItem>(
    `/api/v1/admin/skills/${encodeURIComponent(slug)}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        ...(input.title !== undefined ? { title: input.title } : {}),
        ...(input.coverUrl !== undefined && input.coverUrl !== null
          ? { coverUrl: input.coverUrl }
          : {}),
        ...(input.category !== undefined ? { category: input.category } : {}),
        ...(input.clearCover ? { clearCover: true } : {}),
      }),
    }
  );
}

export function reviewAdminSkill(
  slug: string,
  action: "approve" | "reject" | "unpublish",
  note?: string,
  category?: string
) {
  return apiFetch<AdminSkillListItem>(
    `/api/v1/admin/skills/${encodeURIComponent(slug)}/review`,
    {
      method: "POST",
      body: JSON.stringify({
        action,
        note: note || null,
        category: category || null,
      }),
    }
  );
}

/** 管理端工作流发布列表 / 审核 */
export type AdminWorkflowPublicationItem = {
  id: string;
  title: string;
  description?: string;
  category?: string;
  isPublic?: boolean;
  reviewStatus?: string;
  reviewNote?: string | null;
  reviewedAt?: string | null;
  reviewedBy?: string | null;
  reviewedByName?: string | null;
  reviewHistory?: AdminReviewHistoryItem[];
  displayStatus?: string;
  authorId?: string;
  authorName?: string;
  videoUrl?: string;
  coverUrl?: string;
  publishedAt?: string;
  updatedAt?: string;
};

export function listAdminWorkflowPublications(params?: {
  displayStatus?: string;
  reviewStatus?: string;
  category?: string;
  q?: string;
  page?: number;
  pageSize?: number;
}) {
  const sp = new URLSearchParams();
  if (params?.displayStatus) sp.set("displayStatus", params.displayStatus);
  if (params?.reviewStatus) sp.set("reviewStatus", params.reviewStatus);
  if (params?.category) sp.set("category", params.category);
  if (params?.q) sp.set("q", params.q);
  if (params?.page) sp.set("page", String(params.page));
  if (params?.pageSize) sp.set("pageSize", String(params.pageSize));
  const qs = sp.toString();
  return apiFetch<{
    items: AdminWorkflowPublicationItem[];
    total: number;
    page: number;
    pageSize: number;
    statusCounts?: Record<string, number>;
  }>(`/api/v1/admin/workflow-publications${qs ? `?${qs}` : ""}`);
}

export function patchAdminWorkflowPublicationCategory(
  publicationId: string,
  category: string
) {
  return apiFetch<AdminWorkflowPublicationItem>(
    `/api/v1/admin/workflow-publications/${encodeURIComponent(publicationId)}/category`,
    {
      method: "PATCH",
      body: JSON.stringify({ category }),
    }
  );
}

export function reviewAdminWorkflowPublication(
  publicationId: string,
  action: "approve" | "reject" | "unpublish",
  note?: string,
  category?: string
) {
  return apiFetch<AdminWorkflowPublicationItem>(
    `/api/v1/admin/workflow-publications/${encodeURIComponent(publicationId)}/review`,
    {
      method: "POST",
      body: JSON.stringify({
        action,
        note: note || null,
        category: category || null,
      }),
    }
  );
}
