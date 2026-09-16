"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAdminTabs, type AdminTab } from "./AdminTabContext";

type CtxMenuState = {
  x: number;
  y: number;
  tab: AdminTab;
  index: number;
};

/** 主内容区顶部选项卡栏：多页签切换、关闭，以及右键批量关闭（对齐开源后台 TagsView）。 */
export function AdminTabBar() {
  const {
    tabs,
    activeHref,
    activateTab,
    closeTab,
    closeOthers,
    closeLeft,
    closeRight,
    closeAll,
    refreshTab,
  } = useAdminTabs();
  const [menu, setMenu] = useState<CtxMenuState | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const onClose = (e: MouseEvent) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      setMenu(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(null);
    };
    const onScroll = () => setMenu(null);
    window.addEventListener("mousedown", onClose);
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("mousedown", onClose);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [menu]);

  if (tabs.length === 0) return null;

  const anchorIndex = menu ? menu.index : -1;
  const canCloseLeft = anchorIndex > 0;
  const canCloseRight = anchorIndex >= 0 && anchorIndex < tabs.length - 1;
  const canCloseOthers = tabs.length > 1;

  const run = (fn: () => void) => {
    fn();
    setMenu(null);
  };

  return (
    <div className="relative flex min-h-10 flex-1 items-end gap-1 overflow-x-auto pb-0">
      {tabs.map((tab, index) => {
        const active = tab.href === activeHref;
        return (
          <div
            key={tab.id}
            className={cn(
              "group flex max-w-[200px] shrink-0 items-center gap-1 rounded-t-lg border border-b-0 px-3 py-1.5 text-sm transition-colors",
              active
                ? "border-border bg-background text-foreground"
                : "border-transparent bg-muted/40 text-muted-foreground hover:bg-muted/70 hover:text-foreground"
            )}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenu({ x: e.clientX, y: e.clientY, tab, index });
            }}
          >
            <button
              type="button"
              className="truncate text-left"
              title={tab.label}
              onClick={() => activateTab(tab.href)}
            >
              {tab.label}
            </button>
            <button
              type="button"
              className={cn(
                "rounded p-0.5 opacity-60 transition-opacity hover:bg-muted hover:opacity-100",
                active && "opacity-80"
              )}
              aria-label={`关闭 ${tab.label}`}
              onClick={(e) => {
                e.stopPropagation();
                closeTab(tab.href);
              }}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}

      {menu ? (
        <div
          ref={menuRef}
          className="fixed z-[80] min-w-[10.5rem] rounded-md border border-border bg-popover p-1 text-sm text-popover-foreground shadow-md"
          style={{ left: menu.x, top: menu.y }}
          role="menu"
        >
          <button
            type="button"
            role="menuitem"
            className="flex w-full rounded-sm px-2 py-1.5 text-left hover:bg-accent"
            onClick={() => run(() => refreshTab(menu.tab.href))}
          >
            刷新
          </button>
          <button
            type="button"
            role="menuitem"
            className="flex w-full rounded-sm px-2 py-1.5 text-left hover:bg-accent"
            onClick={() => run(() => closeTab(menu.tab.href))}
          >
            关闭当前
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={!canCloseLeft}
            className="flex w-full rounded-sm px-2 py-1.5 text-left hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
            onClick={() => run(() => closeLeft(menu.tab.href))}
          >
            关闭左侧
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={!canCloseRight}
            className="flex w-full rounded-sm px-2 py-1.5 text-left hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
            onClick={() => run(() => closeRight(menu.tab.href))}
          >
            关闭右侧
          </button>
          <button
            type="button"
            role="menuitem"
            disabled={!canCloseOthers}
            className="flex w-full rounded-sm px-2 py-1.5 text-left hover:bg-accent disabled:pointer-events-none disabled:opacity-40"
            onClick={() => run(() => closeOthers(menu.tab.href))}
          >
            关闭其它
          </button>
          <button
            type="button"
            role="menuitem"
            className="flex w-full rounded-sm px-2 py-1.5 text-left hover:bg-accent"
            onClick={() => run(() => closeAll())}
          >
            关闭全部
          </button>
        </div>
      ) : null}
    </div>
  );
}
