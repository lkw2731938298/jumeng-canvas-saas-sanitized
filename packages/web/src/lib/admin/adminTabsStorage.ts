/** 管理后台页签 sessionStorage 读写；登录/退出时清空，避免历史标签残留。 */

export const ADMIN_TABS_STORAGE_KEY = "canvas-admin-tabs-v1";

export function clearAdminTabsStorage() {
  if (typeof window === "undefined") return;
  try {
    sessionStorage.removeItem(ADMIN_TABS_STORAGE_KEY);
  } catch {
    // 忽略存储失败
  }
}
