"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  getAdminModelUiSeries,
  listAdminModels,
  putAdminModelUiSeries,
  type AdminModelUiSeriesItem,
} from "@/lib/api/admin";
import {
  NODE_MODEL_CATEGORIES,
  type NodeModelCategoryId,
} from "@/lib/admin/modelCategories";
import { resolveModelSeries } from "@/lib/canvas/modelSeries";
import { cn } from "@/lib/utils";

/** 空 categories = 全类目；否则须包含当前 Tab */
function matchesCategory(categories: string[], category: string): boolean {
  if (!categories.length) return true;
  return categories.includes(category);
}

/**
 * 模型开关页「系列展示顺序」：可折叠；列表随顶部类目 Tab 筛选与排序。
 */
export function ModelUiSeriesPanel({ category }: { category: NodeModelCategoryId }) {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin", "models", "ui-series"],
    queryFn: getAdminModelUiSeries,
    staleTime: 15_000,
  });

  const [draft, setDraft] = useState<AdminModelUiSeriesItem[]>([]);
  const [dirty, setDirty] = useState(false);
  // 默认折叠（系列项多，避免刷屏）
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!data || dirty) return;
    setDraft(
      (data.series ?? []).map((s) => ({
        ...s,
        categories: [...(s.categories || [])],
      }))
    );
  }, [data, dirty]);

  const categoryLabel =
    NODE_MODEL_CATEGORIES.find((c) => c.id === category)?.label ?? category;

  const filteredSorted = useMemo(() => {
    return [...draft]
      .filter((s) => matchesCategory(s.categories, category))
      .sort(
        (a, b) => a.sortOrder - b.sortOrder || a.label.localeCompare(b.label, "zh")
      );
  }, [draft, category]);

  const saveMutation = useMutation({
    mutationFn: () =>
      putAdminModelUiSeries({
        version: data?.version,
        series: draft.map((s) => ({
          label: s.label.trim(),
          sortOrder: s.sortOrder,
          enabled: s.enabled,
          categories: s.categories,
        })),
      }),
    onSuccess: () => {
      toast.success("系列顺序已保存");
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["admin", "models", "ui-series"] });
      queryClient.invalidateQueries({ queryKey: ["models", "ui-series"] });
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const syncMutation = useMutation({
    mutationFn: async () => {
      const models = await listAdminModels({});
      const byLabel = new Map<string, Set<string>>();
      for (const m of models) {
        // 仅同步当前类目下的模型系列，避免一次塞满全站
        if (m.category !== category) continue;
        const label = resolveModelSeries({
          name: m.name,
          displayName: m.displayName,
          providerGroup: m.providerGroup,
          parameters: m.parameters as Record<string, unknown> | undefined,
        }).trim();
        if (!label || label === "其他") continue;
        const cats = byLabel.get(label) ?? new Set<string>();
        if (m.category) cats.add(m.category);
        byLabel.set(label, cats);
      }
      return byLabel;
    },
    onSuccess: (byLabel) => {
      setDirty(true);
      setExpanded(true);
      const existingLabels = new Set(draft.map((s) => s.label));
      let added = 0;
      for (const label of byLabel.keys()) {
        if (!existingLabels.has(label)) added += 1;
      }
      setDraft((prev) => {
        const existing = new Map(prev.map((s) => [s.label, s]));
        const next = [...prev];
        let maxOrder = prev.reduce((m, s) => Math.max(m, s.sortOrder), 0);
        const discovered = [...byLabel.keys()].sort((a, b) => a.localeCompare(b, "zh"));
        for (const label of discovered) {
          const hit = existing.get(label);
          if (hit) {
            if (category !== "tool" && !hit.categories.includes(category)) {
              const idx = next.findIndex((s) => s.label === label);
              if (idx >= 0) {
                const cur = next[idx]!;
                if (cur.categories.length > 0) {
                  next[idx] = {
                    ...cur,
                    categories: [...cur.categories, category],
                  };
                }
              }
            }
            continue;
          }
          maxOrder += 10;
          next.push({
            label,
            sortOrder: maxOrder,
            enabled: true,
            categories: category === "tool" ? [] : [category],
          });
        }
        return next;
      });
      toast.success(
        added > 0
          ? `已同步「${categoryLabel}」：新增 ${added} 个系列`
          : `「${categoryLabel}」系列已是最新（发现 ${byLabel.size} 个）`
      );
    },
    onError: (err: Error) => toast.error(err.message || "同步失败"),
  });

  const updateItem = (label: string, patch: Partial<AdminModelUiSeriesItem>) => {
    setDirty(true);
    setDraft((prev) =>
      prev.map((s) => {
        if (s.label !== label) return s;
        const next = { ...s, ...patch };
        if (patch.label != null) {
          const trimmed = patch.label.trim();
          if (!trimmed) return s;
          if (trimmed !== label && prev.some((x) => x.label === trimmed)) {
            toast.error("系列名已存在");
            return s;
          }
          next.label = trimmed;
        }
        return next;
      })
    );
  };

  const removeItem = (label: string) => {
    setDirty(true);
    setDraft((prev) => prev.filter((s) => s.label !== label));
  };

  const addItem = () => {
    setDirty(true);
    setExpanded(true);
    setDraft((prev) => {
      let name = "新系列";
      let i = 1;
      const labels = new Set(prev.map((s) => s.label));
      while (labels.has(name)) {
        i += 1;
        name = `新系列${i}`;
      }
      return [
        ...prev,
        {
          label: name,
          sortOrder: prev.reduce((m, s) => Math.max(m, s.sortOrder), 0) + 10,
          enabled: true,
          categories: category === "tool" ? [] : [category],
        },
      ];
    });
  };

  /** 仅在当前类目可见列表内上下移动 */
  const moveItem = (label: string, dir: -1 | 1) => {
    setDirty(true);
    setDraft((prev) => {
      const visible = [...prev]
        .filter((s) => matchesCategory(s.categories, category))
        .sort(
          (a, b) => a.sortOrder - b.sortOrder || a.label.localeCompare(b.label, "zh")
        );
      const idx = visible.findIndex((s) => s.label === label);
      const swapIdx = idx + dir;
      if (idx < 0 || swapIdx < 0 || swapIdx >= visible.length) return prev;
      const a = visible[idx]!;
      const b = visible[swapIdx]!;
      const orderA = a.sortOrder;
      const orderB = b.sortOrder;
      return prev.map((s) => {
        if (s.label === a.label) {
          return { ...s, sortOrder: orderB === orderA ? orderA + dir : orderB };
        }
        if (s.label === b.label) return { ...s, sortOrder: orderA };
        return s;
      });
    });
  };

  const toggleCategory = (label: string, categoryId: string) => {
    setDirty(true);
    setDraft((prev) =>
      prev.map((s) => {
        if (s.label !== label) return s;
        const has = s.categories.includes(categoryId);
        return {
          ...s,
          categories: has
            ? s.categories.filter((c) => c !== categoryId)
            : [...s.categories, categoryId],
        };
      })
    );
  };

  return (
    <section className="mb-4 rounded-xl border border-border bg-card/40">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex min-w-0 flex-1 items-start gap-2 text-left"
        >
          <span className="mt-0.5 shrink-0 text-muted-foreground">
            {expanded ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </span>
          <span className="min-w-0">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold">系列展示顺序</span>
              <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {categoryLabel} · {filteredSorted.length}
              </span>
              {dirty ? (
                <span className="text-[11px] text-amber-600">未保存</span>
              ) : null}
            </span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              随上方类目 Tab 筛选与排序；「从模型同步」只拉当前类目。点击标题折叠/展开。
            </span>
          </span>
        </button>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={syncMutation.isPending}
            onClick={() => syncMutation.mutate()}
          >
            {syncMutation.isPending ? "同步中…" : "从模型同步"}
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={addItem}>
            新建系列
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!dirty || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? "保存中…" : "保存顺序"}
          </Button>
        </div>
      </div>

      {expanded ? (
        <div className="border-t border-border px-4 py-3">
          {isLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : isError ? (
            <p className="text-sm text-destructive">加载系列顺序失败</p>
          ) : filteredSorted.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
              当前「{categoryLabel}」下暂无系列。可点「从模型同步」或「新建系列」。
            </p>
          ) : (
            <div className="space-y-2">
              {filteredSorted.map((item, index) => (
                <div
                  key={item.label}
                  className="grid gap-3 rounded-lg border border-border/80 bg-background/60 p-3 md:grid-cols-[auto_1fr_auto]"
                >
                  <div className="flex flex-col gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-8"
                      disabled={index === 0}
                      onClick={() => moveItem(item.label, -1)}
                      title="上移"
                    >
                      <ArrowUp className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-8"
                      disabled={index === filteredSorted.length - 1}
                      onClick={() => moveItem(item.label, 1)}
                      title="下移"
                    >
                      <ArrowDown className="size-4" />
                    </Button>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div className="space-y-1">
                      <Label className="text-xs">系列名（与画布一致）</Label>
                      <Input
                        value={item.label}
                        onChange={(e) => updateItem(item.label, { label: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">排序值</Label>
                      <Input
                        type="number"
                        value={item.sortOrder}
                        onChange={(e) =>
                          updateItem(item.label, { sortOrder: Number(e.target.value) || 0 })
                        }
                      />
                    </div>
                    <div className="flex items-end gap-2 pb-1 sm:col-span-2">
                      <Switch
                        checked={item.enabled}
                        onCheckedChange={(checked) =>
                          updateItem(item.label, { enabled: Boolean(checked) })
                        }
                      />
                      <span className="text-xs text-muted-foreground">
                        {item.enabled ? "参与排序" : "停用（回退拼音序）"}
                      </span>
                    </div>
                    <div className="sm:col-span-2">
                      <Label className="text-xs">适用类目（可多选，空=全部）</Label>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {NODE_MODEL_CATEGORIES.filter((c) => c.id !== "tool").map((cat) => {
                          const active = item.categories.includes(cat.id);
                          return (
                            <button
                              key={cat.id}
                              type="button"
                              onClick={() => toggleCategory(item.label, cat.id)}
                              className={cn(
                                "rounded-md border px-2 py-1 text-xs transition-colors",
                                active
                                  ? "border-primary/40 bg-primary/15 text-primary"
                                  : "border-border text-muted-foreground hover:bg-muted"
                              )}
                            >
                              {cat.label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-start justify-end">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => removeItem(item.label)}
                    >
                      删除
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
