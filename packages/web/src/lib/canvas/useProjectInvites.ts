"use client";

import { useQuery } from "@tanstack/react-query";
import { getProjectInviteCount, listProjectInvites } from "@/lib/api/projects";
import { useAuthStore } from "@/stores/authStore";

export function usePendingProjectInviteCount(enabled = true) {
  const userId = useAuthStore((s) => s.user?.id);
  return useQuery({
    queryKey: ["projects", userId, "invites", "count"],
    queryFn: getProjectInviteCount,
    enabled: enabled && Boolean(userId),
    refetchInterval: 30_000,
    staleTime: 10_000,
  });
}

export function useProjectInvites(enabled = true) {
  const userId = useAuthStore((s) => s.user?.id);
  return useQuery({
    queryKey: ["projects", userId, "invites"],
    queryFn: listProjectInvites,
    enabled: enabled && Boolean(userId),
    refetchOnMount: "always",
    retry: 2,
  });
}

export function invalidateProjectInviteQueries(
  queryClient: { invalidateQueries: (opts: { queryKey: unknown[] }) => void },
  userId?: string | null
) {
  const uid = userId ?? undefined;
  queryClient.invalidateQueries({ queryKey: ["projects", uid, "invites"] });
  queryClient.invalidateQueries({ queryKey: ["projects", uid, "invites", "count"] });
  queryClient.invalidateQueries({ queryKey: ["projects", uid, "shared"] });
}
