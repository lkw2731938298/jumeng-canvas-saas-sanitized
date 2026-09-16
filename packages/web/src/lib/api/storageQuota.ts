import { apiFetch } from "./client";

export interface StorageQuota {
  usedBytes: number;
  quotaBytes: number;
  generalGb: number;
  memberGb: number;
  isMemberActive: boolean;
  memberExpiresAt?: string | null;
  usedGb: number;
  quotaGb: number;
}

export function getStorageQuota() {
  return apiFetch<StorageQuota>("/api/v1/storage/quota");
}

export function formatStorageGb(gb: number) {
  return `${gb.toLocaleString(undefined, { maximumFractionDigits: 2 })} GiB`;
}
