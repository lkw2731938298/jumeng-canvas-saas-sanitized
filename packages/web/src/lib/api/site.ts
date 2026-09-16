import { apiFetch } from "./client";
import type { DiscoverPageSettings, SiteFooterSettings, SiteHomepageConfig } from "@/types/admin";

/** 登录页背景等公开读取；勿带会话以免 401 误跳登录 */
export function getSiteHomepage() {
  return apiFetch<SiteHomepageConfig>("/api/v1/site/homepage", { skipAuth: true });
}

/** 公开发现页运营配置（无后台配置时后端返回内置默认） */
export function getSiteDiscover() {
  return apiFetch<DiscoverPageSettings>("/api/v1/site/discover", { skipAuth: true });
}

/** 公开首页 Footer 配置（关于我们 / 联系我们二维码 / 社交链接） */
export function getSiteFooter() {
  return apiFetch<SiteFooterSettings>("/api/v1/site/footer", { skipAuth: true });
}

export type SiteLegalDocType = "user-agreement" | "privacy-policy";

export interface SiteLegalDocument {
  docType: SiteLegalDocType;
  title: string;
  markdown: string;
  configured: boolean;
  updatedAt?: string | null;
}

/** 公开读取用户协议或隐私政策正文（登录弹窗展示） */
export function getSiteLegalDocument(docType: SiteLegalDocType) {
  return apiFetch<SiteLegalDocument>(`/api/v1/site/legal/${encodeURIComponent(docType)}`, {
    skipAuth: true,
  });
}

export interface SiteCreditActivity {
  id: string;
  title: string;
  description: string;
  coverUrl: string;
  amount: number;
  claimedCount: number;
  status: "active" | "ended";
  startsAt?: string | null;
  endsAt?: string | null;
}

/** 活动页公开展示数据，由后台算力活动管理。 */
export function getSiteActivities() {
  return apiFetch<{ items: SiteCreditActivity[] }>("/api/v1/site/activities", {
    skipAuth: true,
  });
}

/** 首页「进行中」：仅上架且未过结束时间；草稿/已结束/过期不展示。 */
export function isOngoingSiteActivity(item: {
  status: string;
  endsAt?: string | null;
}): boolean {
  if (item.status !== "active") return false;
  if (!item.endsAt) return true;
  const endMs = new Date(item.endsAt).getTime();
  return Number.isFinite(endMs) && endMs > Date.now();
}
