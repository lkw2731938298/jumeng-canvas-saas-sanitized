"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createAdminSubscriptionPlan,
  deleteAdminSubscriptionPlan,
  grantAdminUserSubscription,
  listAdminSubscriptionPlans,
  patchAdminSubscriptionPlan,
  type AdminSubscriptionPlan,
} from "@/lib/api/admin";
import { formatPriceYuan } from "@/lib/api/subscriptions";
import { formatValidationError } from "@/lib/errors/errorCodes";

// 安全地把表单字符串转为整数：空/非法值回退到默认值，避免发送 NaN→null 触发 422
function toIntOr(value: string, fallback: number): number {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? n : fallback;
}

function defaultForm() {
  return {
    code: "",
    name: "",
    description: "",
    monthlyCredits: "500",
    storageGb: "20",
    periodDays: "30",
    priceCents: "2900",
    sortOrder: "0",
  };
}

export default function AdminSubscriptionPlansPage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState(defaultForm);
  const [showForm, setShowForm] = useState(false);
  // 编辑中的套餐 ID：为空表示当前表单用于新建，否则用于保存修改
  const [editingId, setEditingId] = useState<string | null>(null);
  // 为用户开通会员：改用手机号作为标识
  const [grantPhone, setGrantPhone] = useState("");
  const [grantPlanId, setGrantPlanId] = useState("");

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["admin", "subscription-plans"],
    queryFn: listAdminSubscriptionPlans,
  });

  const resetForm = () => {
    setForm(defaultForm());
    setEditingId(null);
    setShowForm(false);
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim(),
        monthlyCredits: toIntOr(form.monthlyCredits, 0),
        storageGb: toIntOr(form.storageGb, 0),
        periodDays: toIntOr(form.periodDays, 30),
        priceCents: toIntOr(form.priceCents, 0),
        sortOrder: toIntOr(form.sortOrder, 0),
      };
      if (editingId) {
        return patchAdminSubscriptionPlan(editingId, payload);
      }
      return createAdminSubscriptionPlan({ code: form.code.trim(), ...payload });
    },
    onSuccess: () => {
      toast.success(editingId ? "套餐已更新" : "套餐已创建");
      resetForm();
      void queryClient.invalidateQueries({ queryKey: ["admin", "subscription-plans"] });
    },
    onError: (err: Error) =>
      toast.error(formatValidationError(err) || err.message || (editingId ? "更新失败" : "创建失败")),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) =>
      patchAdminSubscriptionPlan(id, { isActive }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "subscription-plans"] });
    },
    onError: () => toast.error("更新失败"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAdminSubscriptionPlan(id),
    onSuccess: () => {
      toast.success("套餐已删除");
      void queryClient.invalidateQueries({ queryKey: ["admin", "subscription-plans"] });
    },
    onError: (err: Error) => toast.error(err.message || "删除失败"),
  });

  const grantMutation = useMutation({
    mutationFn: () => grantAdminUserSubscription(grantPhone.trim(), grantPlanId),
    onSuccess: () => {
      toast.success("已为用户开通会员并发放算力");
      setGrantPhone("");
    },
    onError: (err: Error) => toast.error(err.message || "开通失败"),
  });

  const startEdit = (plan: AdminSubscriptionPlan) => {
    setForm({
      code: plan.code,
      name: plan.name,
      description: plan.description ?? "",
      monthlyCredits: String(plan.monthlyCredits),
      storageGb: String(plan.storageGb),
      periodDays: String(plan.periodDays),
      priceCents: String(plan.priceCents),
      sortOrder: String(plan.sortOrder),
    });
    setEditingId(plan.id);
    setShowForm(true);
  };

  const plans = data?.items ?? [];

  return (
    <div>
      <AdminHeader
        title="会员套餐"
        description="配置月会员套餐、每期赠送算力与额外云存储（GiB）；切换套餐立即按新配额生效。"
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
          {showForm ? "收起表单" : "新建套餐"}
        </Button>
      </div>

      <div className="mb-6 rounded-xl border border-border bg-card p-4">
        <h3 className="mb-3 text-sm font-medium">为用户开通会员</h3>
        <div className="flex flex-wrap items-end gap-3">
          <div className="space-y-2">
            <Label htmlFor="grantUserId">用户手机号</Label>
            <Input
              id="grantUserId"
              value={grantPhone}
              onChange={(e) => setGrantPhone(e.target.value.replace(/\D/g, "").slice(0, 11))}
              className="w-72"
              inputMode="numeric"
              maxLength={11}
              placeholder="请输入 11 位手机号"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="grantPlanId">套餐</Label>
            <select
              id="grantPlanId"
              className="flex h-10 min-w-48 rounded-md border border-input bg-background px-3 text-sm"
              value={grantPlanId}
              onChange={(e) => setGrantPlanId(e.target.value)}
            >
              <option value="">选择套餐</option>
              {plans.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.name}
                </option>
              ))}
            </select>
          </div>
          <Button
            onClick={() => grantMutation.mutate()}
            disabled={grantPhone.trim().length !== 11 || !grantPlanId || grantMutation.isPending}
          >
            {grantMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            开通并发放
          </Button>
        </div>
      </div>

      {showForm ? (
        <div className="mb-6 rounded-xl border border-border bg-card p-4">
          <h3 className="mb-4 text-sm font-medium">{editingId ? "编辑会员套餐" : "新建会员套餐"}</h3>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="code">Code（唯一{editingId ? "，不可修改" : ""}）</Label>
              <Input
                id="code"
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
                disabled={Boolean(editingId)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="name">名称</Label>
              <Input id="name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="description">说明</Label>
              <Input id="description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="monthlyCredits">每期赠送算力（不限）</Label>
              <Input id="monthlyCredits" type="number" min={0} value={form.monthlyCredits} onChange={(e) => setForm({ ...form, monthlyCredits: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="storageGb">额外存储（GiB，不限）</Label>
              <Input id="storageGb" type="number" min={0} value={form.storageGb} onChange={(e) => setForm({ ...form, storageGb: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="periodDays">账期天数（1–3650）</Label>
              <Input id="periodDays" type="number" min={1} max={3650} value={form.periodDays} onChange={(e) => setForm({ ...form, periodDays: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="priceCents">标价（分，≥ 0）</Label>
              <Input id="priceCents" type="number" min={0} value={form.priceCents} onChange={(e) => setForm({ ...form, priceCents: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="sortOrder">排序</Label>
              <Input id="sortOrder" type="number" value={form.sortOrder} onChange={(e) => setForm({ ...form, sortOrder: e.target.value })} />
            </div>
          </div>
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="outline" onClick={resetForm} disabled={saveMutation.isPending}>
              取消
            </Button>
            <Button
              onClick={() => saveMutation.mutate()}
              disabled={!form.code.trim() || !form.name.trim() || saveMutation.isPending}
            >
              {saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              {editingId ? "保存修改" : "创建套餐"}
            </Button>
          </div>
        </div>
      ) : null}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : plans.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无套餐</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">Code</th>
                <th className="px-4 py-3 font-medium">算力/期</th>
                <th className="px-4 py-3 font-medium">存储</th>
                <th className="px-4 py-3 font-medium">账期</th>
                <th className="px-4 py-3 font-medium">标价</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {plans.map((plan: AdminSubscriptionPlan) => (
                <tr key={plan.id} className="border-b border-border/60 last:border-b-0">
                  <td className="px-4 py-3">
                    <p className="font-medium">{plan.name}</p>
                    {plan.description ? (
                      <p className="text-xs text-muted-foreground">{plan.description}</p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{plan.code}</td>
                  <td className="px-4 py-3 tabular-nums">{plan.monthlyCredits}</td>
                  <td className="px-4 py-3 tabular-nums">{plan.storageGb} GiB</td>
                  <td className="px-4 py-3 tabular-nums">{plan.periodDays} 天</td>
                  <td className="px-4 py-3">{formatPriceYuan(plan.priceCents)}</td>
                  <td className="px-4 py-3">
                    {plan.isActive ? <Badge>上架</Badge> : <Badge variant="secondary">下架</Badge>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={toggleMutation.isPending}
                        onClick={() =>
                          toggleMutation.mutate({ id: plan.id, isActive: !plan.isActive })
                        }
                      >
                        {plan.isActive ? "下架" : "上架"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => startEdit(plan)}
                      >
                        <Pencil className="mr-1 h-3.5 w-3.5" />
                        编辑
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        className="text-destructive hover:text-destructive"
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                          if (window.confirm(`确定删除套餐「${plan.name}」？此操作不可恢复。`)) {
                            deleteMutation.mutate(plan.id);
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
