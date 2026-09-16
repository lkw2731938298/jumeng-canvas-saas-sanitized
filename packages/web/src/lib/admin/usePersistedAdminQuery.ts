"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * 管理后台列表筛选状态持久化：切到其它选项卡再回来时保留输入与查询条件。
 * 使用 sessionStorage（按 storageKey 隔离），与 AdminTab 的 pathname 导航搭配。
 */
function readStored<T extends Record<string, unknown>>(storageKey: string, defaults: T): T {
  if (typeof window === "undefined") return defaults;
  try {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw) as Partial<T>;
    if (!parsed || typeof parsed !== "object") return defaults;
    return { ...defaults, ...parsed };
  } catch {
    return defaults;
  }
}

export function usePersistedAdminQuery<T extends Record<string, unknown>>(
  storageKey: string,
  defaults: T
): [T, (patch: Partial<T> | ((prev: T) => T)) => void, () => void] {
  const [state, setState] = useState<T>(() => readStored(storageKey, defaults));

  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(state));
    } catch {
      // 忽略存储失败
    }
  }, [storageKey, state]);

  const update = useCallback((patch: Partial<T> | ((prev: T) => T)) => {
    setState((prev) =>
      typeof patch === "function" ? patch(prev) : { ...prev, ...patch }
    );
  }, []);

  const reset = useCallback(() => {
    setState(defaults);
    try {
      sessionStorage.removeItem(storageKey);
    } catch {
      // ignore
    }
  }, [defaults, storageKey]);

  return [state, update, reset];
}
