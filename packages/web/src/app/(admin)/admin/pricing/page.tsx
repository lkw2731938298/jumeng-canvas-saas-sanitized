"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { listAdminPricing } from "@/lib/api/admin";
import { Button } from "@/components/ui/button";
import {
  AdminModelSwitchesPanel,
  AdminPricingPageHead,
  AdminPricingTabPanel,
  usePricingTabItems,
  type PricingSettingsTab,
} from "@/components/admin/AdminPricingPanel";
import { AgentControlPricingPanel } from "@/components/admin/AgentControlPricingPanel";

export default function AdminPricingPage() {
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<PricingSettingsTab>("general");

  const { data: items = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ["admin", "pricing"],
    queryFn: () => listAdminPricing(),
  });

  const tabItems = usePricingTabItems(items, activeTab);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "pricing"] });
    void queryClient.invalidateQueries({ queryKey: ["models"] });
    void queryClient.invalidateQueries({ queryKey: ["credits"] });
    void queryClient.invalidateQueries({ queryKey: ["credits", "canvas-tool-pricing"] });
    void queryClient.invalidateQueries({ queryKey: ["credits", "agent-skill-pricing"] });
    void queryClient.invalidateQueries({ queryKey: ["admin", "pricing", "agent-control"] });
  };

  return (
    <div>
      <AdminPricingPageHead activeTab={activeTab} onTabChange={setActiveTab} />

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : isError ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <p className="text-destructive">
            加载计价配置失败：{error instanceof Error ? error.message : "未知错误"}
          </p>
          <Button variant="outline" size="sm" className="mt-3" onClick={() => void refetch()}>
            重试
          </Button>
        </div>
      ) : activeTab === "model_switches" ? (
        <AdminModelSwitchesPanel />
      ) : activeTab === "agent_control" ? (
        <section className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
          <div className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-bold">AI 操控设置</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              配置画布 AI 操控可选模型，以及「一次对话」固定算力（用户每发一条触发助手回复扣一次）。
            </p>
          </div>
          <div className="px-4 py-3">
            <AgentControlPricingPanel onSaved={invalidate} />
          </div>
        </section>
      ) : (
        <AdminPricingTabPanel tab={activeTab} items={tabItems} onSaved={invalidate} />
      )}
    </div>
  );
}
