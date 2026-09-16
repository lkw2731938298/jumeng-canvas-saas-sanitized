"use client";

/**
 * 个人中心 · Agent Access Key 管理（给外部 Agent / jumeng-skills 用）。
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  createAgentAccessKey,
  listAgentAccessKeys,
  revokeAgentAccessKey,
} from "@/lib/api/agentAccessKeys";
import { formatDateTimeCN } from "@/lib/formatDateTime";

export function AgentAccessKeyPanel() {
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [freshKey, setFreshKey] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["agent-access-keys"],
    queryFn: listAgentAccessKeys,
  });

  const active = (query.data ?? []).filter((k) => !k.revokedAt);
  const revoked = (query.data ?? []).filter((k) => k.revokedAt);

  const onCreate = async () => {
    setCreating(true);
    try {
      const row = await createAgentAccessKey({ name: `key-${active.length + 1}` });
      setFreshKey(row.accessKey || null);
      void queryClient.invalidateQueries({ queryKey: ["agent-access-keys"] });
      toast.success("已创建 Access Key，请立即复制保存");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const onRevoke = async (id: string) => {
    if (!window.confirm("确认吊销该 Access Key？外部 Agent 将立即无法使用。")) return;
    try {
      await revokeAgentAccessKey(id);
      void queryClient.invalidateQueries({ queryKey: ["agent-access-keys"] });
      toast.success("已吊销");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "吊销失败");
    }
  };

  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("已复制");
    } catch {
      toast.error("复制失败，请手动选择");
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-medium text-white/80">Agent Access Key</p>
          <p className="mt-1 text-xs text-white/45">
            给外部 Agent（如 OpenClaw）调用 OpenAPI：创建会话、轮询进度、上传素材。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void onCreate()}
          disabled={creating || active.length >= 5}
          className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-white/15 bg-white/10 px-2.5 py-1.5 text-xs text-white/90 hover:bg-white/15 disabled:opacity-40"
        >
          {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
          新建
        </button>
      </div>

      {freshKey ? (
        <div className="rounded-xl border border-amber-400/30 bg-amber-400/10 p-3">
          <p className="mb-1 text-xs text-amber-200/90">明文密钥仅显示一次，请立即复制：</p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all font-mono text-[11px] text-white/90">
              {freshKey}
            </code>
            <button
              type="button"
              className="rounded p-1 text-white/70 hover:bg-white/10"
              onClick={() => void copy(freshKey)}
              aria-label="复制"
            >
              <Copy className="h-4 w-4" />
            </button>
          </div>
        </div>
      ) : null}

      {query.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-white/40">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载中…
        </p>
      ) : active.length === 0 ? (
        <p className="text-xs text-white/40">暂无有效密钥</p>
      ) : (
        <ul className="space-y-2">
          {active.map((k) => (
            <li
              key={k.id}
              className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2"
            >
              <KeyRound className="h-4 w-4 shrink-0 text-white/45" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-white/90">{k.name}</p>
                <p className="font-mono text-[11px] text-white/40">{k.keyPrefix}…</p>
                <p className="text-[10px] text-white/30">
                  创建 {k.createdAt ? formatDateTimeCN(k.createdAt) : "—"}
                  {k.lastUsedAt ? ` · 最近使用 ${formatDateTimeCN(k.lastUsedAt)}` : ""}
                </p>
              </div>
              <button
                type="button"
                className="rounded p-1.5 text-red-300/80 hover:bg-red-400/10"
                aria-label="吊销"
                onClick={() => void onRevoke(k.id)}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}

      {revoked.length > 0 ? (
        <p className="text-[10px] text-white/30">已吊销 {revoked.length} 个密钥</p>
      ) : null}

      <p className="text-[10px] leading-relaxed text-white/35">
        文档见仓库 <code className="text-white/50">jumeng-skills/</code>。OpenAPI 基址：
        <code className="text-white/50"> /api/v1/openapi</code>
      </p>
    </div>
  );
}
