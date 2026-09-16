"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { AdminUser } from "@/types/admin";
import { isAdminAuthDisabled } from "@/lib/adminAuth";
import {
  ADMIN_PERMISSION_KEYS,
  type AdminPermissionKey,
  hasAdminPermission,
} from "@/lib/admin/permissions";

type AdminAuthContextValue = {
  adminUser: AdminUser;
  permissions: string[];
  isSuperAdmin: boolean;
  canManagePermissions: boolean;
  hasPermission: (key: AdminPermissionKey | string) => boolean;
};

const AdminAuthContext = createContext<AdminAuthContextValue | null>(null);

/** 开发关闭鉴权时视为超管（全部权限） */
const DEV_ALL_PERMISSIONS = [...ADMIN_PERMISSION_KEYS];

export function AdminAuthProvider({
  adminUser,
  children,
}: {
  adminUser: AdminUser;
  children: ReactNode;
}) {
  const permissions = isAdminAuthDisabled
    ? DEV_ALL_PERMISSIONS
    : adminUser.permissions ?? [];
  const isSuperAdmin = isAdminAuthDisabled || Boolean(adminUser.isSuperAdmin);
  const canManagePermissions =
    isAdminAuthDisabled ||
    Boolean(adminUser.canManagePermissions) ||
    hasAdminPermission(permissions, "admin_permissions", isSuperAdmin);

  const value: AdminAuthContextValue = {
    adminUser,
    permissions,
    isSuperAdmin,
    canManagePermissions,
    hasPermission: (key) =>
      hasAdminPermission(permissions, key, isSuperAdmin || isAdminAuthDisabled),
  };

  return <AdminAuthContext.Provider value={value}>{children}</AdminAuthContext.Provider>;
}

export function useAdminAuth(): AdminAuthContextValue {
  const ctx = useContext(AdminAuthContext);
  if (!ctx) {
    throw new Error("useAdminAuth must be used within AdminAuthProvider");
  }
  return ctx;
}
