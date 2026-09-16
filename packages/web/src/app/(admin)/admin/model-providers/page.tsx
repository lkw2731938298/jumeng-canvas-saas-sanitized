"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, KeyRound, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  listAdminModelProviders,
  listAdminProviderCredentials,
  listAdminProviderReferencedModels,
  patchAdminModelProvider,
  patchAdminProviderCredential,
  putAdminProviderCredential,
  testAdminProviderCredential,
} from "@/lib/api/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import type { AdminModelProvider, AdminProviderUiField } from "@/types/admin";
import { cn } from "@/lib/utils";

function StatusBadge({ status }: { status?: string }) {
  if (status === "tested_ok") {
    return <Badge className="bg-emerald-600/90 hover:bg-emerald-600/90">已测通</Badge>;
  }
  if (status === "tested_fail") {
    return <Badge variant="destructive">测通失败</Badge>;
  }
  if (status === "configured") {
    return <Badge variant="outline" className="text-amber-700">已配置未测</Badge>;
  }
  return <Badge variant="outline" className="text-muted-foreground">未配置</Badge>;
}

function fieldVisible(field: AdminProviderUiField, showAdvanced: boolean) {
  if (field.advanced && !showAdvanced) return false;
  return true;
}

function ProviderDrawer({
  provider,
  open,
  onClose,
  onSaved,
}: {
  provider: AdminModelProvider | null;
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [profileKey, setProfileKey] = useState("default");
  const [apiKey, setApiKey] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [endpointId, setEndpointId] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [expertMode, setExpertMode] = useState(false);
  const [customProfile, setCustomProfile] = useState("");
  const [enabled, setEnabled] = useState(true);

  const ui = provider?.ui;
  const presetProfiles = ui?.profiles?.length
    ? ui.profiles
    : [{ key: "default", label: "主密钥", hint: "日常生成默认使用" }];

  const { data: credentials, isLoading: credLoading } = useQuery({
    queryKey: ["admin", "model-providers", provider?.code, "credentials"],
    queryFn: () => listAdminProviderCredentials(provider!.code),
    enabled: open && Boolean(provider?.code),
  });

  const { data: refModels, isLoading: modelsLoading } = useQuery({
    queryKey: ["admin", "model-providers", provider?.code, "models"],
    queryFn: () => listAdminProviderReferencedModels(provider!.code),
    enabled: open && Boolean(provider?.code),
  });

  const activeCred = credentials?.find((c) => c.profileKey === profileKey);
  const isEditing = Boolean(activeCred);

  useEffect(() => {
    if (!open || !provider) return;
    setProfileKey("default");
    setShowAdvanced(false);
    setExpertMode(false);
    setCustomProfile("");
    setEnabled(provider.isEnabled);
  }, [open, provider?.code]);

  useEffect(() => {
    if (!provider) return;
    if (activeCred) {
      setApiBase(activeCred.apiBase || provider.defaultApiBase || "");
      setEndpointId("");
      setApiKey("");
    } else {
      setApiBase(provider.defaultApiBase || "");
      setEndpointId("");
      setApiKey("");
    }
  }, [activeCred, profileKey, provider]);

  const saveMutation = useMutation({
    mutationFn: async (andTest: boolean) => {
      if (!provider) throw new Error("缺少供应商");
      const payload = {
        apiKey: apiKey.trim() || undefined,
        apiBase: apiBase || undefined,
        endpointId: endpointId || undefined,
      };
      if (isEditing) {
        await patchAdminProviderCredential(provider.code, profileKey, payload);
      } else {
        if (!payload.apiKey) throw new Error("新建凭证必须填写 API Key");
        await putAdminProviderCredential(provider.code, profileKey, payload);
      }
      if (andTest) {
        return testAdminProviderCredential(provider.code, profileKey);
      }
      return null;
    },
    onSuccess: (testRes) => {
      setApiKey("");
      if (testRes) {
        if (testRes.ok) toast.success(`已保存并测通：${testRes.message}`);
        else toast.error(`已保存，但测通失败：${testRes.message}`);
      } else {
        toast.success(`${provider?.displayName || "供应商"} 密钥已保存`);
      }
      onSaved();
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const testMutation = useMutation({
    mutationFn: () => {
      if (!provider) throw new Error("缺少供应商");
      return testAdminProviderCredential(provider.code, profileKey);
    },
    onSuccess: (res) => {
      if (res.ok) toast.success(res.message);
      else toast.error(res.message);
      onSaved();
    },
    onError: (err: Error) => toast.error(err.message || "测试失败"),
  });

  const toggleMutation = useMutation({
    mutationFn: (next: boolean) => {
      if (!provider) throw new Error("缺少供应商");
      return patchAdminModelProvider(provider.code, { isEnabled: next });
    },
    onSuccess: (row) => {
      setEnabled(row.isEnabled);
      toast.success(row.isEnabled ? "供应商已启用" : "供应商已停用");
      onSaved();
    },
    onError: (err: Error) => toast.error(err.message || "更新失败"),
  });

  const profileTabs = useMemo(() => {
    const keys = new Map<string, string>();
    for (const p of presetProfiles) {
      keys.set(p.key, p.label);
    }
    credentials?.forEach((c) => {
      if (!keys.has(c.profileKey)) {
        keys.set(c.profileKey, c.profileLabel || c.profileKey);
      }
    });
    return Array.from(keys.entries()).map(([key, label]) => ({ key, label }));
  }, [credentials, presetProfiles]);

  if (!open || !provider) return null;

  const fields = (ui?.fields || [
    { key: "apiKey", label: "API Key", required: true, secret: true },
    { key: "apiBase", label: "API 地址", advanced: true },
  ]) as AdminProviderUiField[];

  const canSave = isEditing ? true : Boolean(apiKey.trim());

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-lg flex-col border-l border-border bg-background shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold">{provider.displayName}</h2>
            <p className="mt-0.5 font-mono text-xs text-muted-foreground">{provider.code}</p>
            {ui?.summary ? (
              <p className="mt-1 text-xs text-muted-foreground">{ui.summary}</p>
            ) : null}
          </div>
          <Button variant="ghost" size="sm" onClick={onClose}>
            关闭
          </Button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={provider.status} />
            {provider.apiKeyHint ? (
              <span className="text-xs text-muted-foreground">尾号 …{provider.apiKeyHint}</span>
            ) : null}
            {ui?.docsUrl ? (
              <a
                href={ui.docsUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                文档 <ExternalLink className="h-3 w-3" />
              </a>
            ) : null}
          </div>

          <div className="flex items-center justify-between rounded-md border border-border px-3 py-2">
            <div>
              <p className="text-sm font-medium">启用供应商</p>
              <p className="text-xs text-muted-foreground">停用后依赖该密钥的模型将无法正常调用</p>
            </div>
            <Switch
              checked={enabled}
              disabled={toggleMutation.isPending}
              onCheckedChange={(v) => toggleMutation.mutate(Boolean(v))}
            />
          </div>

          <div className="space-y-2">
            <Label>密钥用途</Label>
            <div className="flex flex-wrap gap-1">
              {profileTabs.map((p) => (
                <Button
                  key={p.key}
                  type="button"
                  size="sm"
                  variant={profileKey === p.key ? "default" : "outline"}
                  onClick={() => setProfileKey(p.key)}
                >
                  {p.label}
                </Button>
              ))}
            </div>
            {presetProfiles.find((p) => p.key === profileKey)?.hint ? (
              <p className="text-xs text-muted-foreground">
                {presetProfiles.find((p) => p.key === profileKey)?.hint}
              </p>
            ) : null}
            {activeCred?.apiKeyHint ? (
              <p className="text-xs text-muted-foreground">当前密钥尾号：…{activeCred.apiKeyHint}</p>
            ) : (
              <p className="text-xs text-amber-700">该用途尚未配置密钥</p>
            )}
            {activeCred?.lastTestedAt ? (
              <p className="text-xs text-muted-foreground">
                最近测通：{activeCred.lastTestOk ? "成功" : "失败"} ·{" "}
                {formatDateTimeCN(activeCred.lastTestedAt)}
                {activeCred.lastTestMessage ? ` · ${activeCred.lastTestMessage}` : ""}
              </p>
            ) : null}
          </div>

          {credLoading ? (
            <p className="text-sm text-muted-foreground">加载凭证…</p>
          ) : (
            <form
              className="grid gap-3"
              autoComplete="off"
              onSubmit={(e) => e.preventDefault()}
            >
              {/* 吸收浏览器「账号/手机号」自动填充，避免灌进列表页搜索框 */}
              <input
                type="text"
                name="username"
                autoComplete="username"
                tabIndex={-1}
                aria-hidden
                className="pointer-events-none absolute h-0 w-0 opacity-0"
                defaultValue=""
              />
              {fields.filter((f) => fieldVisible(f, showAdvanced)).map((field) => {
                if (field.key === "apiKey") {
                  return (
                    <div key={field.key} className="space-y-1">
                      <Label>{field.label}{field.required ? " *" : ""}</Label>
                      <Input
                        type="password"
                        name="provider-api-key"
                        value={apiKey}
                        onChange={(e) => setApiKey(e.target.value)}
                        placeholder={
                          isEditing
                            ? "留空则保留原密钥"
                            : field.placeholder || "输入新密钥（保存后不明文展示）"
                        }
                        autoComplete="new-password"
                        data-1p-ignore
                        data-lpignore="true"
                        data-form-type="other"
                      />
                    </div>
                  );
                }
                if (field.key === "apiBase") {
                  return (
                    <div key={field.key} className="space-y-1">
                      <Label>{field.label}</Label>
                      <Input
                        name="provider-api-base"
                        value={apiBase}
                        onChange={(e) => setApiBase(e.target.value)}
                        placeholder={field.placeholder || provider.defaultApiBase || ""}
                        autoComplete="off"
                        data-1p-ignore
                        data-lpignore="true"
                      />
                    </div>
                  );
                }
                if (field.key === "endpointId") {
                  return (
                    <div key={field.key} className="space-y-1">
                      <Label>{field.label}</Label>
                      <Input
                        name="provider-endpoint-id"
                        value={endpointId}
                        onChange={(e) => setEndpointId(e.target.value)}
                        placeholder={activeCred?.endpointIdMasked || field.placeholder || "ep-xxxxxxxx"}
                        autoComplete="off"
                        data-1p-ignore
                        data-lpignore="true"
                      />
                      {field.hint ? (
                        <p className="text-xs text-muted-foreground">{field.hint}</p>
                      ) : null}
                    </div>
                  );
                }
                return null;
              })}

              {fields.some((f) => f.advanced) ? (
                <button
                  type="button"
                  className="text-left text-xs text-primary hover:underline"
                  onClick={() => setShowAdvanced((v) => !v)}
                >
                  {showAdvanced ? "收起高级选项" : "展开高级选项（API 地址等）"}
                </button>
              ) : null}

              <div className="rounded-md border border-dashed border-border p-3">
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <p className="text-xs font-medium">专家模式</p>
                    <p className="text-[11px] text-muted-foreground">自定义 profile 名称（一般无需）</p>
                  </div>
                  <Switch checked={expertMode} onCheckedChange={setExpertMode} />
                </div>
                {expertMode ? (
                  <div className="mt-2 flex gap-2">
                    <Input
                      className="font-mono text-xs"
                      value={customProfile}
                      onChange={(e) => setCustomProfile(e.target.value)}
                      placeholder="自定义 profile_key"
                      autoComplete="off"
                      data-1p-ignore
                      data-lpignore="true"
                    />
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        const k = customProfile.trim();
                        if (!k) return;
                        setProfileKey(k);
                      }}
                    >
                      使用
                    </Button>
                  </div>
                ) : null}
              </div>
            </form>
          )}

          <div className="space-y-2">
            <Label>引用本供应商的模型</Label>
            {modelsLoading ? (
              <p className="text-xs text-muted-foreground">加载中…</p>
            ) : !refModels?.length ? (
              <p className="text-xs text-muted-foreground">暂无目录模型引用此供应商</p>
            ) : (
              <ul className="max-h-40 space-y-1 overflow-y-auto rounded-md border border-border p-2 text-xs">
                {refModels.map((m) => (
                  <li key={m.id} className="flex items-center justify-between gap-2">
                    <span className="truncate">
                      {m.displayName}{" "}
                      <span className="font-mono text-muted-foreground">({m.name})</span>
                    </span>
                    <Badge variant={m.isAvailable ? "secondary" : "outline"} className="shrink-0">
                      {m.isAvailable ? "启用" : "停用"}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[11px] text-muted-foreground">
              主/副通道在「模型开关」中配置；此处仅展示引用关系。
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border px-4 py-3">
          <Button
            variant="outline"
            size="sm"
            disabled={testMutation.isPending || !isEditing}
            onClick={() => testMutation.mutate()}
          >
            {testMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            仅测试
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!canSave || saveMutation.isPending}
            onClick={() => saveMutation.mutate(false)}
          >
            保存
          </Button>
          <Button
            size="sm"
            disabled={!canSave || saveMutation.isPending}
            onClick={() => saveMutation.mutate(true)}
          >
            {saveMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
            保存并测试
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function AdminModelProvidersPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [active, setActive] = useState<AdminModelProvider | null>(null);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["admin", "model-providers"],
    queryFn: listAdminModelProviders,
  });

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return data ?? [];
    return (data ?? []).filter(
      (p) => p.code.toLowerCase().includes(q) || p.displayName.toLowerCase().includes(q)
    );
  }, [data, search]);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "model-providers"] });
    void queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
    void queryClient.invalidateQueries({ queryKey: ["models"] });
  };

  // 抽屉打开时随列表刷新同步状态徽标
  useEffect(() => {
    if (!active || !data) return;
    const fresh = data.find((p) => p.code === active.code);
    if (!fresh) return;
    if (
      fresh.status !== active.status ||
      fresh.hasCredential !== active.hasCredential ||
      fresh.apiKeyHint !== active.apiKeyHint ||
      fresh.lastTestedAt !== active.lastTestedAt ||
      fresh.isEnabled !== active.isEnabled ||
      fresh.modelCount !== active.modelCount
    ) {
      setActive(fresh);
    }
  }, [data, active]);

  // 打开抽屉瞬间若浏览器把手机号灌进搜索框，清空无效过滤（延迟再扫一次，覆盖异步自动填充）
  useEffect(() => {
    if (!active) return;
    const clearIfPhoneAutofill = () => {
      setSearch((prev) => {
        const q = prev.trim();
        if (/^1\d{10}$/.test(q)) return "";
        return prev;
      });
    };
    clearIfPhoneAutofill();
    const t1 = window.setTimeout(clearIfPhoneAutofill, 80);
    const t2 = window.setTimeout(clearIfPhoneAutofill, 400);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, [active?.code]);

  // 抽屉打开期间搜索框被再次自动填充成手机号时立即清掉
  useEffect(() => {
    if (!active) return;
    if (/^1\d{10}$/.test(search.trim())) {
      setSearch("");
    }
  }, [active, search]);

  const configuredCount = filtered.filter((p) => p.hasCredential).length;
  const testedCount = filtered.filter((p) => p.status === "tested_ok").length;

  return (
    <>
      <AdminHeader
        title="供应商密钥"
        description="加密保存上游 API Key。日常只需打开卡片配置主密钥并「保存并测试」；高级 Profile 与 API 地址在抽屉内展开。"
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label className="text-xs text-muted-foreground">搜索</Label>
          <Input
            className="w-56"
            placeholder="供应商名称 / code"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            // 打开密钥抽屉时浏览器常把登录手机号灌进此框，需显式关闭自动填充
            name="provider-filter-q"
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            data-1p-ignore
            data-lpignore="true"
            data-form-type="other"
          />
        </div>
        <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={cn("mr-2 h-4 w-4", isFetching && "animate-spin")} />
          刷新
        </Button>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : isError ? (
        <p className="text-sm text-destructive">加载供应商列表失败</p>
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <KeyRound className="h-4 w-4" />
              共 {filtered.length} 家
            </span>
            <span>已配置 {configuredCount}</span>
            <span>已测通 {testedCount}</span>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map((provider) => (
              <button
                key={provider.code}
                type="button"
                onClick={() => setActive(provider)}
                className={cn(
                  "rounded-xl border border-border bg-card p-4 text-left shadow-sm transition-colors",
                  "hover:border-primary/40 hover:bg-muted/30",
                  !provider.isEnabled && "opacity-70"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="truncate text-sm font-semibold">{provider.displayName}</h3>
                    <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">{provider.code}</p>
                  </div>
                  <StatusBadge status={provider.status} />
                </div>
                <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                  <p>
                    密钥：
                    {provider.apiKeyHint ? `尾号 …${provider.apiKeyHint}` : "未配置"}
                    {!provider.isEnabled ? " · 已停用" : ""}
                  </p>
                  <p>引用模型：{provider.modelCount ?? 0} 个</p>
                  {provider.lastTestedAt ? (
                    <p>
                      测通：{formatDateTimeCN(provider.lastTestedAt)}
                      {provider.lastTestOk === false && provider.lastTestMessage
                        ? ` · ${provider.lastTestMessage}`
                        : ""}
                    </p>
                  ) : (
                    <p>测通：—</p>
                  )}
                </div>
                <div className="mt-3 text-xs font-medium text-primary">配置 →</div>
              </button>
            ))}
          </div>
        </div>
      )}

      <ProviderDrawer
        provider={active}
        open={!!active}
        onClose={() => setActive(null)}
        onSaved={() => {
          invalidate();
          // 刷新当前抽屉数据
          if (active) {
            void queryClient.invalidateQueries({
              queryKey: ["admin", "model-providers", active.code],
            });
          }
        }}
      />
    </>
  );
}
