"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { resolveAdminTabLabel } from "./adminNavConfig";
import { ADMIN_TABS_STORAGE_KEY } from "@/lib/admin/adminTabsStorage";

export type AdminTab = {
  id: string;
  href: string;
  label: string;
};

type AdminTabContextValue = {
  tabs: AdminTab[];
  activeHref: string;
  openTab: (href: string, label?: string) => void;
  closeTab: (href: string) => void;
  activateTab: (href: string) => void;
  /** 只保留锚点标签 */
  closeOthers: (href: string) => void;
  /** 关闭锚点左侧全部 */
  closeLeft: (href: string) => void;
  /** 关闭锚点右侧全部 */
  closeRight: (href: string) => void;
  /** 关闭全部并回到仪表盘 */
  closeAll: () => void;
  /** 刷新锚点对应路由（若为当前页则 router.refresh） */
  refreshTab: (href: string) => void;
};

const AdminTabContext = createContext<AdminTabContextValue | null>(null);

function loadStoredTabs(): AdminTab[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(ADMIN_TABS_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as AdminTab[];
    return Array.isArray(parsed) ? parsed.filter((t) => t?.href && t?.label) : [];
  } catch {
    return [];
  }
}

function persistTabs(tabs: AdminTab[]) {
  try {
    sessionStorage.setItem(ADMIN_TABS_STORAGE_KEY, JSON.stringify(tabs));
  } catch {
    // 忽略存储失败
  }
}

function navigateAfterClose(
  router: ReturnType<typeof useRouter>,
  pathname: string,
  closedHref: string,
  next: AdminTab[],
  prev: AdminTab[]
) {
  if (closedHref !== pathname) return;
  if (next.length > 0) {
    const closedIndex = prev.findIndex((t) => t.href === closedHref);
    const fallback = next[Math.min(Math.max(closedIndex, 0), next.length - 1)];
    router.push(fallback.href);
  } else {
    router.push("/admin");
  }
}

export function AdminTabProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname() || "/admin";
  const [tabs, setTabs] = useState<AdminTab[]>([]);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setTabs(loadStoredTabs());
    setHydrated(true);
  }, []);

  const openTab = useCallback(
    (href: string, label?: string) => {
      const resolvedLabel = label ?? resolveAdminTabLabel(href);
      setTabs((prev) => {
        const exists = prev.find((t) => t.href === href);
        if (exists) {
          if (exists.label === resolvedLabel) return prev;
          return prev.map((t) => (t.href === href ? { ...t, label: resolvedLabel } : t));
        }
        return [...prev, { id: href, href, label: resolvedLabel }];
      });
      if (pathname !== href) {
        router.push(href);
      }
    },
    [pathname, router]
  );

  const closeTab = useCallback(
    (href: string) => {
      setTabs((prev) => {
        const next = prev.filter((t) => t.href !== href);
        navigateAfterClose(router, pathname, href, next, prev);
        return next;
      });
    },
    [pathname, router]
  );

  const activateTab = useCallback(
    (href: string) => {
      if (pathname !== href) router.push(href);
    },
    [pathname, router]
  );

  const closeOthers = useCallback(
    (href: string) => {
      setTabs((prev) => {
        const keep = prev.find((t) => t.href === href);
        const next = keep ? [keep] : [];
        if (pathname !== href) {
          if (keep) router.push(keep.href);
          else router.push("/admin");
        }
        return next;
      });
    },
    [pathname, router]
  );

  const closeLeft = useCallback(
    (href: string) => {
      setTabs((prev) => {
        const idx = prev.findIndex((t) => t.href === href);
        if (idx <= 0) return prev;
        const next = prev.slice(idx);
        if (!next.some((t) => t.href === pathname) && next[0]) {
          router.push(next[0].href);
        }
        return next;
      });
    },
    [pathname, router]
  );

  const closeRight = useCallback(
    (href: string) => {
      setTabs((prev) => {
        const idx = prev.findIndex((t) => t.href === href);
        if (idx < 0 || idx >= prev.length - 1) return prev;
        const next = prev.slice(0, idx + 1);
        if (!next.some((t) => t.href === pathname) && next[next.length - 1]) {
          router.push(next[next.length - 1].href);
        }
        return next;
      });
    },
    [pathname, router]
  );

  const closeAll = useCallback(() => {
    setTabs([]);
    router.push("/admin");
  }, [router]);

  const refreshTab = useCallback(
    (href: string) => {
      if (pathname === href) {
        router.refresh();
        return;
      }
      router.push(href);
      // 切到目标后再 refresh，短延迟保证导航先完成
      window.setTimeout(() => router.refresh(), 0);
    },
    [pathname, router]
  );

  // 路由变化时自动登记当前页为选项卡
  useEffect(() => {
    if (!hydrated || !pathname.startsWith("/admin")) return;
    // 登录页本身不进标签栏
    if (pathname.startsWith("/admin/login")) return;
    const label = resolveAdminTabLabel(pathname);
    setTabs((prev) => {
      const exists = prev.find((t) => t.href === pathname);
      if (exists) {
        if (exists.label === label) return prev;
        return prev.map((t) => (t.href === pathname ? { ...t, label } : t));
      }
      return [...prev, { id: pathname, href: pathname, label }];
    });
  }, [hydrated, pathname]);

  useEffect(() => {
    if (!hydrated) return;
    persistTabs(tabs);
  }, [hydrated, tabs]);

  const value = useMemo(
    () => ({
      tabs,
      activeHref: pathname,
      openTab,
      closeTab,
      activateTab,
      closeOthers,
      closeLeft,
      closeRight,
      closeAll,
      refreshTab,
    }),
    [
      tabs,
      pathname,
      openTab,
      closeTab,
      activateTab,
      closeOthers,
      closeLeft,
      closeRight,
      closeAll,
      refreshTab,
    ]
  );

  return <AdminTabContext.Provider value={value}>{children}</AdminTabContext.Provider>;
}

export function useAdminTabs() {
  const ctx = useContext(AdminTabContext);
  if (!ctx) throw new Error("useAdminTabs must be used within AdminTabProvider");
  return ctx;
}
