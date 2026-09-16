"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Search, Trash2, UserPlus } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  inviteProjectMember,
  listProjectMembers,
  lookupProjectMember,
  removeProjectMember,
  type ProjectMemberLookup,
} from "@/lib/api/projects";
import { ApiError } from "@/lib/api/client";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { ProjectCollaborationSpendPanel } from "@/components/canvas/ProjectCollaborationSpendPanel";

interface ProjectShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
}

export function ProjectShareDialog({
  open,
  onOpenChange,
  projectId,
}: ProjectShareDialogProps) {
  const queryClient = useQueryClient();
  const [phone, setPhone] = useState("");
  const [candidate, setCandidate] = useState<ProjectMemberLookup | null>(null);

  const membersQuery = useQuery({
    queryKey: ["projects", projectId, "members"],
    queryFn: () => listProjectMembers(projectId),
    enabled: open && Boolean(projectId),
  });

  const lookupMutation = useMutation({
    mutationFn: (value: string) => lookupProjectMember(projectId, value),
    onSuccess: (data) => {
      setCandidate(data);
    },
    onError: (err: unknown) => {
      setCandidate(null);
      if (err instanceof ApiError) {
        toast.error(err.message);
      } else {
        toast.error("查找用户失败");
      }
    },
  });

  const inviteMutation = useMutation({
    mutationFn: (userId: string) => inviteProjectMember(projectId, userId),
    onSuccess: () => {
      toast.success("邀请已发送，等待对方接受");
      setPhone("");
      setCandidate(null);
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "members"] });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) toast.error(err.message);
      else toast.error("邀请失败");
    },
  });

  const removeMutation = useMutation({
    mutationFn: (userId: string) => removeProjectMember(projectId, userId),
    onSuccess: () => {
      toast.success("已移除协作成员");
      void queryClient.invalidateQueries({ queryKey: ["projects", projectId, "members"] });
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError) toast.error(err.message);
      else toast.error("移除失败");
    },
  });

  const handleLookup = () => {
    const trimmed = phone.replace(/\D/g, "");
    if (trimmed.length !== 11) {
      toast.error("请输入 11 位手机号");
      return;
    }
    lookupMutation.mutate(trimmed);
  };

  const handleInvite = () => {
    if (!candidate) return;
    inviteMutation.mutate(candidate.userId);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg border-border bg-background/95 backdrop-blur-xl">
        <DialogHeader>
          <DialogTitle>项目协作</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
            <p className="mb-2 text-xs text-muted-foreground">邀请协作者（按手机号）</p>
            <div className="flex gap-2">
              <Input
                value={phone}
                onChange={(e) => {
                  setPhone(e.target.value);
                  setCandidate(null);
                }}
                placeholder="请输入 11 位手机号"
                className="h-9"
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleLookup();
                }}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="shrink-0"
                disabled={lookupMutation.isPending}
                onClick={handleLookup}
              >
                {lookupMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Search className="h-4 w-4" />
                )}
              </Button>
            </div>
            {candidate ? (
              <div className="mt-3 flex items-center justify-between rounded-md border border-border/50 bg-background/60 px-3 py-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{candidate.displayName || "未命名用户"}</p>
                  <p className="text-xs text-muted-foreground">{candidate.phoneMasked}</p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  disabled={inviteMutation.isPending}
                  onClick={handleInvite}
                >
                  {inviteMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <>
                      <UserPlus className="mr-1 h-3.5 w-3.5" />
                      邀请
                    </>
                  )}
                </Button>
              </div>
            ) : null}
          </div>

          <div>
            <p className="mb-2 text-xs text-muted-foreground">协作成员</p>
            {membersQuery.isLoading ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : membersQuery.data && membersQuery.data.length > 0 ? (
              <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                {membersQuery.data.map((member) => (
                  <div
                    key={member.userId}
                    className="flex items-start justify-between gap-3 rounded-lg border border-border/50 bg-muted/10 px-3 py-2.5"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {member.displayName || "未命名用户"}
                        {member.status === "pending" ? (
                          <span className="ml-1.5 text-[10px] font-normal text-amber-500/90">待接受</span>
                        ) : (
                          <span className="ml-1.5 text-[10px] font-normal text-muted-foreground">已加入</span>
                        )}
                      </p>
                      <p className="font-mono text-[11px] text-muted-foreground/80">
                        ID: {member.userId}
                      </p>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        邀请：{member.invitedAt ? formatDateTimeCN(member.invitedAt) : "—"}
                      </p>
                      <p className="text-[11px] text-muted-foreground">
                        首次进入：
                        {member.firstAccessedAt
                          ? formatDateTimeCN(member.firstAccessedAt)
                          : "尚未进入"}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="shrink-0 text-destructive hover:text-destructive"
                      disabled={removeMutation.isPending}
                      onClick={() => {
                        const label = member.status === "pending" ? "取消邀请" : "移除";
                        if (window.confirm(`确定${label}「${member.displayName || "该用户"}」？`)) {
                          removeMutation.mutate(member.userId);
                        }
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="py-6 text-center text-sm text-muted-foreground">暂无协作成员</p>
            )}
          </div>

          {projectId ? (
            <ProjectCollaborationSpendPanel projectId={projectId} enabled={open} />
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
