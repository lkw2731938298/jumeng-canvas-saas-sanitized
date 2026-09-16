"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImageUp, Loader2, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createAdminCreditActivity,
  deleteAdminCreditActivity,
  listAdminCreditActivities,
  patchAdminCreditActivity,
  type AdminCreditActivity,
  uploadAdminCreditActivityImage,
} from "@/lib/api/admin";
import { formatValidationError } from "@/lib/errors/errorCodes";

// 安全地把表单字符串转为整数：空/非法值回退到默认值，避免发送 NaN→null 触发 422
function toIntOr(value: string, fallback: number): number {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? n : fallback;
}

const CREDIT_TYPES = [
  { value: "activity", label: "活动算力" },
  { value: "model_specific", label: "模型专用算力" },
  { value: "subscription", label: "会员订阅算力" },
];

function toLocalInputValue(date: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function defaultForm() {
  const now = new Date();
  const end = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
  return {
    title: "",
    description: "",
    coverUrl: "",
    creditType: "activity",
    amount: "100",
    modelName: "",
    validDays: "30",
    startsAt: toLocalInputValue(now),
    endsAt: toLocalInputValue(end),
    perUserLimit: "1",
    totalQuota: "",
    status: "active",
    // 领取资格（留空=不限制）
    registeredFrom: "",
    registeredTo: "",
    minRechargeYuan: "",
    minRechargeCredits: "",
    requireActiveMember: false,
  };
}

/** 表单领取条件 → API claimRules；全空则 null */
function buildClaimRules(form: ReturnType<typeof defaultForm>) {
  const rules: {
    registeredFrom?: string;
    registeredTo?: string;
    minRechargeFen?: number;
    minRechargeCredits?: number;
    requireActiveMember?: boolean;
  } = {};
  if (form.registeredFrom.trim()) {
    rules.registeredFrom = new Date(form.registeredFrom).toISOString();
  }
  if (form.registeredTo.trim()) {
    rules.registeredTo = new Date(form.registeredTo).toISOString();
  }
  const yuan = form.minRechargeYuan.trim();
  if (yuan) {
    const fen = Math.round(Number(yuan) * 100);
    if (Number.isFinite(fen) && fen >= 1) rules.minRechargeFen = fen;
  }
  const credits = form.minRechargeCredits.trim();
  if (credits) {
    const n = Math.round(Number(credits));
    if (Number.isFinite(n) && n >= 1) rules.minRechargeCredits = n;
  }
  if (form.requireActiveMember) rules.requireActiveMember = true;
  return Object.keys(rules).length > 0 ? rules : null;
}

function statusBadge(status: string) {
  if (status === "active") return <Badge>进行中</Badge>;
  if (status === "ended") return <Badge variant="secondary">已结束</Badge>;
  return <Badge variant="outline">草稿</Badge>;
}

export default function AdminCreditActivitiesPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(defaultForm);
  const [showForm, setShowForm] = useState(false);
  // 编辑中的活动 ID：为空表示当前表单用于新建，否则用于保存修改
  const [editingId, setEditingId] = useState<string | null>(null);

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["admin", "credit-activities"],
    queryFn: listAdminCreditActivities,
  });

  const resetForm = () => {
    setForm(defaultForm());
    setEditingId(null);
    setShowForm(false);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        title: form.title.trim(),
        description: form.description.trim(),
        coverUrl: form.coverUrl.trim(),
        creditType: form.creditType,
        amount: toIntOr(form.amount, 1),
        modelName: form.creditType === "model_specific" ? form.modelName.trim() : "",
        validDays: toIntOr(form.validDays, 30),
        startsAt: new Date(form.startsAt).toISOString(),
        endsAt: new Date(form.endsAt).toISOString(),
        // 每人每活动固定只可领 1 次（服务端强制 ge=1,le=1）
        perUserLimit: 1,
        totalQuota: form.totalQuota.trim() ? toIntOr(form.totalQuota, 1) : null,
        status: form.status,
        claimRules: buildClaimRules(form),
      };
      if (editingId) {
        return patchAdminCreditActivity(editingId, payload);
      }
      return createAdminCreditActivity({
        ...payload,
        modelName: form.creditType === "model_specific" ? form.modelName.trim() : undefined,
      });
    },
    onSuccess: () => {
      toast.success(editingId ? "活动已更新" : "活动已创建");
      resetForm();
      void queryClient.invalidateQueries({ queryKey: ["admin", "credit-activities"] });
    },
    onError: (err: Error) =>
      toast.error(formatValidationError(err) || err.message || (editingId ? "更新失败" : "创建失败")),
  });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      patchAdminCreditActivity(id, { status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "credit-activities"] });
    },
    onError: () => toast.error("状态更新失败"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAdminCreditActivity(id),
    onSuccess: () => {
      toast.success("活动已删除");
      void queryClient.invalidateQueries({ queryKey: ["admin", "credit-activities"] });
    },
    onError: (err: Error) => toast.error(err.message || "删除失败"),
  });

  const imageMutation = useMutation({
    mutationFn: uploadAdminCreditActivityImage,
    onSuccess: (uploaded) => {
      setForm((current) => ({ ...current, coverUrl: uploaded.imageUrl }));
      toast.success("活动封面上传成功");
    },
    onError: (err: Error) => toast.error(err.message || "活动封面上传失败"),
  });

  const startEdit = (activity: AdminCreditActivity) => {
    const rules = activity.claimRules ?? null;
    setForm({
      title: activity.title,
      description: activity.description ?? "",
      coverUrl: activity.coverUrl ?? "",
      creditType: activity.creditType,
      amount: String(activity.amount),
      modelName: activity.modelName ?? "",
      validDays: String(activity.validDays),
      startsAt: activity.startsAt ? toLocalInputValue(new Date(activity.startsAt)) : defaultForm().startsAt,
      endsAt: activity.endsAt ? toLocalInputValue(new Date(activity.endsAt)) : defaultForm().endsAt,
      perUserLimit: String(activity.perUserLimit || 1),
      totalQuota: activity.totalQuota != null ? String(activity.totalQuota) : "",
      status: activity.status,
      registeredFrom: rules?.registeredFrom
        ? toLocalInputValue(new Date(rules.registeredFrom))
        : "",
      registeredTo: rules?.registeredTo ? toLocalInputValue(new Date(rules.registeredTo)) : "",
      minRechargeYuan:
        rules?.minRechargeFen != null && rules.minRechargeFen > 0
          ? String(rules.minRechargeFen / 100)
          : "",
      minRechargeCredits:
        rules?.minRechargeCredits != null ? String(rules.minRechargeCredits) : "",
      requireActiveMember: Boolean(rules?.requireActiveMember),
    });
    setEditingId(activity.id);
    setShowForm(true);
  };

  const items = data?.items ?? [];
  const sorted = useMemo(
    () => [...items].sort((a, b) => (b.startsAt ?? "").localeCompare(a.startsAt ?? "")),
    [items]
  );

  return (
    <div>
      <AdminHeader
        title="算力活动"
        description="配置用户可领取的限时算力活动；可按注册时间、累计充值、是否会员限制领取资格。"
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          {isFetching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          刷新
        </Button>
        <Button
          size="sm"
          onClick={() => {
            if (showForm) {
              resetForm();
            } else {
              setForm(defaultForm());
              setEditingId(null);
              setShowForm(true);
            }
          }}
        >
          <Plus className="mr-2 h-4 w-4" />
          {showForm ? "收起表单" : "新建活动"}
        </Button>
      </div>

      {showForm ? (
        <div className="mb-6 rounded-xl border border-border bg-card p-4">
          <h3 className="mb-4 text-sm font-medium">{editingId ? "编辑算力活动" : "新建算力活动"}</h3>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="title">活动标题</Label>
              <Input id="title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="amount">赠送算力</Label>
              <Input id="amount" type="number" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="description">活动说明</Label>
              <Input id="description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="coverUrl">活动页封面</Label>
              <div className="flex flex-wrap items-start gap-3">
                {form.coverUrl ? (
                  <img
                    src={form.coverUrl}
                    alt="活动封面预览"
                    className="h-28 w-48 rounded-lg border border-border object-cover"
                  />
                ) : (
                  <div className="flex h-28 w-48 items-center justify-center rounded-lg border border-dashed border-border text-xs text-muted-foreground">
                    暂无封面
                  </div>
                )}
                <div className="min-w-64 flex-1 space-y-2">
                  <Input
                    id="coverUrl"
                    value={form.coverUrl}
                    onChange={(e) => setForm({ ...form, coverUrl: e.target.value })}
                    placeholder="上传图片或填写图片 URL"
                  />
                  <label className="inline-flex h-9 cursor-pointer items-center rounded-md border border-input bg-background px-3 text-sm hover:bg-accent">
                    {imageMutation.isPending ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <ImageUp className="mr-2 h-4 w-4" />
                    )}
                    上传图片
                    <input
                      type="file"
                      accept="image/jpeg,image/png,image/webp,image/gif"
                      className="sr-only"
                      disabled={imageMutation.isPending}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        if (file) imageMutation.mutate(file);
                        event.currentTarget.value = "";
                      }}
                    />
                  </label>
                </div>
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="creditType">算力类型</Label>
              <select
                id="creditType"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={form.creditType}
                onChange={(e) => setForm({ ...form, creditType: e.target.value })}
              >
                {CREDIT_TYPES.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            {form.creditType === "model_specific" ? (
              <div className="space-y-2">
                <Label htmlFor="modelName">模型 name</Label>
                <Input id="modelName" value={form.modelName} onChange={(e) => setForm({ ...form, modelName: e.target.value })} placeholder="与模型目录 name 一致" />
              </div>
            ) : null}
            <div className="space-y-2">
              <Label htmlFor="validDays">领取后有效天数</Label>
              <Input id="validDays" type="number" value={form.validDays} onChange={(e) => setForm({ ...form, validDays: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="perUserLimit">每人可领次数（固定 1 次）</Label>
              <Input id="perUserLimit" type="number" value={1} disabled readOnly />
              <p className="text-xs text-muted-foreground">每人每活动固定只可领取 1 次，不可修改。</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="totalQuota">总名额（留空不限）</Label>
              <Input id="totalQuota" type="number" value={form.totalQuota} onChange={(e) => setForm({ ...form, totalQuota: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="startsAt">开始时间</Label>
              <Input id="startsAt" type="datetime-local" value={form.startsAt} onChange={(e) => setForm({ ...form, startsAt: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="endsAt">结束时间</Label>
              <Input id="endsAt" type="datetime-local" value={form.endsAt} onChange={(e) => setForm({ ...form, endsAt: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="status">状态</Label>
              <select
                id="status"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value })}
              >
                <option value="draft">草稿</option>
                <option value="active">进行中</option>
                <option value="ended">已结束</option>
              </select>
            </div>

            <div className="md:col-span-2 mt-2 border-t border-border pt-4">
              <h4 className="mb-1 text-sm font-medium">领取资格（可选，条件之间为且）</h4>
              <p className="mb-3 text-xs text-muted-foreground">
                留空表示不限制。累计充值仅统计已支付成功的算力充值单（不含会员开通）。
              </p>
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="registeredFrom">注册时间起</Label>
                  <Input
                    id="registeredFrom"
                    type="datetime-local"
                    value={form.registeredFrom}
                    onChange={(e) => setForm({ ...form, registeredFrom: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="registeredTo">注册时间止</Label>
                  <Input
                    id="registeredTo"
                    type="datetime-local"
                    value={form.registeredTo}
                    onChange={(e) => setForm({ ...form, registeredTo: e.target.value })}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="minRechargeYuan">最低累计充值（元）</Label>
                  <Input
                    id="minRechargeYuan"
                    type="number"
                    min={0}
                    step="0.01"
                    value={form.minRechargeYuan}
                    onChange={(e) => setForm({ ...form, minRechargeYuan: e.target.value })}
                    placeholder="例如 100"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="minRechargeCredits">最低累计充值算力点</Label>
                  <Input
                    id="minRechargeCredits"
                    type="number"
                    min={0}
                    value={form.minRechargeCredits}
                    onChange={(e) => setForm({ ...form, minRechargeCredits: e.target.value })}
                    placeholder="例如 500"
                  />
                </div>
                <div className="flex items-center gap-2 md:col-span-2">
                  <input
                    id="requireActiveMember"
                    type="checkbox"
                    className="h-4 w-4 rounded border-input"
                    checked={form.requireActiveMember}
                    onChange={(e) => setForm({ ...form, requireActiveMember: e.target.checked })}
                  />
                  <Label htmlFor="requireActiveMember" className="font-normal">
                    须为有效会员
                  </Label>
                </div>
              </div>
            </div>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" onClick={resetForm} disabled={saveMutation.isPending}>
              取消
            </Button>
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={!form.title.trim() || saveMutation.isPending}
            >
              {saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {editingId ? "保存修改" : "创建活动"}
            </Button>
          </div>
        </div>
      ) : null}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无活动，点击「新建活动」开始配置。</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">标题</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium">算力</th>
                <th className="px-4 py-3 font-medium">领取/名额</th>
                <th className="px-4 py-3 font-medium">有效期</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((activity: AdminCreditActivity) => (
                <tr key={activity.id} className="border-b border-border/60 last:border-b-0">
                  <td className="px-4 py-3">
                    <p className="font-medium">{activity.title}</p>
                    {activity.description ? (
                      <p className="text-xs text-muted-foreground">{activity.description}</p>
                    ) : null}
                    {(activity.claimRuleSummary?.length ?? 0) > 0 ? (
                      <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
                        条件：{activity.claimRuleSummary!.join("；")}
                      </p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    {activity.creditType}
                    {activity.modelName ? <span className="block text-xs text-muted-foreground">{activity.modelName}</span> : null}
                  </td>
                  <td className="px-4 py-3 tabular-nums">{activity.amount}</td>
                  <td className="px-4 py-3 tabular-nums">
                    {activity.claimedCount}
                    {activity.totalQuota != null ? ` / ${activity.totalQuota}` : " / ∞"}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {activity.startsAt ? new Date(activity.startsAt).toLocaleString("zh-CN") : "—"}
                    <br />
                    {activity.endsAt ? new Date(activity.endsAt).toLocaleString("zh-CN") : "—"}
                  </td>
                  <td className="px-4 py-3">{statusBadge(activity.status)}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      {activity.status !== "active" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={statusMutation.isPending}
                          onClick={() => statusMutation.mutate({ id: activity.id, status: "active" })}
                        >
                          上线
                        </Button>
                      ) : null}
                      {activity.status === "active" ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={statusMutation.isPending}
                          onClick={() => statusMutation.mutate({ id: activity.id, status: "ended" })}
                        >
                          结束
                        </Button>
                      ) : null}
                      <Button size="sm" variant="outline" onClick={() => startEdit(activity)}>
                        <Pencil className="mr-1 h-3.5 w-3.5" />
                        编辑
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-destructive hover:text-destructive"
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                          if (window.confirm(`确定删除活动「${activity.title}」？此操作不可恢复。`)) {
                            deleteMutation.mutate(activity.id);
                          }
                        }}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        删除
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
