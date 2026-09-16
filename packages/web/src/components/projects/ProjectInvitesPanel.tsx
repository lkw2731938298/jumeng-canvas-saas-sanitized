"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, FolderOpen, Loader2, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api/client";
import {
  acceptProjectInvite,
  declineProjectInvite,
  type ProjectInvite,
} from "@/lib/api/projects";
import { resolveProjectCoverDisplayUrl } from "@/lib/api/storageUrl";
import {
  invalidateProjectInviteQueries,
  useProjectInvites,
} from "@/lib/canvas/useProjectInvites";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { useAuthStore } from "@/stores/authStore";

function coverSrc(coverUrl: string | null | undefined): string | null {
  return resolveProjectCoverDisplayUrl(coverUrl);
}

export function ProjectInvitesPanel() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const userId = useAuthStore((s) => s.user?.id);
  const invitesQuery = useProjectInvites();
  const invites = invitesQuery.data ?? [];

  const acceptMutation = useMutation({
    mutationFn: (projectId: string) => acceptProjectInvite(projectId),
    onSuccess: (project) => {
      toast.success(`已加入「${project.title}」`);
      invalidateProjectInviteQueries(queryClient, userId);
      router.push(`/${project.id}`);
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) toast.error(err.message);
      else toast.error("接受邀请失败");
    },
  });

  const declineMutation = useMutation({
    mutationFn: (projectId: string) => declineProjectInvite(projectId),
    onSuccess: () => {
      toast.success("已拒绝邀请");
      invalidateProjectInviteQueries(queryClient, userId);
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) toast.error(err.message);
      else toast.error("拒绝邀请失败");
    },
  });

  const busy = acceptMutation.isPending || declineMutation.isPending;

  if (invitesQuery.isLoading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-white/50" />
      </div>
    );
  }

  if (invitesQuery.isError) {
    return (
      <div className="py-10 text-center">
        <p className="text-sm text-red-300">加载邀请失败，请稍后重试</p>
        <Button
          type="button"
          size="sm"
          variant="secondary"
          className="mt-3"
          onClick={() => void invitesQuery.refetch()}
        >
          重新加载
        </Button>
      </div>
    );
  }

  if (invites.length === 0) {
    return (
      <div className="py-12 text-center">
        <FolderOpen className="mx-auto mb-3 h-10 w-10 text-white/25" />
        <p className="text-sm text-white/60">暂无待处理的协作邀请</p>
        <p className="mt-1 text-xs text-white/40">
          项目创建者邀请您后，会显示在这里；已加入的项目请在「项目列表 → 与我协作」查看
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {invites.map((invite) => (
        <InviteCard
          key={invite.projectId}
          invite={invite}
          busy={busy}
          onAccept={() => acceptMutation.mutate(invite.projectId)}
          onDecline={() => declineMutation.mutate(invite.projectId)}
        />
      ))}
    </div>
  );
}

function InviteCard({
  invite,
  busy,
  onAccept,
  onDecline,
}: {
  invite: ProjectInvite;
  busy: boolean;
  onAccept: () => void;
  onDecline: () => void;
}) {
  const cover = coverSrc(invite.coverUrl);

  return (
    <div className="flex gap-3 rounded-xl border border-white/10 bg-white/5 p-3">
      <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-lg bg-white/10">
        {cover ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={cover} alt="" className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <FolderOpen className="h-6 w-6 text-white/30" />
          </div>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            {invite.projectNo ? (
              <span className="font-mono text-[10px] text-white/40">{invite.projectNo}</span>
            ) : null}
            <h3 className="truncate text-sm font-medium text-white/95">{invite.title || "未命名项目"}</h3>
            <p className="mt-0.5 text-xs text-white/55">
              {invite.inviterDisplayName || "项目创建者"} 邀请您协作编辑
            </p>
            {invite.invitedAt ? (
              <p className="mt-1 text-[11px] text-white/40">{formatDateTimeCN(invite.invitedAt)}</p>
            ) : null}
          </div>
        </div>
        <div className="mt-3 flex gap-2">
          <Button
            type="button"
            size="sm"
            className="h-8"
            disabled={busy}
            onClick={onAccept}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="mr-1 h-3.5 w-3.5" />}
            接受
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-8 text-white/70 hover:bg-white/10 hover:text-white"
            disabled={busy}
            onClick={onDecline}
          >
            <X className="mr-1 h-3.5 w-3.5" />
            拒绝
          </Button>
        </div>
      </div>
    </div>
  );
}
