import { normalizeStorageUrl, isCdnStorageMode, isSignedStorageMode } from "./storageUrl";
import { ensureHttpsOssUrl, isCdnOssUrl, isSignedOssUrl } from "@/lib/signedUrl";
import { validateCanvasImageFile } from "@/lib/canvas/imageSizePolicy";
import { withBasePath } from "@/lib/basePath";
import { resolveErrorMessage } from "@/lib/errors/errorCodes";

export type AssetCategory = "image" | "video" | "audio" | "document" | "model";

export interface Asset {
  id: string;
  projectId: string;
  /** Pre-migration UUID asset id (from OSS path); used for workflow node lookups. */
  legacyId?: string;
  title: string;
  category: AssetCategory;
  subcategory: string | null;
  ossKey?: string;
  fileUrl: string;
  thumbnailUrl: string;
  fileType: string;
  fileSize: number;
  createdAt: string;
}

/** Image uploads from node cards use this subcategory in the asset panel. */
export const NODE_IMAGE_SUBCATEGORY = "素材";

export const ASSETS_UPDATED_EVENT = "canvas:assets-updated";

export const projectAssetsKey = (projectId: string) => ["project-assets", projectId] as const;

export function notifyAssetsUpdated() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(ASSETS_UPDATED_EVENT));
  }
}

function parseApiError(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const record = body as { error?: string; message?: string; detail?: string | { msg?: string }[] };
  if (typeof record.error === "string" && record.error.trim()) return record.error;
  if (typeof record.message === "string" && record.message.trim()) return record.message;
  if (typeof record.detail === "string" && record.detail.trim()) return record.detail;
  if (Array.isArray(record.detail) && record.detail[0]?.msg) return record.detail[0].msg;
  return resolveErrorMessage(body, fallback);
}

function preserveApiStorageUrl(raw: string): string {
  const value = raw.trim();
  if (!value) return "";
  // CDN / 签权直链保留，禁止再改写成 proxy
  if (isCdnOssUrl(value) || isSignedOssUrl(value)) return ensureHttpsOssUrl(value);
  if (
    (isCdnStorageMode() || isSignedStorageMode()) &&
    (value.startsWith("http://") || value.startsWith("https://"))
  ) {
    return ensureHttpsOssUrl(value);
  }
  return normalizeStorageUrl(value);
}

function legacyUuidFromOssKey(ossKey: string | undefined): string | undefined {
  if (!ossKey) return undefined;
  const match = ossKey.match(
    /\/assets\/[^/]+\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\./i
  );
  return match?.[1]?.toLowerCase();
}

function normalizeAsset(raw: Record<string, unknown>): Asset {
  const fileUrl = preserveApiStorageUrl(String(raw.fileUrl ?? raw.file_url ?? ""));
  const thumbnailUrl = preserveApiStorageUrl(
    String(raw.thumbnailUrl ?? raw.thumbnail_url ?? fileUrl)
  );
  const ossKey = String(raw.ossKey ?? raw.oss_key ?? "");
  const legacyId =
    String(raw.legacyId ?? raw.legacy_id ?? "").trim() ||
    legacyUuidFromOssKey(ossKey) ||
    undefined;
  return {
    id: String(raw.id ?? ""),
    projectId: String(raw.projectId ?? raw.project_id ?? ""),
    legacyId,
    title: String(raw.title ?? ""),
    category: raw.category as AssetCategory,
    subcategory: (raw.subcategory as string | null) ?? null,
    ossKey,
    fileUrl,
    thumbnailUrl,
    fileType: String(raw.fileType ?? raw.file_type ?? ""),
    fileSize: Number(raw.fileSize ?? raw.file_size ?? 0),
    createdAt: String(raw.createdAt ?? raw.created_at ?? ""),
  };
}

function bffUrl(path: string): URL {
  return new URL(withBasePath(path), window.location.origin);
}

function authHeaders(): Record<string, string> {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Load the full project asset manifest (all categories). */
export async function fetchProjectAssetManifest(projectId: string): Promise<Asset[]> {
  if (!projectId) return [];
  const url = bffUrl("/api/assets");
  url.searchParams.set("projectId", projectId);
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) return [];
  const list = (await res.json()) as Record<string, unknown>[];
  return list.map((item) => normalizeAsset(item));
}

export async function fetchAssetsBatch(projectId: string, assetIds: string[]): Promise<Asset[]> {
  if (!projectId || assetIds.length === 0) return [];
  const url = bffUrl("/api/assets/batch");
  url.searchParams.set("projectId", projectId);
  url.searchParams.set("ids", assetIds.join(","));
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) return [];
  const list = (await res.json()) as Record<string, unknown>[];
  return list.map((item) => normalizeAsset(item));
}

export function buildAssetLookup(assets: Asset[]): Map<string, Asset> {
  const map = new Map<string, Asset>();
  for (const asset of assets) {
    map.set(asset.id, asset);
    if (asset.legacyId) map.set(asset.legacyId, asset);
  }
  return map;
}

export function lookupAsset(manifest: Asset[] | Map<string, Asset> | undefined, assetId: string): Asset | null {
  if (!assetId || !manifest) return null;
  if (manifest instanceof Map) return manifest.get(assetId) ?? null;
  const direct = manifest.find((item) => item.id === assetId) ?? null;
  if (direct) return direct;
  return manifest.find((item) => item.legacyId === assetId) ?? null;
}

export async function uploadAsset(params: {
  file: File;
  projectId: string;
  category: AssetCategory;
  subcategory?: string | null;
  title?: string;
}): Promise<Asset> {
  if (params.category === "image") {
    const sizeError = validateCanvasImageFile(params.file);
    if (sizeError) throw new Error(sizeError);
  }

  const fd = new FormData();
  fd.append("file", params.file);
  fd.append("projectId", params.projectId);
  fd.append("category", params.category);
  fd.append("title", params.title || params.file.name.replace(/\.[^.]+$/, "") || "未命名");
  if (params.subcategory) fd.append("subcategory", params.subcategory);

  const res = await fetch(withBasePath("/api/assets"), { method: "POST", body: fd, headers: authHeaders() });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(parseApiError(body, "上传失败"));
  }

  const asset = normalizeAsset((await res.json()) as Record<string, unknown>);
  notifyAssetsUpdated();
  return asset;
}

/** Upload GLTF with external resources (.gltf + .bin/.png etc.) as one pack. */
export async function uploadModelBundle(params: {
  files: File[];
  projectId: string;
  subcategory?: string | null;
  title?: string;
}): Promise<Asset> {
  if (!params.files.length) throw new Error("请选择模型文件");
  const fd = new FormData();
  for (const file of params.files) {
    fd.append("files", file);
  }
  fd.append("projectId", params.projectId);
  const main =
    params.files.find((f) => /\.glb$/i.test(f.name)) ??
    params.files.find((f) => /\.gltf$/i.test(f.name)) ??
    params.files[0]!;
  fd.append("title", params.title || main.name.replace(/\.[^.]+$/, "") || "人模");
  if (params.subcategory) fd.append("subcategory", params.subcategory);

  const res = await fetch(withBasePath("/api/assets/model-bundle"), { method: "POST", body: fd, headers: authHeaders() });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(parseApiError(body, "模型包上传失败"));
  }

  const asset = normalizeAsset((await res.json()) as Record<string, unknown>);
  notifyAssetsUpdated();
  return asset;
}

export async function fetchAssets(params: {
  projectId: string;
  category: AssetCategory;
  subcategory?: string | null;
}): Promise<Asset[]> {
  const url = bffUrl("/api/assets");
  url.searchParams.set("projectId", params.projectId);
  url.searchParams.set("category", params.category);
  if (params.category === "image" && params.subcategory) {
    url.searchParams.set("subcategory", params.subcategory);
  }
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) return [];
  const list = (await res.json()) as Record<string, unknown>[];
  return list.map((item) => normalizeAsset(item));
}

export async function fetchAssetById(projectId: string, assetId: string): Promise<Asset | null> {
  if (!projectId || !assetId) return null;
  const batch = await fetchAssetsBatch(projectId, [assetId]);
  return batch[0] ?? null;
}
