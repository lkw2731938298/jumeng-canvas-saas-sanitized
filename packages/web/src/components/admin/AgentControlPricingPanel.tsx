"use client";

/**
 * 管理端 · AI 操控：可选控制器模型白名单 + 一次对话算力价
 */
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getAdminAgentSkillPricing,
  putAdminAgentSkillPricing,
  type AdminAgentControllerModel,
} from "@/lib/api/admin";
import { roundCreditAmount } from "@/lib/api/credits";

export function AgentControlPricingPanel({ onSaved }: { onSaved?: () => void }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["admin", "pricing", "agent-control"],
    queryFn: getAdminAgentSkillPricing,
  });

  const [turnCost, setTurnCost] = useState("20");
  const [skillAiFillCost, setSkillAiFillCost] = useState("10");
  const [enabledIds, setEnabledIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!data) return;
    setTurnCost(String(data.conversationTurn ?? data.sessionStart ?? 20));
    setSkillAiFillCost(String(data.skillAiFill ?? 10));
    // 白名单为空 = 全部开启
    if (!data.controllerModels?.length) {
      setEnabledIds(new Set(data.catalog.map((m) => m.id)));
    } else {
      setEnabledIds(new Set(data.controllerModels));
    }
  }, [data]);

  const catalog = useMemo(() => data?.catalog ?? [], [data?.catalog]);

  const toggle = (id: string) => {
    setEnabledIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setEnabledIds(new Set(catalog.map((m) => m.id)));
  const clearAll = () => setEnabledIds(new Set());

  const saveMutation = useMutation({
    mutationFn: () => {
      const cost = roundCreditAmount(turnCost);
      // 全选时存空数组 → 运行时视为「不限白名单」
      const allSelected =
        catalog.length > 0 && catalog.every((m) => enabledIds.has(m.id));
      const models = allSelected ? [] : Array.from(enabledIds);
      if (!allSelected && models.length === 0) {
        throw new Error("请至少启用一个 AI 操控模型");
      }
      const fillCost = roundCreditAmount(skillAiFillCost);
      return putAdminAgentSkillPricing({
        conversationTurn: cost,
        skillAiFill: fillCost,
        controllerModels: models,
      });
    },
    onSuccess: () => {
      toast.success("AI 操控配置已保存");
      void refetch();
      onSaved?.();
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const renderModel = (m: AdminAgentControllerModel) => {
    const checked = enabledIds.has(m.id);
    return (
      <label
        key={m.id}
        className="flex cursor-pointer items-start gap-2 rounded-lg border border-border/70 bg-background/60 px-3 py-2 text-sm hover:border-violet-500/40"
      >
        <input
          type="checkbox"
          className="mt-1"
          checked={checked}
          onChange={() => toggle(m.id)}
        />
        <span className="min-w-0 flex-1">
          <span className="font-medium">{m.label}</span>
          <span className="mt-0.5 block text-[11px] text-muted-foreground">
            {m.id}
            {m.supportsFiles ? " · 可附件" : ""}
            {!m.configured ? " · 未配置密钥" : ""}
          </span>
        </span>
      </label>
    );
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-muted/10 px-4 py-3">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold">一次对话算力</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              用户在 AI 操控里每发送一条并触发助手回复，按此价格预扣一次（与生图/生视频轨道分开）。
              {data?.version != null ? ` 当前版本 v${data.version}` : null}
            </p>
          </div>
          <Button
            size="sm"
            disabled={saveMutation.isPending || isLoading}
            onClick={() => saveMutation.mutate()}
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              "保存"
            )}
          </Button>
        </div>
        {isLoading ? (
          <p className="text-xs text-muted-foreground">加载中…</p>
        ) : isError ? (
          <div className="text-xs text-destructive">
            加载失败
            <Button variant="ghost" size="sm" className="ml-2 h-7" onClick={() => void refetch()}>
              重试
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap gap-x-6 gap-y-3">
            <label className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">算力 / 次对话</span>
              <Input
                className="h-8 w-28 tabular-nums"
                value={turnCost}
                onChange={(e) => setTurnCost(e.target.value)}
                inputMode="decimal"
                aria-label="一次对话算力"
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Skill AI 填充</span>
              <Input
                className="h-8 w-28 tabular-nums"
                value={skillAiFillCost}
                onChange={(e) => setSkillAiFillCost(e.target.value)}
                inputMode="decimal"
                aria-label="创建 Skill AI 填充算力"
              />
              <span className="text-xs text-muted-foreground">算力 / 次</span>
            </label>
          </div>
        )}
        {!isLoading && !isError ? (
          <p className="mt-2 text-[11px] text-muted-foreground">
            「Skill AI 填充」对应创建弹窗「按类型 AI 填充」按钮；0 表示未配置（开启算力时不可用）。
          </p>
        ) : null}
      </div>

      <div className="rounded-lg border border-border bg-muted/10 px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-sm font-semibold">AI 操控可用模型</h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              勾选后出现在画布 AI 操控下拉里。全选保存后视为不限制（目录内控制器均可）。
            </p>
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={selectAll}>
              全选
            </Button>
            <Button type="button" variant="outline" size="sm" onClick={clearAll}>
              清空
            </Button>
          </div>
        </div>
        {isLoading ? (
          <p className="text-xs text-muted-foreground">加载中…</p>
        ) : isError ? null : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {catalog.map(renderModel)}
          </div>
        )}
      </div>
    </div>
  );
}
