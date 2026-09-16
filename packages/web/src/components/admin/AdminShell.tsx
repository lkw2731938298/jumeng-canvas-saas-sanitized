"use client";

import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { useAdminAuth } from "./AdminAuthContext";
import {
  ADMIN_BRAND_ICON,
  ADMIN_NAV_SECTIONS,
  filterAdminNavByPermissions,
} from "./adminNavConfig";
import { useAdminTabs } from "./AdminTabContext";

const BrandIcon = ADMIN_BRAND_ICON;

export function AdminSidebar() {
  const pathname = usePathname();
  const { openTab } = useAdminTabs();
  const { hasPermission } = useAdminAuth();
  const sections = filterAdminNavByPermissions(ADMIN_NAV_SECTIONS, hasPermission);

  return (
    <aside className="fixed left-0 top-0 z-40 flex h-screen w-56 flex-col border-r border-border bg-background">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <BrandIcon className="h-5 w-5 text-primary" />
        <span className="font-semibold">画布运营后台</span>
      </div>

      {/* 管理域与用户站隔离：侧栏不再提供「返回前台」入口 */}
      <nav className="flex-1 flex flex-col gap-1 overflow-y-auto p-3">
        {sections.map((section, sectionIndex) => (
          <div key={section.title ?? `section-${sectionIndex}`} className={sectionIndex > 0 ? "mt-4" : ""}>
            {section.title ? (
              <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground/75">
                {section.title}
              </p>
            ) : null}
            {section.items.map(({ href, label, icon: Icon, exact }) => {
              const active = exact ? pathname === href : pathname.startsWith(href);
              return (
                <button
                  key={href}
                  type="button"
                  onClick={() => openTab(href, label)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                    active
                      ? "bg-primary/15 text-primary"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground"
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {label}
                </button>
              );
            })}
          </div>
        ))}
      </nav>
    </aside>
  );
}

export function AdminHeader({ title, description }: { title: string; description?: string }) {
  return (
    <header className="mb-6">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <BrandIcon className="h-3.5 w-3.5" />
        <span>聚梦画布 · 运营管理</span>
      </div>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">{title}</h1>
      {description ? (
        <p className="mt-1 text-sm text-muted-foreground">{description}</p>
      ) : null}
    </header>
  );
}

export function StatCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="mt-2 text-3xl font-semibold tabular-nums">{value}</p>
      {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}
