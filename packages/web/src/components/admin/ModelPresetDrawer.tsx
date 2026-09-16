"use client";

import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { patchAdminModel, resetAdminModelPresets } from "@/lib/api/admin";
import type { AdminModel } from "@/types/admin";
import type { GenerationPresetsConfig } from "@/types/generationPresets";

interface ModelPresetDrawerProps {
  model: AdminModel | null;
  open: boolean;
  onClose: () => void;
}

export function ModelPresetDrawer({ model, open, onClose }: ModelPresetDrawerProps) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<GenerationPresetsConfig | null>(null);

  useEffect(() => {
    if (!model || !open) {
      setDraft(null);
      return;
    }
    const presets = model.parameters?.generationPresets as GenerationPresetsConfig | undefined;
    setDraft(presets ? structuredClone(presets) : null);
  }, [model, open]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!model || !draft) throw new Error("No presets");
      return patchAdminModel(model.id, { generationPresets: draft as unknown as Record<string, unknown> });
    },
    onSuccess: () => {
      toast.success("预设已保存");
      queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
      onClose();
    },
    onError: () => toast.error("保存预设失败"),
  });

  const resetMutation = useMutation({
    mutationFn: () => {
      if (!model) throw new Error("No model");
      return resetAdminModelPresets(model.id);
    },
    onSuccess: (updated) => {
      const presets = updated.parameters?.generationPresets as GenerationPresetsConfig | undefined;
      setDraft(presets ? structuredClone(presets) : null);
      toast.success("已恢复默认预设");
      queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: () => toast.error("恢复默认失败"),
  });

  if (!open || !model) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-lg flex-col border-l border-border bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold">生成预设 · {model.displayName}</h2>
            <p className="text-xs text-muted-foreground font-mono">{model.name}</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {!draft ? (
            <p className="text-sm text-muted-foreground">
              该模型暂无生成预设（仅图片类模型支持）。可点击「恢复默认」尝试写入模板。
            </p>
          ) : (
            <div className="space-y-6">
              {draft.groups.map((group, gi) => (
                <section key={group.id} className="space-y-3">
                  <h3 className="text-sm font-medium">{group.label}</h3>
                  <div className="space-y-2">
                    {group.items.map((item, ii) => (
                      <div
                        key={item.id}
                        className="rounded-lg border border-border p-3 space-y-2"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <Input
                            value={item.label}
                            onChange={(e) => {
                              const next = structuredClone(draft);
                              next.groups[gi].items[ii].label = e.target.value;
                              setDraft(next);
                            }}
                            className="h-8 text-sm"
                          />
                          <Switch
                            checked={item.enabled !== false}
                            onCheckedChange={(checked) => {
                              const next = structuredClone(draft);
                              next.groups[gi].items[ii].enabled = checked;
                              setDraft(next);
                            }}
                          />
                        </div>
                        {item.promptSuffix !== undefined ? (
                          <Input
                            placeholder="promptSuffix"
                            value={item.promptSuffix}
                            onChange={(e) => {
                              const next = structuredClone(draft);
                              next.groups[gi].items[ii].promptSuffix = e.target.value;
                              setDraft(next);
                            }}
                            className="h-8 text-xs"
                          />
                        ) : null}
                        {item.api?.size !== undefined ? (
                          <Input
                            placeholder="api.size"
                            value={String(item.api.size ?? "")}
                            onChange={(e) => {
                              const next = structuredClone(draft);
                              const api = { ...(next.groups[gi].items[ii].api ?? {}) };
                              api.size = e.target.value;
                              next.groups[gi].items[ii].api = api;
                              setDraft(next);
                            }}
                            className="h-8 text-xs font-mono"
                          />
                        ) : null}
                        <p className="text-[10px] text-muted-foreground font-mono">id: {item.id}</p>
                      </div>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-border px-4 py-3">
          <Button
            variant="outline"
            onClick={() => resetMutation.mutate()}
            disabled={resetMutation.isPending}
          >
            恢复默认
          </Button>
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={!draft || saveMutation.isPending}
          >
            保存预设
          </Button>
        </div>
      </div>
    </div>
  );
}
