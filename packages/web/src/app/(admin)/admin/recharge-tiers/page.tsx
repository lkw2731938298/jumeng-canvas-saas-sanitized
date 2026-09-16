"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getAdminRechargeTiers, putAdminRechargeTiers, type AdminRechargeTierItem } from "@/lib/api/admin";

type TierRow = AdminRechargeTierItem & { key: string };

function newRow(sortOrder: number): TierRow {
  return {
    key: `new-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    creditsAmount: 100,
    payAmountFen: 1000,
    sortOrder,
    isActive: true,
  };
}

function fenToYuanInput(fen: number) {
  return (fen / 100).toFixed(fen % 100 === 0 ? 0 : 2);
}

function yuanInputToFen(raw: string) {
  const n = Number(raw);
  if (!Number.isFinite(n) || n <= 0) return 0;
  return Math.round(n * 100);
}

export default function AdminRechargeTiersPage() {
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<TierRow[]>([]);
  const [yuanInputs, setYuanInputs] = useState<Record<string, string>>({});

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["admin", "recharge-tiers"],
    queryFn: getAdminRechargeTiers,
  });

  useEffect(() => {
    if (!data?.items) return;
    setRows(
      data.items.map((item, index) => ({
        ...item,
        key: `tier-${item.creditsAmount}-${index}`,
      }))
    );
    setYuanInputs(
      Object.fromEntries(
        data.items.map((item, index) => [
          `tier-${item.creditsAmount}-${index}`,
          fenToYuanInput(item.payAmountFen),
        ])
      )
    );
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: (items: AdminRechargeTierItem[]) => putAdminRechargeTiers(items),
    onSuccess: () => {
      toast.success("充值档位已保存");
      void queryClient.invalidateQueries({ queryKey: ["admin", "recharge-tiers"] });
      void queryClient.invalidateQueries({ queryKey: ["payments", "config"] });
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const updateRow = (key: string, patch: Partial<TierRow>) => {
    setRows((prev) => prev.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  };

  const removeRow = (key: string) => {
    setRows((prev) => prev.filter((row) => row.key !== key));
    setYuanInputs((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const addRow = () => {
    const nextSort = rows.length ? Math.max(...rows.map((r) => r.sortOrder)) + 10 : 10;
    const row = newRow(nextSort);
    setRows((prev) => [...prev, row]);
    setYuanInputs((prev) => ({ ...prev, [row.key]: fenToYuanInput(row.payAmountFen) }));
  };

  const handleSave = () => {
    const merged = rows.map((row) => ({
      ...row,
      payAmountFen: yuanInputToFen(yuanInputs[row.key] ?? fenToYuanInput(row.payAmountFen)),
    }));
    if (merged.some((r) => r.creditsAmount < 1 || r.payAmountFen < 1)) {
      toast.error("请填写有效的算力点数与售价");
      return;
    }
    const creditsSet = new Set<number>();
    for (const row of merged) {
      if (creditsSet.has(row.creditsAmount)) {
        toast.error(`算力档位 ${row.creditsAmount} 重复`);
        return;
      }
      creditsSet.add(row.creditsAmount);
    }
    if (!merged.some((r) => r.isActive)) {
      toast.error("至少保留一条启用的档位");
      return;
    }
    setRows(merged);
    saveMutation.mutate(
      merged.map(({ creditsAmount, payAmountFen, sortOrder, isActive }) => ({
        creditsAmount,
        payAmountFen,
        sortOrder,
        isActive,
      }))
    );
  };

  return (
    <div>
      <AdminHeader
        title="充值档位"
        description="配置用户充值可选的算力点数与支付宝售价（元）；保存后前台充值页即时生效。"
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          {isFetching ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          刷新
        </Button>
        <Button variant="outline" size="sm" onClick={addRow}>
          <Plus className="mr-2 h-4 w-4" />
          添加档位
        </Button>
        <Button size="sm" onClick={handleSave} disabled={saveMutation.isPending || isLoading}>
          {saveMutation.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          保存配置
        </Button>
        {data?.updatedAt ? (
          <span className="text-xs text-muted-foreground">上次更新：{data.updatedAt}</span>
        ) : null}
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">排序</th>
              <th className="px-4 py-3 font-medium">算力点数</th>
              <th className="px-4 py-3 font-medium">售价（元）</th>
              <th className="px-4 py-3 font-medium">启用</th>
              <th className="px-4 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-muted-foreground">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                </td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-10 text-center text-muted-foreground">
                  暂无档位，点击「添加档位」
                </td>
              </tr>
            ) : (
              [...rows]
                .sort((a, b) => a.sortOrder - b.sortOrder || a.creditsAmount - b.creditsAmount)
                .map((row) => (
                  <tr key={row.key} className="border-b border-border/60">
                    <td className="px-4 py-3">
                      <Input
                        type="number"
                        min={0}
                        className="w-20"
                        value={row.sortOrder}
                        onChange={(e) => updateRow(row.key, { sortOrder: Number(e.target.value) || 0 })}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <Input
                        type="number"
                        min={1}
                        className="w-28 font-mono"
                        value={row.creditsAmount}
                        onChange={(e) =>
                          updateRow(row.key, { creditsAmount: Math.max(1, Number(e.target.value) || 1) })
                        }
                      />
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1">
                        <span className="text-muted-foreground">¥</span>
                        <Input
                          type="number"
                          min={0.01}
                          step={0.01}
                          className="w-28 font-mono"
                          value={yuanInputs[row.key] ?? fenToYuanInput(row.payAmountFen)}
                          onChange={(e) =>
                            setYuanInputs((prev) => ({ ...prev, [row.key]: e.target.value }))
                          }
                        />
                      </div>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        入库 {yuanInputToFen(yuanInputs[row.key] ?? fenToYuanInput(row.payAmountFen))} 分
                      </p>
                    </td>
                    <td className="px-4 py-3">
                      <label className="inline-flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={row.isActive}
                          onChange={(e) => updateRow(row.key, { isActive: e.target.checked })}
                        />
                        {row.isActive ? "启用" : "停用"}
                      </label>
                    </td>
                    <td className="px-4 py-3">
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        onClick={() => removeRow(row.key)}
                        disabled={rows.length <= 1}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </td>
                  </tr>
                ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 rounded-lg border border-border bg-muted/20 p-4 text-xs text-muted-foreground space-y-1">
        <p>· 算力点数 = 用户到账的通用算力；售价 = 支付宝实际扣款金额（元）。</p>
        <p>· 停用档位不会在前台展示，但历史订单仍保留原档位信息。</p>
        <p>· 修改售价仅影响新订单，已创建的待支付订单仍按下单时价格结算。</p>
      </div>
    </div>
  );
}
