"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  getAdminModelUiTags,
  putAdminModelUiTags,
  type AdminModelUiTag,
} from "@/lib/api/admin";
import {
  NODE_MODEL_CATEGORIES,
  type NodeModelCategoryId,
} from "@/lib/admin/modelCategories";
import { cn } from "@/lib/utils";

/** 生成标签 id（字母数字下划线短横线） */
function newTagId(): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 10)
      : Math.random().toString(36).slice(2, 12);
  return `tag_${rand}`;
}

/** 空 categories = 全类目；否则须包含当前 Tab */
function matchesCategory(categories: string[], category: string): boolean {
  if (!categories.length) return true;
  return categories.includes(category);
}

/**
 * 模型开关页「标签库」：可折叠；列表随顶部类目 Tab 筛选。
 */
export function ModelUiTagsPanel({ category }: { category: NodeModelCategoryId }) {
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin", "models", "ui-tags"],
    queryFn: getAdminModelUiTags,
    staleTime: 15_000,
  });

  const [draft, setDraft] = useState<AdminModelUiTag[]>([]);
  const [dirty, setDirty] = useState(false);
  // 默认折叠，避免占满页面
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!data || dirty) return;
    setDraft(
      (data.tags ?? []).map((t) => ({
        ...t,
        categories: [...(t.categories || [])],
      }))
    );
  }, [data, dirty]);

  const categoryLabel =
    NODE_MODEL_CATEGORIES.find((c) => c.id === category)?.label ?? category;

  const filteredSorted = useMemo(() => {
    return [...draft]
      .filter((t) => matchesCategory(t.categories, category))
      .sort((a, b) => a.sortOrder - b.sortOrder || a.label.localeCompare(b.label, "zh"));
  }, [draft, category]);

  const saveMutation = useMutation({
    mutationFn: () =>
      putAdminModelUiTags({
        version: data?.version,
        tags: draft.map((t) => ({
          id: t.id,
          label: t.label.trim(),
          sortOrder: t.sortOrder,
          enabled: t.enabled,
          categories: t.categories,
        })),
      }),
    onSuccess: () => {
      toast.success("标签库已保存");
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["admin", "models", "ui-tags"] });
      queryClient.invalidateQueries({ queryKey: ["models", "ui-tags"] });
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const updateTag = (id: string, patch: Partial<AdminModelUiTag>) => {
    setDirty(true);
    setDraft((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  };

  const removeTag = (id: string) => {
    setDirty(true);
    setDraft((prev) => prev.filter((t) => t.id !== id));
  };

  const addTag = () => {
    setDirty(true);
    setExpanded(true);
    setDraft((prev) => [
      ...prev,
      {
        id: newTagId(),
        label: "新标签",
        sortOrder: prev.reduce((m, t) => Math.max(m, t.sortOrder), 0) + 10,
        enabled: true,
        // 新建默认绑定当前类目 Tab
        categories: category === "tool" ? [] : [category],
      },
    ]);
  };

  const toggleCategory = (tagId: string, categoryId: string) => {
    setDirty(true);
    setDraft((prev) =>
      prev.map((t) => {
        if (t.id !== tagId) return t;
        const has = t.categories.includes(categoryId);
        return {
          ...t,
          categories: has
            ? t.categories.filter((c) => c !== categoryId)
            : [...t.categories, categoryId],
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
              <span className="text-sm font-semibold">模型 UI 标签库</span>
              <span className="rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {categoryLabel} · {filteredSorted.length}
              </span>
              {dirty ? (
                <span className="text-[11px] text-amber-600">未保存</span>
              ) : null}
            </span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              随上方类目 Tab 筛选；空类目=全类目可见。点击标题折叠/展开。
            </span>
          </span>
        </button>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={addTag}>
            新建标签
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!dirty || saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? "保存中…" : "保存标签库"}
          </Button>
        </div>
      </div>

      {expanded ? (
        <div className="border-t border-border px-4 py-3">
          {isLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : isError ? (
            <p className="text-sm text-destructive">加载标签库失败</p>
          ) : filteredSorted.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
              当前「{categoryLabel}」下暂无标签（含「全类目」项）。可点「新建标签」。
            </p>
          ) : (
            <div className="space-y-3">
              {filteredSorted.map((tag) => (
                <div
                  key={tag.id}
                  className="grid gap-3 rounded-lg border border-border/80 bg-background/60 p-3 md:grid-cols-[1fr_auto]"
                >
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div className="space-y-1">
                      <Label className="text-xs">显示名</Label>
                      <Input
                        value={tag.label}
                        onChange={(e) => updateTag(tag.id, { label: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">标识 id</Label>
                      <Input value={tag.id} disabled className="font-mono text-xs" />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">排序</Label>
                      <Input
                        type="number"
                        value={tag.sortOrder}
                        onChange={(e) =>
                          updateTag(tag.id, { sortOrder: Number(e.target.value) || 0 })
                        }
                      />
                    </div>
                    <div className="flex items-end gap-2 pb-1">
                      <Switch
                        checked={tag.enabled}
                        onCheckedChange={(checked) =>
                          updateTag(tag.id, { enabled: Boolean(checked) })
                        }
                      />
                      <span className="text-xs text-muted-foreground">
                        {tag.enabled ? "启用" : "停用"}
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-col gap-2">
                    <Label className="text-xs">适用类目（可多选，空=全部）</Label>
                    <div className="flex flex-wrap gap-1.5">
                      {NODE_MODEL_CATEGORIES.filter((c) => c.id !== "tool").map((cat) => {
                        const active = tag.categories.includes(cat.id);
                        return (
                          <button
                            key={cat.id}
                            type="button"
                            onClick={() => toggleCategory(tag.id, cat.id)}
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
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="self-end text-destructive"
                      onClick={() => removeTag(tag.id)}
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
