"use client";

import { useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import { CREATIVE_TOOL_COLUMNS } from "@/lib/canvas/imageCreativeToolsCatalog";
import {
  validateCreativeToolsConfig,
  type CreativeToolPromptItem,
  type CreativeToolsPromptToolConfig,
} from "@/lib/canvas/renderToolPrompt";
import { cn } from "@/lib/utils";

/** 目录顺序，用于后台列表排序与缺项提示 */
const CATALOG_ORDER: Array<{ id: string; label: string; section: string }> = (() => {
  const rows: Array<{ id: string; label: string; section: string }> = [];
  for (const column of CREATIVE_TOOL_COLUMNS) {
    for (const section of column) {
      for (const item of section.items) {
        rows.push({ id: item.id, label: item.label, section: section.label });
      }
    }
  }
  return rows;
})();

function sortItems(items: CreativeToolPromptItem[]): CreativeToolPromptItem[] {
  const order = new Map(CATALOG_ORDER.map((row, idx) => [row.id, idx]));
  return [...items].sort(
    (a, b) =>
      (order.get(a.id) ?? 999) - (order.get(b.id) ?? 999) ||
      (a.sortOrder ?? 0) - (b.sortOrder ?? 0)
  );
}

export function PromptCreativeToolsEditor({
  value,
  onChange,
}: {
  value: CreativeToolsPromptToolConfig;
  onChange: (next: CreativeToolsPromptToolConfig) => void;
}) {
  const [activeId, setActiveId] = useState<string>(value.items[0]?.id || CATALOG_ORDER[0]?.id || "");
  const items = useMemo(() => sortItems(value.items || []), [value.items]);
  const active = items.find((item) => item.id === activeId) || items[0];
  const errors = useMemo(() => validateCreativeToolsConfig(value), [value]);

  const patchItem = (id: string, patch: Partial<CreativeToolPromptItem>) => {
    onChange({
      ...value,
      items: value.items.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    });
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">菜单名称</label>
          <Input
            value={value.menuLabel}
            onChange={(e) => onChange({ ...value, menuLabel: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">拼接符（兼容旧追加）</label>
          <Input
            value={value.joiner ?? "，"}
            onChange={(e) => onChange({ ...value, joiner: e.target.value })}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium">默认追加文案（兼容旧顶栏后缀）</label>
        <textarea
          value={value.appendText ?? ""}
          onChange={(e) => onChange({ ...value, appendText: e.target.value })}
          rows={2}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          placeholder="多机位九宫格等旧路径的默认追加文案"
        />
      </div>

      <p className="text-sm text-muted-foreground">
        下方按画布「九宫格」弹层功能分别配置提示词；创建节点时会预填到 prompt，可在此随时改文案。
      </p>

      <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <div className="max-h-[560px] space-y-1 overflow-y-auto rounded-xl border border-border p-2">
          {items.map((item) => {
            const meta = CATALOG_ORDER.find((row) => row.id === item.id);
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveId(item.id)}
                className={cn(
                  "flex w-full flex-col rounded-lg px-2.5 py-2 text-left text-sm transition-colors",
                  active?.id === item.id
                    ? "bg-primary/15 text-primary"
                    : "hover:bg-muted text-foreground"
                )}
              >
                <span className="font-medium">{item.label || meta?.label || item.id}</span>
                <span className="text-[11px] text-muted-foreground">
                  {meta?.section || "自定义"} · {item.id}
                  {item.enabled === false ? " · 已禁用" : ""}
                </span>
              </button>
            );
          })}
        </div>

        {active ? (
          <div className="space-y-3 rounded-xl border border-border p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="text-sm font-medium">{active.label}</div>
                <div className="text-xs text-muted-foreground">id: {active.id}</div>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={active.enabled !== false}
                  onChange={(e) => patchItem(active.id, { enabled: e.target.checked })}
                />
                启用
              </label>
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">显示名称</label>
              <Input
                value={active.label}
                onChange={(e) => patchItem(active.id, { label: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">生成提示词</label>
              <textarea
                value={active.prompt}
                onChange={(e) => patchItem(active.id, { prompt: e.target.value })}
                rows={10}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm leading-relaxed"
              />
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">暂无条目</p>
        )}
      </div>

      {errors.length ? (
        <p className="text-sm text-destructive">{errors.join("；")}</p>
      ) : null}
    </div>
  );
}

export function defaultCreativeToolsDraft(label: string): CreativeToolsPromptToolConfig {
  return {
    kind: "creative_tools",
    menuLabel: label,
    joiner: "，",
    appendText: "九宫格构图，九张关联分镜，多机位视角一致、角色与场景设定统一",
    items: CATALOG_ORDER.map((row, idx) => ({
      id: row.id,
      label: row.label,
      prompt: row.label,
      enabled: true,
      sortOrder: idx * 10,
    })),
  };
}
