"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { toast } from "sonner";
import { AdminAuthProvider, useAdminAuth } from "@/components/admin/AdminAuthContext";
import { AdminAccountMenu } from "@/components/admin/AdminAccountMenu";
import { AdminSidebar } from "@/components/admin/AdminShell";
import { AdminTabBar } from "@/components/admin/AdminTabBar";
import { AdminTabProvider } from "@/components/admin/AdminTabContext";
import { getAdminMe } from "@/lib/api/admin";
import { isAdminAuthDisabled } from "@/lib/adminAuth";
import { ADMIN_PERMISSION_KEYS, ADMIN_NAV_PERMISSION, permissionForPath } from "@/lib/admin/permissions";
import { ApiError } from "@/lib/api/client";
import { useAuthStore } from "@/stores/authStore";
import type { AdminUser } from "@/types/admin";

const DEV_ADMIN_PLACEHOLDER: AdminUser = {
  id: "dev",
  sourceUserId: "dev_user",
  displayName: "开发模式",
  role: "admin",
  isSuperAdmin: true,
  permissions: [...ADMIN_PERMISSION_KEYS],
  canManagePermissions: true,
};

/** 无权限访问时拦截；若连仪表盘都没有则停留提示，避免跳转死循环 */
function AdminPermissionGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { hasPermission, permissions, isSuperAdmin } = useAdminAuth();
  const required = permissionForPath(pathname || "/admin");

  useEffect(() => {
    if (!required) return;
    if (hasPermission(required)) return;
    const fallbackEntry = Object.entries(ADMIN_NAV_PERMISSION)
      .sort((a, b) => a[0].length - b[0].length)
      .find(([, key]) => hasPermission(key));
    const fallback = fallbackEntry?.[0] ?? null;
    toast.error("当前账号无此功能权限");
    if (fallback && fallback !== pathname) {
      router.replace(fallback);
    }
  }, [hasPermission, required, router, pathname, permissions, isSuperAdmin]);

  if (required && !hasPermission(required)) {
    return (
      <div className="px-8 py-12 text-sm text-muted-foreground">
        无权访问该功能
        {!isSuperAdmin && permissions.length === 0 ? (
          <p className="mt-2">当前账号尚未被分配任何后台功能权限，请联系超级管理员授权。</p>
        ) : null}
      </div>
    );
  }
  return <>{children}</>;
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const hydrate = useAuthStore((s) => s.hydrate);
  const [adminUser, setAdminUser] = useState<AdminUser | null>(
    isAdminAuthDisabled ? DEV_ADMIN_PLACEHOLDER : null
  );
  const [checking, setChecking] = useState(!isAdminAuthDisabled);
  const [authError, setAuthError] = useState<string | null>(null);

  useEffect(() => {
    hydrate();
    if (isAdminAuthDisabled) return;

    getAdminMe()
      .then((user) => {
        setAdminUser(user);
        setChecking(false);
        setAuthError(null);
      })
      .catch((err: unknown) => {
        setChecking(false);
        setAdminUser(null);
        if (err instanceof ApiError) {
          if (err.status === 403) {
            setAuthError("当前账号无管理后台访问权限");
            toast.error("无管理后台访问权限");
          } else if (err.status === 401) {
            setAuthError("请先使用管理员账号登录");
            toast.error("请先登录");
          } else {
            setAuthError(err.message || "管理权限校验失败");
          }
        } else {
          setAuthError("管理权限校验失败");
        }
        const next = encodeURIComponent(pathname || "/admin");
        router.replace(`/admin/login?next=${next}`);
      });
  }, [hydrate, pathname, router]);

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground">
        正在验证管理权限…
      </div>
    );
  }

  if (!adminUser) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-background px-6 text-center text-sm text-muted-foreground">
        <p>{authError ?? "正在跳转登录…"}</p>
        <Link href={`/admin/login?next=${encodeURIComponent(pathname || "/admin")}`} className="text-primary hover:underline">
          前往管理员登录
        </Link>
      </div>
    );
  }

  return (
    <AdminAuthProvider adminUser={adminUser}>
      <AdminTabProvider>
        <div className="min-h-screen bg-background">
          <AdminSidebar />
          <div className="pl-56">
            <div className="flex items-end justify-between gap-4 border-b border-border bg-muted/20 px-6 pt-2">
              <AdminTabBar />
              <div className="shrink-0 pb-2">
                {isAdminAuthDisabled ? (
                  <span className="text-sm text-amber-500">开发模式 · 管理权限校验已关闭</span>
                ) : (
                  <AdminAccountMenu adminUser={adminUser} />
                )}
              </div>
            </div>
            <main className="px-8 py-6">
              <AdminPermissionGate>{children}</AdminPermissionGate>
            </main>
          </div>
        </div>
      </AdminTabProvider>
    </AdminAuthProvider>
  );
}
