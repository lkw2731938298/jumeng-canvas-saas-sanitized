"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getAdminRegisterBonusSettings,
  listAdminRegisterBonusGrants,
  putAdminRegisterBonusSettings,
} from "@/lib/api/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";

function statusLabel(status: string) {
  if (status === "granted") return "已发放";
  if (status === "reserved") return "占位未完成";
  return status;
}

export default function AdminRegisterBonusPage() {
  const queryClient = useQueryClient();
  const [amountInput, setAmountInput] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);

  const { data: settings, isLoading: settingsLoading } = useQuery({
    queryKey: ["admin", "register-bonus"],
    queryFn: getAdminRegisterBonusSettings,
  });

  const { data: grants, isLoading: grantsLoading } = useQuery({
    queryKey: ["admin", "register-bonus-grants", page, search],
    queryFn: () => listAdminRegisterBonusGrants({ page, pageSize: 20, search }),
  });

  const saveMutation = useMutation({
    mutationFn: putAdminRegisterBonusSettings,
    onSuccess: (res) => {
      toast.success(
        res.enabled ? `已开启注册赠送，每次 ${res.amount} 点（每人仅一次）` : "已关闭注册赠送"
      );
      void queryClient.invalidateQueries({ queryKey: ["admin", "register-bonus"] });
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const amountValue =
    amountInput !== null ? amountInput : String(settings?.amount ?? 0);
  const totalPages = Math.max(1, Math.ceil((grants?.total || 0) / (grants?.pageSize || 20)));

  return (
    <div className="p-8">
      <AdminHeader
        title="注册赠送算力"
        description="新用户完成注册后自动发放；每人终身仅一次。关闭开关后新注册不再发放，已发放记录保留。"
      />

      <div className="mb-6 max-w-3xl space-y-4 rounded-xl border border-border bg-card p-6">
        {settingsLoading || !settings ? (
          <p className="text-sm text-muted-foreground">加载配置…</p>
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium">赠送开关</p>
                <p className="text-xs text-muted-foreground">
                  开启且点数大于 0 时，注册成功写入活动算力（有效期 30 天）
                </p>
              </div>
              <Button
                type="button"
                variant={settings.enabled ? "default" : "outline"}
                size="sm"
                disabled={saveMutation.isPending}
                onClick={() =>
                  saveMutation.mutate({
                    enabled: !settings.enabled,
                    amount: Math.max(0, Math.round(Number(amountValue) || 0)),
                  })
                }
              >
                {settings.enabled ? "当前：已开启" : "当前：已关闭"}
              </Button>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <label className="block">
                <span className="mb-1.5 block text-xs text-muted-foreground">每次赠送点数</span>
                <Input
                  type="number"
                  min={0}
                  max={1000000}
                  className="w-40"
                  value={amountValue}
                  onChange={(e) => setAmountInput(e.target.value)}
                />
              </label>
              <Button
                type="button"
                disabled={saveMutation.isPending}
                onClick={() =>
                  saveMutation.mutate({
                    enabled: Boolean(settings.enabled),
                    amount: Math.max(0, Math.round(Number(amountValue) || 0)),
                  })
                }
              >
                {saveMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : null}
                保存点数
              </Button>
            </div>
          </>
        )}
      </div>

      <div className="rounded-xl border border-border bg-card p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">发放记录</h2>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              setPage(1);
              setSearch(searchInput.trim());
            }}
          >
            <Input
              placeholder="搜索手机号 / 昵称"
              className="w-52"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
            />
            <Button type="submit" variant="outline" size="sm">
              搜索
            </Button>
          </form>
        </div>
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">用户</th>
                <th className="px-4 py-2 font-medium">手机号</th>
                <th className="px-4 py-2 font-medium">点数</th>
                <th className="px-4 py-2 font-medium">状态</th>
                <th className="px-4 py-2 font-medium">发放时间</th>
              </tr>
            </thead>
            <tbody>
              {grantsLoading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                    加载中…
                  </td>
                </tr>
              ) : !grants?.items.length ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                    暂无发放记录
                  </td>
                </tr>
              ) : (
                grants.items.map((row) => (
                  <tr key={row.id} className="border-t border-border/60">
                    <td className="px-4 py-3">{row.displayName || "—"}</td>
                    <td className="px-4 py-3 tabular-nums">{row.phone || row.phoneMasked || "—"}</td>
                    <td className="px-4 py-3 tabular-nums font-medium">{row.amount}</td>
                    <td className="px-4 py-3">
                      <Badge variant={row.status === "granted" ? "outline" : "secondary"}>
                        {statusLabel(row.status)}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {formatDateTimeCN(row.grantedAt || row.createdAt)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {grants ? (
          <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
            <span>
              共 {grants.total} 条 · 第 {grants.page} / {totalPages} 页
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
