import { apiFetch } from "./client";

export type UserLocalModel = {
  id: string;
  name: string;
  displayName: string;
  provider: string;
  category: string;
  upstreamModel?: string;
  comfyBaseUrl?: string;
  localApiBase?: string;
  isAvailable: boolean;
  isConfigured?: boolean;
  isImplemented?: boolean;
};

export function listMyLocalModels() {
  return apiFetch<{ items: UserLocalModel[] }>("/api/v1/me/local-models");
}

export function createMyLocalModel(data: {
  displayName: string;
  category: "image" | "video";
  backend: "comfyui" | "openai_compat";
  upstreamModel: string;
  comfyBaseUrl?: string;
  localApiBase?: string;
  comfyFolder?: string;
  comfyWorkflow?: unknown;
}) {
  return apiFetch<UserLocalModel>("/api/v1/me/local-models", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export function deleteMyLocalModel(id: string) {
  return apiFetch<{ ok: boolean }>(`/api/v1/me/local-models/${id}`, { method: "DELETE" });
}
