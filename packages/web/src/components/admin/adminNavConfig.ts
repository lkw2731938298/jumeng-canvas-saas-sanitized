import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Banknote,
  Box,
  Boxes,
  Coins,
  Crown,
  FileText,
  FolderKanban,
  Gift,
  History,
  Home,
  Images,
  KeyRound,
  LayoutDashboard,
  ListTodo,
  Receipt,
  Scroll,
  ScrollText,
  ShieldAlert,
  Sparkles,
  UserPlus,
  Users,
  Wallet,
  Shield,
} from "lucide-react";

export type AdminNavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
  exact?: boolean;
  /** 所需功能权限键；缺省则仅要求登录管理员 */
  permission?: string;
};

export type AdminNavSection = {
  title?: string;
  items: AdminNavItem[];
};

/** 后台侧边栏导航配置（与选项卡标题共用） */
export const ADMIN_NAV_SECTIONS: AdminNavSection[] = [
  {
    title: "概览",
    items: [
      { href: "/admin", label: "仪表盘", icon: LayoutDashboard, exact: true, permission: "dashboard" },
    ],
  },
  {
    title: "用户与项目",
    items: [
      { href: "/admin/users", label: "用户管理", icon: Users, permission: "users" },
      { href: "/admin/register-bonus", label: "注册赠送", icon: Gift, permission: "users" },
      { href: "/admin/projects", label: "项目列表", icon: FolderKanban, permission: "projects" },
      {
        href: "/admin/admin-permissions",
        label: "管理员权限",
        icon: Shield,
        permission: "admin_permissions",
      },
    ],
  },
  {
    title: "生成与模型",
    items: [
      { href: "/admin/jobs", label: "生成任务", icon: ListTodo, permission: "jobs" },
      { href: "/admin/generation-logs", label: "模型调用日志", icon: Scroll, permission: "generation_logs" },
      { href: "/admin/models", label: "模型开关", icon: Boxes, permission: "models" },
      { href: "/admin/model-providers", label: "供应商密钥", icon: KeyRound, permission: "model_providers" },
      { href: "/admin/pricing", label: "算力价格设置", icon: Coins, permission: "pricing" },
    ],
  },
  {
    title: "算力与计费",
    items: [
      { href: "/admin/recharge-tiers", label: "充值档位", icon: Banknote, permission: "recharge_tiers" },
      { href: "/admin/recharge-orders", label: "充值记录", icon: Receipt, permission: "recharge_orders" },
      {
        href: "/admin/credit-transactions",
        label: "算力变动记录",
        icon: History,
        permission: "credit_transactions",
      },
      { href: "/admin/credits", label: "算力对账", icon: Wallet, permission: "credits" },
      { href: "/admin/credit-activities", label: "算力活动", icon: Gift, permission: "credit_activities" },
      {
        href: "/admin/invite-campaign",
        label: "邀请活动",
        icon: UserPlus,
        permission: "invite_campaign",
      },
      {
        href: "/admin/subscription-plans",
        label: "会员套餐",
        icon: Crown,
        permission: "subscription_plans",
      },
    ],
  },
  {
    title: "平台配置",
    items: [
      { href: "/admin/storage", label: "内存管理", icon: Box, permission: "storage" },
      { href: "/admin/prompt-templates", label: "Prompt 模板", icon: FileText, permission: "prompt_templates" },
      { href: "/admin/error-codes", label: "错误编码表", icon: ScrollText, permission: "error_codes" },
    ],
  },
  {
    title: "内容管理",
    items: [
      { href: "/admin/content/homepage", label: "登录页背景", icon: Home, permission: "homepage" },
      { href: "/admin/content/discover", label: "发现页内容", icon: Sparkles, permission: "homepage" },
      { href: "/admin/content/footer", label: "首页内容", icon: FileText, permission: "homepage" },
      {
        href: "/admin/content/material-library",
        label: "素材库",
        icon: Images,
        permission: "material_library",
      },
      { href: "/admin/content/skill-docs", label: "Skill 文档", icon: Scroll, permission: "homepage" },
      { href: "/admin/content/skills", label: "Skill 管理", icon: Sparkles, permission: "homepage" },
      {
        href: "/admin/content/workflow-publications",
        label: "工作流发布",
        icon: FolderKanban,
        permission: "homepage",
      },
      {
        href: "/admin/content/sensitive-words",
        label: "敏感词",
        icon: ShieldAlert,
        permission: "sensitive_words",
      },
    ],
  },
];

/** 按权限过滤侧栏（无权限项的分组自动隐藏） */
export function filterAdminNavByPermissions(
  sections: AdminNavSection[],
  hasPermission: (key: string) => boolean
): AdminNavSection[] {
  return sections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => !item.permission || hasPermission(item.permission)),
    }))
    .filter((section) => section.items.length > 0);
}
/** 根据路径解析选项卡标题 */
export function resolveAdminTabLabel(pathname: string): string {
  for (const section of ADMIN_NAV_SECTIONS) {
    for (const item of section.items) {
      const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
      if (active) return item.label;
    }
  }
  if (pathname.startsWith("/admin")) return "后台";
  return "页面";
}

export const ADMIN_BRAND_ICON = Activity;
