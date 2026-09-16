import { apiFetch } from "./client";

export interface CanvasModel {
  id: string;
  name: string;
  displayName: string;
  provider: string;
  modelType: string;
  category: string;
  description?: string;
  isAvailable: boolean;
  isConfigured?: boolean;
  isImplemented?: boolean;
  providerGroup?: string;
  /** 绑定的 UI 标签 id（多选） */
  uiTagIds?: string[];
  parameters?: Record<string, unknown>;
}

export interface ModelUiTag {
  id: string;
  label: string;
  sortOrder: number;
  categories: string[];
}

export interface ModelUiTagsResponse {
  version: number;
  tags: ModelUiTag[];
}

export function listModels(params?: {
  category?: string;
  modelType?: string;
  provider?: string;
  search?: string;
}) {
  const search = new URLSearchParams();
  if (params?.category) search.set("category", params.category);
  if (params?.modelType) search.set("model_type", params.modelType);
  if (params?.provider) search.set("provider", params.provider);
  if (params?.search) search.set("search", params.search);
  const qs = search.toString();
  return apiFetch<CanvasModel[]>(`/api/v1/models${qs ? `?${qs}` : ""}`);
}

/** 画布模型选择旁的 UI 标签（仅启用） */
export function listModelUiTags(params?: { category?: string }) {
  const search = new URLSearchParams();
  if (params?.category) search.set("category", params.category);
  const qs = search.toString();
  return apiFetch<ModelUiTagsResponse>(`/api/v1/models/ui-tags${qs ? `?${qs}` : ""}`);
}

/** 画布选模左侧系列展示顺序（仅启用） */
export interface ModelUiSeriesItem {
  label: string;
  sortOrder: number;
  categories: string[];
}

export interface ModelUiSeriesResponse {
  version: number;
  series: ModelUiSeriesItem[];
}

export function listModelUiSeries(params?: { category?: string }) {
  const search = new URLSearchParams();
  if (params?.category) search.set("category", params.category);
  const qs = search.toString();
  return apiFetch<ModelUiSeriesResponse>(`/api/v1/models/ui-series${qs ? `?${qs}` : ""}`);
}
