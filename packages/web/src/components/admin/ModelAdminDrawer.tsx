"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  createAdminModel,
  deleteAdminModel,
  getAdminModelUiTags,
  listAdminModelProviders,
  patchAdminModel,
  testAdminModel,
} from "@/lib/api/admin";
import { NODE_MODEL_CATEGORIES } from "@/lib/admin/modelCategories";
import type { AdminModel } from "@/types/admin";
import { cn } from "@/lib/utils";

const PROVIDERS = [
  "doubao",
  "ark",
  "deepseek",
  "dashscope",
  "kling",
  "vidu",
  "runninghub",
  "ltx_runninghub",
  "nodyhub",
  "huahu",
  "jumengai",
  "comfyui",
  "local",
  "openai",
  "qwen",
  "zhipu",
  "moonshot",
] as const;

type Mode = "create" | "edit";

type ChannelRole = "primary" | "fallback";

function readChannelModelName(
  params: Record<string, unknown> | undefined,
  role: ChannelRole,
  fallbackName: string
): string {
  const raw = params?.channels;
  if (!Array.isArray(raw)) return role === "primary" ? fallbackName : "";
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as Record<string, unknown>;
    if (String(row.role || "") !== role) continue;
    return String(row.modelName || row.model_name || "").trim();
  }
  return role === "primary" ? fallbackName : "";
}

function readFallbackEnabled(params: Record<string, unknown> | undefined): boolean {
  const raw = params?.channels;
  if (!Array.isArray(raw)) return false;
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as Record<string, unknown>;
    if (String(row.role || "") !== "fallback") continue;
    return row.enabled !== false && Boolean(String(row.modelName || row.model_name || "").trim());
  }
  return false;
}

function credentialStatusLabel(status?: string | null) {
  switch (status) {
    case "tested_ok":
      return "已测通";
    case "tested_fail":
      return "测通失败";
    case "configured":
      return "已配置";
    default:
      return "缺密钥";
  }
}

export function ModelAdminDrawer({
  mode,
  model,
  open,
  defaultCategory,
  catalogModels = [],
  onClose,
  onSaved,
}: {
  mode: Mode;
  model: AdminModel | null;
  open: boolean;
  defaultCategory?: string;
  /** 同系统目录模型，供主/副通道下拉选择 */
  catalogModels?: AdminModel[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [provider, setProvider] = useState("dashscope");
  const [category, setCategory] = useState(defaultCategory || "image");
  const [modelType, setModelType] = useState("checkpoint");
  const [description, setDescription] = useState("");
  const [sortOrder, setSortOrder] = useState("0");
  const [upstreamModel, setUpstreamModel] = useState("");
  const [implementation, setImplementation] = useState("reserved");
  const [capabilities, setCapabilities] = useState("");
  const [primaryModelName, setPrimaryModelName] = useState("");
  const [fallbackModelName, setFallbackModelName] = useState("");
  const [fallbackEnabled, setFallbackEnabled] = useState(false);
  const [uiTagIds, setUiTagIds] = useState<string[]>([]);
  /** 画布下拉左栏系列名（空则按名称启发式推断） */
  const [uiSeries, setUiSeries] = useState("");

  const selfName = mode === "edit" ? model?.name || "" : name.trim();

  const { data: providers } = useQuery({
    queryKey: ["admin", "model-providers"],
    queryFn: listAdminModelProviders,
    enabled: open,
    staleTime: 60_000,
  });

  const { data: uiTagsData } = useQuery({
    queryKey: ["admin", "models", "ui-tags"],
    queryFn: getAdminModelUiTags,
    enabled: open,
    staleTime: 30_000,
  });

  const tagChoices = useMemo(() => {
    const all = uiTagsData?.tags ?? [];
    return all.filter((t) => {
      if (!t.enabled) return false;
      const cats = t.categories || [];
      if (!cats.length) return true;
      return cats.includes(category);
    });
  }, [uiTagsData?.tags, category]);

  const providerStatusByCode = useMemo(() => {
    const map = new Map<string, string>();
    providers?.forEach((p) => map.set(p.code, p.status || (p.hasCredential ? "configured" : "unconfigured")));
    return map;
  }, [providers]);

  const channelOptions = useMemo(() => {
    const sameCategory = catalogModels.filter((m) => m.category === category);
    const hasSelf = selfName && sameCategory.some((m) => m.name === selfName);
    const options = sameCategory.map((m) => {
      const cred = providerStatusByCode.get(m.provider) || "unconfigured";
      return {
        name: m.name,
        label: `${m.displayName || m.name} (${m.name}) · ${m.provider} · ${credentialStatusLabel(cred)}`,
        credentialOk: cred === "tested_ok" || cred === "configured",
      };
    });
    if (selfName && !hasSelf) {
      const cred = providerStatusByCode.get(provider) || "unconfigured";
      options.unshift({
        name: selfName,
        label: `本模型 (${selfName || "…"}) · ${provider} · ${credentialStatusLabel(cred)}`,
        credentialOk: cred === "tested_ok" || cred === "configured",
      });
    }
    return options;
  }, [catalogModels, category, selfName, providerStatusByCode, provider]);

  useEffect(() => {
    if (mode === "edit" && model) {
      setDisplayName(model.displayName);
      setProvider(model.provider);
      setCategory(model.category);
      setModelType(model.modelType);
      setDescription(model.description || "");
      setSortOrder(String(model.sortOrder ?? 0));
      const params = model.parameters || {};
      setUpstreamModel(String(params.upstreamModel || ""));
      setImplementation(String(params.implementation || "live"));
      setCapabilities(Array.isArray(params.capabilities) ? params.capabilities.join(", ") : "");
      setPrimaryModelName(readChannelModelName(params, "primary", model.name));
      setFallbackModelName(readChannelModelName(params, "fallback", ""));
      setFallbackEnabled(readFallbackEnabled(params));
      const rawTags = params.uiTagIds;
      setUiTagIds(
        Array.isArray(rawTags)
          ? rawTags.map((x) => String(x || "").trim()).filter(Boolean)
          : []
      );
      setUiSeries(String(params.uiSeries || params.ui_series || "").trim());
    } else if (mode === "create") {
      setName("");
      setDisplayName("");
      setProvider("dashscope");
      setCategory(defaultCategory || "image");
      setModelType("checkpoint");
      setDescription("");
      setSortOrder("0");
      setUpstreamModel("");
      setImplementation("reserved");
      setCapabilities("");
      setPrimaryModelName("");
      setFallbackModelName("");
      setFallbackEnabled(false);
      setUiTagIds([]);
      setUiSeries("");
    }
  }, [mode, model, defaultCategory, open]);

  // 新建时主通道默认跟 name 走
  useEffect(() => {
    if (mode === "create" && name.trim() && !primaryModelName) {
      setPrimaryModelName(name.trim());
    }
  }, [mode, name, primaryModelName]);

  const buildChannels = () => {
    const catalog = (mode === "edit" ? model?.name : name.trim()) || "";
    const primary = (primaryModelName || catalog).trim() || catalog;
    const channels: Array<Record<string, unknown>> = [
      {
        role: "primary",
        modelName: primary,
        enabled: true,
        label: "主通道",
      },
    ];
    if (fallbackEnabled && fallbackModelName.trim()) {
      channels.push({
        role: "fallback",
        modelName: fallbackModelName.trim(),
        enabled: true,
        label: "副通道",
      });
    }
    return channels;
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const caps = capabilities
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const channels = buildChannels();
      // 通道所选模型若供应商缺密钥，仍允许保存，但提示运营先去配密钥
      for (const ch of channels) {
        const modelName = String(ch.modelName || "");
        const opt = channelOptions.find((o) => o.name === modelName);
        if (opt && !opt.credentialOk) {
          toast.message(`通道「${modelName}」对应供应商暂缺密钥，请到供应商密钥页配置`);
        }
      }
      if (mode === "create") {
        return createAdminModel({
          name,
          displayName: displayName || name,
          provider,
          modelType,
          category,
          description: description || undefined,
          sortOrder: Number(sortOrder) || 0,
          upstreamModel: upstreamModel || undefined,
          capabilities: caps.length ? caps : undefined,
          implementation,
          channels,
          parameters: { uiTagIds, uiSeries: uiSeries.trim() },
        });
      }
      if (!model) throw new Error("缺少模型");
      return patchAdminModel(model.id, {
        displayName,
        provider,
        modelType,
        category,
        description,
        sortOrder: Number(sortOrder) || 0,
        upstreamModel: upstreamModel || "",
        capabilities: caps,
        implementation,
        channels,
        parameters: {
          ...(model.parameters || {}),
          uiTagIds,
          uiSeries: uiSeries.trim(),
        },
      });
    },
    onSuccess: (saved) => {
      toast.success(mode === "create" ? `已创建 ${saved.displayName}` : "模型已更新");
      onSaved();
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const testMutation = useMutation({
    mutationFn: () => {
      if (!model) throw new Error("请先保存模型");
      return testAdminModel(model.id);
    },
    onSuccess: (res) => {
      if (res.ok) toast.success(res.message);
      else toast.error(res.message);
    },
    onError: (err: Error) => toast.error(err.message || "测试失败"),
  });

  const deleteMutation = useMutation({
    mutationFn: () => {
      if (!model) throw new Error("缺少模型");
      return deleteAdminModel(model.id);
    },
    onSuccess: () => {
      toast.success("模型已软删除（停用）");
      onSaved();
      onClose();
    },
    onError: (err: Error) => toast.error(err.message || "删除失败"),
  });

  if (!open) return null;
  if (mode === "edit" && !model) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-lg flex-col border-l border-border bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold">{mode === "create" ? "新建模型" : "编辑模型"}</h2>
            <p className="text-xs text-muted-foreground">
              name 创建后不可改；主/副通道引用目录模型，用户仍按本模型报价扣费
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          <div className="grid gap-4">
            {mode === "create" ? (
              <div className="space-y-1">
                <Label>模型标识 name</Label>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="my_custom_model"
                  className="font-mono"
                />
              </div>
            ) : (
              <div className="space-y-1">
                <Label>模型标识</Label>
                <Input value={model?.name || ""} disabled className="font-mono" />
              </div>
            )}
            <div className="space-y-1">
              <Label>显示名称</Label>
              <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>分类</Label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                >
                  {NODE_MODEL_CATEGORIES.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label>供应商</Label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  {PROVIDERS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label>model_type</Label>
                <Input value={modelType} onChange={(e) => setModelType(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label>排序</Label>
                <Input type="number" value={sortOrder} onChange={(e) => setSortOrder(e.target.value)} />
              </div>
            </div>
            <div className="space-y-1">
              <Label>描述</Label>
              <Input value={description} onChange={(e) => setDescription(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>画布系列名（下拉左栏）</Label>
              <Input
                value={uiSeries}
                onChange={(e) => setUiSeries(e.target.value)}
                placeholder="如 Seedance / HappyHorse；留空则自动推断"
              />
              <p className="text-[11px] text-muted-foreground">
                画布模型下拉按系列分组，悬停右侧出现该系列具体模型
              </p>
            </div>
            <div className="space-y-1.5">
              <Label>画布 UI 标签（可多选）</Label>
              <p className="text-[11px] text-muted-foreground">
                在模型开关页上方「标签库」维护；用于画布选模旁分类过滤与悬停菜单
              </p>
              {tagChoices.length === 0 ? (
                <p className="text-xs text-muted-foreground">当前类目暂无可用标签</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {tagChoices.map((tag) => {
                    const active = uiTagIds.includes(tag.id);
                    return (
                      <button
                        key={tag.id}
                        type="button"
                        onClick={() =>
                          setUiTagIds((prev) =>
                            active ? prev.filter((id) => id !== tag.id) : [...prev, tag.id]
                          )
                        }
                        className={cn(
                          "rounded-md border px-2.5 py-1 text-xs transition-colors",
                          active
                            ? "border-primary/40 bg-primary/15 text-primary"
                            : "border-border text-muted-foreground hover:bg-muted"
                        )}
                      >
                        {tag.label}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            <div className="space-y-1">
              <Label>upstreamModel</Label>
              <Input
                value={upstreamModel}
                onChange={(e) => setUpstreamModel(e.target.value)}
                placeholder="上游真实 model id"
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label>capabilities（逗号分隔）</Label>
              <Input
                value={capabilities}
                onChange={(e) => setCapabilities(e.target.value)}
                placeholder="text_to_image, image_to_image"
                className="font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label>implementation</Label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                value={implementation}
                onChange={(e) => setImplementation(e.target.value)}
              >
                <option value="live">live（可接入）</option>
                <option value="reserved">reserved（预留）</option>
              </select>
            </div>

            <div className="space-y-3 rounded-md border border-border p-3">
              <div>
                <p className="text-sm font-medium">上游切换通道</p>
                <p className="text-xs text-muted-foreground">
                  主通道失败且未拿到上游 task_id 时，立即用副通道再提交；任务列表可区分主/副
                </p>
              </div>
              <div className="space-y-1">
                <Label>主通道模型</Label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm font-mono text-xs"
                  value={primaryModelName || selfName}
                  onChange={(e) => setPrimaryModelName(e.target.value)}
                >
                  {channelOptions.map((opt) => (
                    <option key={`primary-${opt.name}`} value={opt.name}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <Label>启用副通道托底</Label>
                  <p className="text-xs text-muted-foreground">建议选稳定线路作为备用</p>
                </div>
                <Switch checked={fallbackEnabled} onCheckedChange={setFallbackEnabled} />
              </div>
              {fallbackEnabled ? (
                <div className="space-y-1">
                  <Label>副通道模型</Label>
                  <select
                    className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm font-mono text-xs"
                    value={fallbackModelName}
                    onChange={(e) => setFallbackModelName(e.target.value)}
                  >
                    <option value="">请选择稳定模型</option>
                    {channelOptions
                      .filter((opt) => opt.name !== (primaryModelName || selfName))
                      .map((opt) => (
                        <option key={`fallback-${opt.name}`} value={opt.name}>
                          {opt.label}
                        </option>
                      ))}
                  </select>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
          {mode === "edit" && model ? (
            <>
              <Button
                variant="outline"
                onClick={() => testMutation.mutate()}
                disabled={testMutation.isPending}
              >
                测试连通
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  if (window.confirm(`确认软删除模型「${model.displayName}」？`)) {
                    deleteMutation.mutate();
                  }
                }}
                disabled={deleteMutation.isPending}
              >
                软删除
              </Button>
            </>
          ) : null}
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            保存
          </Button>
        </div>
      </div>
    </div>
  );
}
