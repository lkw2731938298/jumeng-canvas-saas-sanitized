/** 管理后台功能权限键（与后端 admin_permissions.py 对齐） */
export const ADMIN_PERMISSION_KEYS = [
  "dashboard",
  "users",
  "projects",
  "admin_permissions",
  "jobs",
  "generation_logs",
  "models",
  "model_providers",
  "pricing",
  "recharge_tiers",
  "recharge_orders",
  "credit_transactions",
  "credits",
  "credit_activities",
  "invite_campaign",
  "subscription_plans",
  "storage",
  "prompt_templates",
  "error_codes",
  "homepage",
  "material_library",
  "sensitive_words",
] as const;

export type AdminPermissionKey = (typeof ADMIN_PERMISSION_KEYS)[number];

/** 导航路径 → 所需权限 */
export const ADMIN_NAV_PERMISSION: Record<string, AdminPermissionKey> = {
  "/admin": "dashboard",
  "/admin/users": "users",
  "/admin/register-bonus": "users",
  "/admin/projects": "projects",
  "/admin/admin-permissions": "admin_permissions",
  "/admin/jobs": "jobs",
  "/admin/generation-logs": "generation_logs",
  "/admin/models": "models",
  "/admin/model-providers": "model_providers",
  "/admin/pricing": "pricing",
  "/admin/recharge-tiers": "recharge_tiers",
  "/admin/recharge-orders": "recharge_orders",
  "/admin/credit-transactions": "credit_transactions",
  "/admin/credits": "credits",
  "/admin/credit-activities": "credit_activities",
  "/admin/invite-campaign": "invite_campaign",
  "/admin/subscription-plans": "subscription_plans",
  "/admin/storage": "storage",
  "/admin/prompt-templates": "prompt_templates",
  "/admin/error-codes": "error_codes",
  "/admin/content/homepage": "homepage",
  "/admin/content/discover": "homepage",
  "/admin/content/footer": "homepage",
  "/admin/content/material-library": "material_library",
  "/admin/content/skill-docs": "homepage",
  "/admin/content/skills": "homepage",
  "/admin/content/workflow-publications": "homepage",
  "/admin/content/sensitive-words": "sensitive_words",
};

export function hasAdminPermission(
  permissions: string[] | undefined,
  key: string,
  isSuperAdmin = false
): boolean {
  if (isSuperAdmin) return true;
  if (!permissions?.length) return false;
  return permissions.includes(key);
}

export function permissionForPath(pathname: string): AdminPermissionKey | null {
  const entries = Object.entries(ADMIN_NAV_PERMISSION).sort(
    (a, b) => b[0].length - a[0].length
  );
  for (const [prefix, perm] of entries) {
    if (prefix === "/admin") {
      if (pathname === "/admin" || pathname === "/admin/") return perm;
      continue;
    }
    if (pathname === prefix || pathname.startsWith(prefix + "/")) return perm;
  }
  return null;
}
