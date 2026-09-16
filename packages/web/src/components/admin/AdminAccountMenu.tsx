"use client";

import { useRouter } from "next/navigation";
import { ChevronDown, LogOut } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { performLogout } from "@/lib/auth/session";
import { clearAdminTabsStorage } from "@/lib/admin/adminTabsStorage";
import type { AdminUser } from "@/types/admin";

/** 管理后台右上角账号菜单：展示当前管理员，下拉仅提供退出登录（不回用户前台）。 */
export function AdminAccountMenu({ adminUser }: { adminUser: AdminUser }) {
  const router = useRouter();

  const handleLogout = async () => {
    clearAdminTabsStorage();
    await performLogout();
    router.replace("/admin/login");
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        type="button"
        className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-sm text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span>
          当前管理员：
          <span className="text-foreground">{adminUser.displayName}</span>
        </span>
        {adminUser.isSuperAdmin ? (
          <span className="text-xs text-primary">超级管理员</span>
        ) : null}
        <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-70" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="bottom" className="min-w-[10rem]">
        <DropdownMenuItem
          className="cursor-pointer gap-2"
          onClick={() => {
            void handleLogout();
          }}
        >
          <LogOut className="h-4 w-4" />
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
