"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  clearProjectPresence,
  getProjectPresence,
  touchProjectPresence,
} from "@/lib/api/projects";

const HEARTBEAT_MS = 20_000;
const POLL_MS = 15_000;

export function useProjectPresence(projectId: string, enabled: boolean) {
  const presenceQuery = useQuery({
    queryKey: ["projects", projectId, "presence"],
    queryFn: () => getProjectPresence(projectId),
    enabled: enabled && Boolean(projectId),
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
    staleTime: 5_000,
  });

  useEffect(() => {
    if (!enabled || !projectId) return;

    let cancelled = false;
    const beat = () => {
      if (!cancelled) void touchProjectPresence(projectId).catch(() => {});
    };

    beat();
    const timer = window.setInterval(beat, HEARTBEAT_MS);

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      void clearProjectPresence(projectId).catch(() => {});
    };
  }, [enabled, projectId]);

  return {
    peers: presenceQuery.data?.users ?? [],
    isLoading: presenceQuery.isLoading,
  };
}

export function formatPresenceLabel(
  peers: Array<{ displayName: string; userId: string }>
): string | null {
  if (!peers.length) return null;
  const names = peers.map((p) => p.displayName?.trim() || "协作者").slice(0, 3);
  if (peers.length > 3) {
    return `${names.join("、")} 等 ${peers.length} 人正在编辑`;
  }
  if (peers.length === 1) {
    return `${names[0]} 正在编辑`;
  }
  return `${names.join("、")} 正在编辑`;
}
