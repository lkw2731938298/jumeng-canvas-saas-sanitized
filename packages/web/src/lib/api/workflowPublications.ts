import { apiFetch } from "./client";
import { ensureHttpsOssUrl } from "@/lib/signedUrl";

export type PublicationScope = "public" | "mine" | "used";

export interface WorkflowPublication {
  id: string;
  sourceProjectId: string;
  title: string;
  description: string;
  category: string;
  isPublic: boolean;
  /** pending | approved | rejected */
  reviewStatus?: string;
  reviewNote?: string | null;
  videoUrl: string;
  videoAssetId: string;
  /** 封面图 URL；广场默认展示，悬浮播视频 */
  coverUrl: string;
  coverAssetId: string;
  likeCount: number;
  useCount: number;
  liked: boolean;
  authorId: string;
  authorName: string;
  authorAvatar: string;
  publishedAt: string;
  updatedAt: string;
}

export interface PublicationListResult {
  items: WorkflowPublication[];
  total: number;
  page: number;
  pageSize: number;
  categories: string[];
}

export interface PublicationPreviewAsset {
  id: string;
  legacyId?: string | null;
  category: string;
  title: string;
  fileUrl: string;
  thumbnailUrl: string;
  fileType: string;
}

export interface PublicationPreview {
  publicationId: string;
  title: string;
  sourceProjectId?: string;
  flowJson: {
    nodes?: Array<Record<string, unknown>>;
    edges?: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  assets: PublicationPreviewAsset[];
  nodeIds?: string[];
}

export interface PublishWorkflowInput {
  projectId: string;
  title: string;
  description: string;
  category: string;
  videoAssetId: string;
  coverAssetId: string;
  isPublic: boolean;
}

function normalizePublication(raw: Record<string, unknown>): WorkflowPublication {
  return {
    id: String(raw.id ?? ""),
    sourceProjectId: String(raw.sourceProjectId ?? ""),
    title: String(raw.title ?? ""),
    description: String(raw.description ?? ""),
    category: String(raw.category ?? ""),
    isPublic: Boolean(raw.isPublic),
    reviewStatus: String(raw.reviewStatus ?? "approved"),
    reviewNote: raw.reviewNote != null ? String(raw.reviewNote) : null,
    videoUrl: ensureHttpsOssUrl(String(raw.videoUrl ?? "")),
    videoAssetId: String(raw.videoAssetId ?? ""),
    coverUrl: ensureHttpsOssUrl(String(raw.coverUrl ?? "")),
    coverAssetId: String(raw.coverAssetId ?? ""),
    likeCount: Number(raw.likeCount ?? 0),
    useCount: Number(raw.useCount ?? 0),
    liked: Boolean(raw.liked),
    authorId: String(raw.authorId ?? ""),
    authorName: String(raw.authorName ?? "创作者"),
    authorAvatar: String(raw.authorAvatar ?? ""),
    publishedAt: String(raw.publishedAt ?? ""),
    updatedAt: String(raw.updatedAt ?? ""),
  };
}

/** 作品广场排序：最新 / 最热（按点赞量） */
export type PublicationSort = "latest" | "hot";

export async function listWorkflowPublications(params: {
  scope: PublicationScope;
  category?: string;
  sort?: PublicationSort;
  page?: number;
  pageSize?: number;
}): Promise<PublicationListResult> {
  const qs = new URLSearchParams();
  qs.set("scope", params.scope);
  if (params.category && params.category !== "全部") qs.set("category", params.category);
  qs.set("sort", params.sort === "hot" ? "hot" : "latest");
  qs.set("page", String(params.page ?? 1));
  qs.set("pageSize", String(params.pageSize ?? 20));
  const raw = await apiFetch<Record<string, unknown>>(
    `/api/v1/workflow-publications?${qs.toString()}`
  );
  const items = Array.isArray(raw.items)
    ? (raw.items as Record<string, unknown>[]).map(normalizePublication)
    : [];
  return {
    items,
    total: Number(raw.total ?? 0),
    page: Number(raw.page ?? 1),
    pageSize: Number(raw.pageSize ?? 20),
    categories: Array.isArray(raw.categories)
      ? (raw.categories as unknown[]).map(String)
      : [],
  };
}

export async function getWorkflowPublication(id: string): Promise<WorkflowPublication> {
  const raw = await apiFetch<Record<string, unknown>>(`/api/v1/workflow-publications/${id}`);
  return normalizePublication(raw);
}

export async function publishWorkflow(input: PublishWorkflowInput): Promise<WorkflowPublication> {
  const raw = await apiFetch<Record<string, unknown>>("/api/v1/workflow-publications", {
    method: "POST",
    body: JSON.stringify(input),
  });
  return normalizePublication(raw);
}

/** 作者删除自己的发布条目 */
export async function deleteWorkflowPublication(
  id: string
): Promise<{ id: string; deleted: boolean }> {
  return apiFetch(`/api/v1/workflow-publications/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function togglePublicationLike(
  id: string
): Promise<{ liked: boolean; likeCount: number }> {
  return apiFetch(`/api/v1/workflow-publications/${id}/like`, { method: "POST" });
}

export async function getPublicationPreview(id: string): Promise<PublicationPreview> {
  const raw = await apiFetch<Record<string, unknown>>(
    `/api/v1/workflow-publications/${id}/preview`
  );
  const assets = Array.isArray(raw.assets)
    ? (raw.assets as Record<string, unknown>[]).map((a) => ({
        id: String(a.id ?? ""),
        legacyId: a.legacyId != null ? String(a.legacyId) : null,
        category: String(a.category ?? "image"),
        title: String(a.title ?? ""),
        fileUrl: ensureHttpsOssUrl(String(a.fileUrl ?? "")),
        thumbnailUrl: ensureHttpsOssUrl(String(a.thumbnailUrl ?? a.fileUrl ?? "")),
        fileType: String(a.fileType ?? ""),
      }))
    : [];
  return {
    publicationId: String(raw.publicationId ?? id),
    title: String(raw.title ?? ""),
    sourceProjectId: raw.sourceProjectId != null ? String(raw.sourceProjectId) : undefined,
    flowJson: (raw.flowJson as PublicationPreview["flowJson"]) || { nodes: [], edges: [] },
    assets,
    nodeIds: Array.isArray(raw.nodeIds) ? raw.nodeIds.map(String) : [],
  };
}

export async function copyWorkflowPublication(
  id: string
): Promise<{ projectId: string; workflowId: string; title: string }> {
  return apiFetch(`/api/v1/workflow-publications/${id}/copy`, { method: "POST" });
}
