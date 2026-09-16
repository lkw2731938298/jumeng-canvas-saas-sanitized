"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { NODE_MODEL_CATEGORIES, PROVIDER_GROUP_ORDER, type NodeModelCategoryId } from "@/lib/admin/modelCategories";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { getAdminModelUiTags, listAdminModels, patchAdminModel } from "@/lib/api/admin";
import type { AdminModel } from "@/types/admin";
import { ModelPresetDrawer } from "@/components/admin/ModelPresetDrawer";
import { ModelAdminDrawer } from "@/components/admin/ModelAdminDrawer";
import { ModelUiTagsPanel } from "@/components/admin/ModelUiTagsPanel";
import { ModelUiSeriesPanel } from "@/components/admin/ModelUiSeriesPanel";
import { CanvasToolModelSwitchPanel } from "@/components/admin/CanvasToolModelSwitchPanel";
import { cn } from "@/lib/utils";

function StatusBadge({ model }: { model: AdminModel }) {
  if (!model.isConfigured) {
    return <Badge variant="outline" className="text-amber-600">缺密钥</Badge>;
  }
  if (!model.isImplemented) {
    return <Badge variant="outline" className="text-muted-foreground">未接入</Badge>;
  }
  if (!model.isAvailable) {
    return <Badge variant="secondary">已停用</Badge>;
  }
  return <Badge className="bg-emerald-600/90 hover:bg-emerald-600/90">可用</Badge>;
}

function ModelTable({
  models,
  tagLabelById,
  onToggle,
  toggling,
  onEditPresets,
  onEditModel,
}: {
  models: AdminModel[];
  tagLabelById: Map<string, string>;
  onToggle: (id: string, isAvailable: boolean) => void;
  toggling: boolean;
  onEditPresets: (model: AdminModel) => void;
  onEditModel: (model: AdminModel) => void;
}) {
  const grouped = useMemo(() => {
    const map = new Map<string, AdminModel[]>();
    for (const m of models) {
      const key = m.providerGroup || m.provider || "其他";
      const list = map.get(key) ?? [];
      list.push(m);
      map.set(key, list);
    }
    const order = [...PROVIDER_GROUP_ORDER];
    const keys = [...map.keys()].sort((a, b) => {
      const ia = order.indexOf(a as (typeof order)[number]);
      const ib = order.indexOf(b as (typeof order)[number]);
      if (ia === -1 && ib === -1) return a.localeCompare(b);
      if (ia === -1) return 1;
      if (ib === -1) return -1;
      return ia - ib;
    });
    return keys.map((k) => ({ group: k, items: map.get(k) ?? [] }));
  }, [models]);

  if (!models.length) {
    return (
      <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
        该分类下暂无模型
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {grouped.map(({ group, items }) => (
        <section key={group}>
          <h3 className="mb-2 text-sm font-medium text-foreground">{group}</h3>
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full min-w-[880px] text-left text-sm">
              <thead className="border-b border-border bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-4 py-3 font-medium">启用</th>
                  <th className="px-4 py-3 font-medium">状态</th>
                  <th className="px-4 py-3 font-medium">显示名称</th>
                  <th className="px-4 py-3 font-medium">标识</th>
                  <th className="px-4 py-3 font-medium">类型</th>
                  <th className="px-4 py-3 font-medium">排序</th>
                  <th className="px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((model) => (
                  <tr key={model.id} className="border-b border-border/60 hover:bg-muted/20">
                    <td className="px-4 py-3">
                      <Switch
                        checked={model.isAvailable}
                        disabled={toggling || !model.isImplemented}
                        onCheckedChange={(checked) => onToggle(model.id, Boolean(checked))}
                      />
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge model={model} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium">{model.displayName}</div>
                      {model.description ? (
                        <div className="mt-0.5 text-xs text-muted-foreground">{model.description}</div>
                      ) : null}
                      {(() => {
                        const raw = model.parameters?.uiTagIds;
                        const ids = Array.isArray(raw)
                          ? raw.map((x) => String(x || "").trim()).filter(Boolean)
                          : [];
                        if (!ids.length) return null;
                        return (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {ids.map((id) => (
                              <Badge key={id} variant="outline" className="text-[10px] font-normal">
                                {tagLabelById.get(id) || id}
                              </Badge>
                            ))}
                          </div>
                        );
                      })()}
                      {Array.isArray(model.parameters?.channels) &&
                      (model.parameters.channels as unknown[]).some(
                        (c) =>
                          c &&
                          typeof c === "object" &&
                          String((c as { role?: string }).role) === "fallback" &&
                          (c as { enabled?: boolean }).enabled !== false
                      ) ? (
                        <div className="mt-0.5 text-[11px] text-muted-foreground">含副通道托底</div>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{model.name}</td>
                    <td className="px-4 py-3 text-muted-foreground">{model.modelType}</td>
                    <td className="px-4 py-3 tabular-nums text-muted-foreground">{model.sortOrder}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        <Button variant="outline" size="sm" onClick={() => onEditModel(model)}>
                          编辑
                        </Button>
                        {model.category === "image" || model.category === "tool" ? (
                          <Button variant="outline" size="sm" onClick={() => onEditPresets(model)}>
                            预设
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

export default function AdminModelsPage() {
  const queryClient = useQueryClient();
  const [activeCategory, setActiveCategory] = useState<NodeModelCategoryId>("text");
  const [search, setSearch] = useState("");
  const [presetModel, setPresetModel] = useState<AdminModel | null>(null);
  const [editModel, setEditModel] = useState<AdminModel | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["admin", "models", search],
    queryFn: () => listAdminModels({ search: search || undefined }),
  });

  const { data: uiTagsData } = useQuery({
    queryKey: ["admin", "models", "ui-tags"],
    queryFn: getAdminModelUiTags,
    staleTime: 30_000,
  });

  const tagLabelById = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of uiTagsData?.tags ?? []) {
      map.set(t.id, t.label);
    }
    return map;
  }, [uiTagsData?.tags]);

  const toggleMutation = useMutation({
    mutationFn: ({ id, isAvailable }: { id: string; isAvailable: boolean }) =>
      patchAdminModel(id, { isAvailable }),
    onMutate: async ({ id, isAvailable }) => {
      await queryClient.cancelQueries({ queryKey: ["admin", "models"] });
      const previous = queryClient.getQueriesData({ queryKey: ["admin", "models"] });
      // 仅更新「模型列表」数组缓存；同前缀还有 ui-tags / all-for-tools 等，勿对对象调 .map
      queryClient.setQueriesData({ queryKey: ["admin", "models"] }, (old) => {
        if (!Array.isArray(old)) return old;
        return old.map((m: AdminModel) => (m.id === id ? { ...m, isAvailable } : m));
      });
      return { previous };
    },
    onError: (err, _vars, context) => {
      context?.previous?.forEach(([key, value]) => {
        queryClient.setQueryData(key, value);
      });
      const detail = err instanceof Error && err.message ? err.message : "更新模型状态失败";
      toast.error(detail);
    },
    onSuccess: (updated) => {
      toast.success(`${updated.displayName} 已${updated.isAvailable ? "启用" : "停用"}`);
    },
    onSettled: () => {
      // 只刷新列表与画布模型，避免与 ui-tags 乐观更新纠缠
      queryClient.invalidateQueries({
        queryKey: ["admin", "models"],
        predicate: (q) => q.queryKey[2] !== "ui-tags" && q.queryKey[2] !== "ui-series",
      });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });

  const activeMeta = NODE_MODEL_CATEGORIES.find((c) => c.id === activeCategory);
  const categoryModels = (data ?? []).filter((m) => m.category === activeCategory);
  const enabledCount = categoryModels.filter((m) => m.isAvailable && m.isImplemented).length;

  return (
    <>
      <AdminHeader
        title="模型开关"
        description="按能力分类管理模型；仅「可用」且已接入上游的模型会在画布节点中展示"
      />

      <div className="mb-6 flex flex-wrap gap-2 border-b border-border pb-1">
        {NODE_MODEL_CATEGORIES.map((cat) => {
          const count = data?.filter((m) => m.category === cat.id).length ?? 0;
          const enabled = data?.filter((m) => m.category === cat.id && m.isAvailable && m.isImplemented).length ?? 0;
          return (
            <button
              key={cat.id}
              type="button"
              onClick={() => setActiveCategory(cat.id)}
              className={cn(
                "rounded-t-lg px-4 py-2 text-sm transition-colors",
                activeCategory === cat.id
                  ? "bg-primary/15 font-medium text-primary"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              )}
            >
              {cat.label}
              <span className="ml-1.5 text-xs opacity-70">
                {enabled}/{count}
              </span>
            </button>
          );
        })}
      </div>

      <ModelUiTagsPanel category={activeCategory} />
      <ModelUiSeriesPanel category={activeCategory} />

      {activeMeta ? (
        <p className="mb-4 text-sm text-muted-foreground">{activeMeta.description}</p>
      ) : null}

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">搜索</label>
          <Input
            className="w-56"
            placeholder="名称 / 显示名"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
          刷新
        </Button>
        <Button onClick={() => setCreateOpen(true)}>新建模型</Button>
        <p className="text-xs text-muted-foreground">
          当前分类已启用 {enabledCount} / {categoryModels.length} 个可接入模型
        </p>
      </div>

      {activeCategory === "tool" ? (
        <div className="mb-6">
          <CanvasToolModelSwitchPanel />
        </div>
      ) : null}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : isError ? (
        <p className="text-sm text-destructive">加载模型列表失败</p>
      ) : (
        <ModelTable
          models={categoryModels}
          tagLabelById={tagLabelById}
          toggling={toggleMutation.isPending}
          onToggle={(id, isAvailable) => toggleMutation.mutate({ id, isAvailable })}
          onEditPresets={setPresetModel}
          onEditModel={setEditModel}
        />
      )}

      <ModelPresetDrawer
        model={presetModel}
        open={!!presetModel}
        onClose={() => setPresetModel(null)}
      />

      <ModelAdminDrawer
        mode="create"
        model={null}
        open={createOpen}
        defaultCategory={activeCategory}
        catalogModels={data ?? []}
        onClose={() => setCreateOpen(false)}
        onSaved={() => {
          void queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
          void queryClient.invalidateQueries({ queryKey: ["models"] });
        }}
      />

      <ModelAdminDrawer
        mode="edit"
        model={editModel}
        open={!!editModel}
        catalogModels={data ?? []}
        onClose={() => setEditModel(null)}
        onSaved={() => {
          void queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
          void queryClient.invalidateQueries({ queryKey: ["models"] });
        }}
      />
    </>
  );
}
