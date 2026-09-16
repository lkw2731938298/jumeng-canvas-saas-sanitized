"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageUp, Loader2, Pencil, Plus, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createAdminInviteCampaign,
  listAdminInviteBindings,
  listAdminInviteCampaigns,
  patchAdminInviteCampaign,
  reconcileAdminInviteBinding,
  uploadAdminInviteCampaignImage,
  type AdminInviteCampaign,
} from "@/lib/api/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";

function toIntOr(value: string, fallback: number): number {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? n : fallback;
}

function toLocalInputValue(date: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function defaultForm() {
  const now = new Date();
  const end = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
  return {
    title: "邀请有礼",
    description: "邀请好友注册，双方均可获得算力奖励。",
    coverUrl: "",
    inviterRewardAmount: "100",
    inviteeRewardAmount: "50",
    rewardCreditType: "activity",
    rewardValidDays: "30",
    inviterRewardOn: "register",
    maxRewardsPerInviter: "",
    totalInviteQuota: "",
    startsAt: toLocalInputValue(now),
    endsAt: toLocalInputValue(end),
    status: "active",
  };
}

function statusBadge(status: string) {
  if (status === "active") return <Badge>进行中</Badge>;
  if (status === "ended") return <Badge variant="secondary">已结束</Badge>;
  return <Badge variant="outline">草稿</Badge>;
}

export default function AdminInviteCampaignPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(defaultForm);
  const [editingId, setEditingId] = useState<string | null>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["admin", "invite-campaigns"],
    queryFn: listAdminInviteCampaigns,
  });

  const { data: bindingsData, refetch: refetchBindings } = useQuery({
    queryKey: ["admin", "invite-bindings"],
    queryFn: () => listAdminInviteBindings({ limit: 50 }),
  });

  const saveMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        title: form.title.trim() || "邀请有礼",
        description: form.description,
        coverUrl: form.coverUrl,
        inviterRewardAmount: toIntOr(form.inviterRewardAmount, 100),
        inviteeRewardAmount: toIntOr(form.inviteeRewardAmount, 50),
        rewardCreditType: form.rewardCreditType,
        rewardValidDays: toIntOr(form.rewardValidDays, 30),
        inviterRewardOn: form.inviterRewardOn,
        maxRewardsPerInviter: form.maxRewardsPerInviter.trim()
          ? toIntOr(form.maxRewardsPerInviter, 1)
          : null,
        totalInviteQuota: form.totalInviteQuota.trim()
          ? toIntOr(form.totalInviteQuota, 1)
          : null,
        startsAt: new Date(form.startsAt).toISOString(),
        endsAt: new Date(form.endsAt).toISOString(),
        status: form.status,
      };
      if (editingId) {
        return patchAdminInviteCampaign(editingId, payload);
      }
      return createAdminInviteCampaign(payload);
    },
    onSuccess: () => {
      toast.success(editingId ? "已更新邀请活动" : "已创建邀请活动");
      setEditingId(null);
      setForm(defaultForm());
      queryClient.invalidateQueries({ queryKey: ["admin", "invite-campaigns"] });
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const startEdit = (c: AdminInviteCampaign) => {
    setEditingId(c.id);
    setForm({
      title: c.title || "",
      description: c.description || "",
      coverUrl: c.coverUrl || "",
      inviterRewardAmount: String(c.inviterRewardAmount ?? 100),
      inviteeRewardAmount: String(c.inviteeRewardAmount ?? 50),
      rewardCreditType: c.rewardCreditType || "activity",
      rewardValidDays: String(c.rewardValidDays ?? 30),
      inviterRewardOn: c.inviterRewardOn || "register",
      maxRewardsPerInviter:
        c.maxRewardsPerInviter != null ? String(c.maxRewardsPerInviter) : "",
      totalInviteQuota: c.totalInviteQuota != null ? String(c.totalInviteQuota) : "",
      startsAt: c.startsAt ? toLocalInputValue(new Date(c.startsAt)) : form.startsAt,
      endsAt: c.endsAt ? toLocalInputValue(new Date(c.endsAt)) : form.endsAt,
      status: c.status || "draft",
    });
  };

  const onUpload = async (file: File | null) => {
    if (!file) return;
    try {
      const up = await uploadAdminInviteCampaignImage(file);
      setForm((f) => ({ ...f, coverUrl: up.imageUrl || up.url || "" }));
      toast.success("封面已上传");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
    }
  };

  return (
    <div className="space-y-6 p-6">
      <AdminHeader
        title="邀请活动"
        description="配置用户邀请奖励、发奖时机与配额；同时仅一条进行中活动。"
      />
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          <RefreshCw className="mr-1 h-4 w-4" />
          刷新
        </Button>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="rounded-xl border bg-card p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm font-medium">{editingId ? "编辑活动" : "新建活动"}</h2>
            {editingId ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setEditingId(null);
                  setForm(defaultForm());
                }}
              >
                取消编辑
              </Button>
            ) : null}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Label>标题</Label>
              <Input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
              />
            </div>
            <div className="sm:col-span-2">
              <Label>活动介绍</Label>
              <textarea
                className="min-h-[88px] w-full rounded-md border bg-background px-3 py-2 text-sm"
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
            </div>
            <div>
              <Label>邀请人奖励</Label>
              <Input
                value={form.inviterRewardAmount}
                onChange={(e) => setForm({ ...form, inviterRewardAmount: e.target.value })}
              />
            </div>
            <div>
              <Label>被邀请人奖励</Label>
              <Input
                value={form.inviteeRewardAmount}
                onChange={(e) => setForm({ ...form, inviteeRewardAmount: e.target.value })}
              />
            </div>
            <div>
              <Label>算力类型</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.rewardCreditType}
                onChange={(e) => setForm({ ...form, rewardCreditType: e.target.value })}
              >
                <option value="activity">活动算力</option>
                <option value="general">通用算力</option>
              </select>
            </div>
            <div>
              <Label>有效天数</Label>
              <Input
                value={form.rewardValidDays}
                onChange={(e) => setForm({ ...form, rewardValidDays: e.target.value })}
              />
            </div>
            <div>
              <Label>邀请人发奖时机</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.inviterRewardOn}
                onChange={(e) => setForm({ ...form, inviterRewardOn: e.target.value })}
              >
                <option value="register">好友注册即发</option>
                <option value="invitee_first_recharge">好友首充后发</option>
              </select>
            </div>
            <div>
              <Label>状态</Label>
              <select
                className="h-9 w-full rounded-md border bg-background px-2 text-sm"
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                <option value="draft">草稿</option>
                <option value="active">进行中</option>
                <option value="ended">已结束</option>
              </select>
            </div>
            <div>
              <Label>每人奖励次数上限（空=不限）</Label>
              <Input
                value={form.maxRewardsPerInviter}
                onChange={(e) => setForm({ ...form, maxRewardsPerInviter: e.target.value })}
              />
            </div>
            <div>
              <Label>全站名额（空=不限）</Label>
              <Input
                value={form.totalInviteQuota}
                onChange={(e) => setForm({ ...form, totalInviteQuota: e.target.value })}
              />
            </div>
            <div>
              <Label>开始时间</Label>
              <Input
                type="datetime-local"
                value={form.startsAt}
                onChange={(e) => setForm({ ...form, startsAt: e.target.value })}
              />
            </div>
            <div>
              <Label>结束时间</Label>
              <Input
                type="datetime-local"
                value={form.endsAt}
                onChange={(e) => setForm({ ...form, endsAt: e.target.value })}
              />
            </div>
            <div className="sm:col-span-2">
              <Label>封面 URL</Label>
              <div className="flex gap-2">
                <Input
                  value={form.coverUrl}
                  onChange={(e) => setForm({ ...form, coverUrl: e.target.value })}
                />
                <label className="inline-flex cursor-pointer items-center gap-1 rounded-md border px-3 text-sm">
                  <ImageUp className="h-4 w-4" />
                  上传
                  <input
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={(e) => onUpload(e.target.files?.[0] || null)}
                  />
                </label>
              </div>
            </div>
          </div>
          <Button
            className="mt-4"
            disabled={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : editingId ? (
              <Pencil className="mr-2 h-4 w-4" />
            ) : (
              <Plus className="mr-2 h-4 w-4" />
            )}
            {editingId ? "保存修改" : "创建活动"}
          </Button>
        </section>

        <section className="rounded-xl border bg-card p-5">
          <h2 className="mb-4 text-sm font-medium">活动列表</h2>
          {isLoading ? (
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          ) : (
            <div className="space-y-3">
              {(data?.items || []).map((c) => (
                <div
                  key={c.id}
                  className="flex items-start justify-between gap-3 rounded-lg border p-3"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <p className="font-medium">{c.title}</p>
                      {statusBadge(c.status)}
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      邀请人 {c.inviterRewardAmount} / 被邀请人 {c.inviteeRewardAmount} · 已邀{" "}
                      {c.rewardedInviteeCount ?? 0}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {c.startsAt ? formatDateTimeCN(c.startsAt) : "—"} ~{" "}
                      {c.endsAt ? formatDateTimeCN(c.endsAt) : "—"}
                    </p>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => startEdit(c)}>
                    编辑
                  </Button>
                </div>
              ))}
              {!data?.items?.length ? (
                <p className="text-sm text-muted-foreground">暂无活动，请先创建。</p>
              ) : null}
            </div>
          )}
        </section>
      </div>

      <section className="rounded-xl border bg-card p-5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-medium">最近邀请绑定</h2>
          <Button variant="outline" size="sm" onClick={() => refetchBindings()}>
            刷新明细
          </Button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b text-xs text-muted-foreground">
                <th className="pb-2 font-medium">被邀请人</th>
                <th className="pb-2 font-medium">手机</th>
                <th className="pb-2 font-medium">邀请码</th>
                <th className="pb-2 font-medium">被邀奖励</th>
                <th className="pb-2 font-medium">邀请人奖励</th>
                <th className="pb-2 font-medium">时间</th>
                <th className="pb-2 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {(bindingsData?.items || []).map((b) => (
                <tr key={b.id} className="border-b last:border-0">
                  <td className="py-2">{b.inviteeDisplayName || b.inviteeUserId}</td>
                  <td className="py-2 font-mono text-xs">{b.inviteePhoneMasked}</td>
                  <td className="py-2 font-mono">{b.inviteCode}</td>
                  <td className="py-2">{b.inviteeRewardStatus}</td>
                  <td className="py-2">{b.inviterRewardStatus}</td>
                  <td className="py-2 text-xs text-muted-foreground">
                    {b.createdAt ? formatDateTimeCN(b.createdAt) : "—"}
                  </td>
                  <td className="py-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        try {
                          await reconcileAdminInviteBinding(b.id);
                          toast.success("已尝试补发");
                          refetchBindings();
                        } catch (err) {
                          toast.error(err instanceof Error ? err.message : "补发失败");
                        }
                      }}
                    >
                      补发
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!bindingsData?.items?.length ? (
            <p className="py-6 text-sm text-muted-foreground">暂无绑定记录</p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
