"use client";

import { useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import {
  renderAppendPrompt,
  validateAppendConfig,
  type AppendPromptToolConfig,
} from "@/lib/canvas/renderToolPrompt";

export function PromptAppendEditor({
  value,
  onChange,
}: {
  value: AppendPromptToolConfig;
  onChange: (next: AppendPromptToolConfig) => void;
}) {
  const [previewBase, setPreviewBase] = useState("一位角色站在城市天台");

  const previewPrompt = useMemo(
    () => renderAppendPrompt(value, { base: previewBase }),
    [value, previewBase]
  );
  const previewErrors = useMemo(() => validateAppendConfig(value), [value]);

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
          <label className="text-sm font-medium">拼接符</label>
          <Input
            value={value.joiner ?? "，"}
            onChange={(e) => onChange({ ...value, joiner: e.target.value })}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium">追加 Prompt 文案</label>
        <textarea
          value={value.appendText}
          onChange={(e) => onChange({ ...value, appendText: e.target.value })}
          rows={4}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        />
      </div>

      <div className="rounded-xl border border-border p-4">
        <div className="mb-2 text-sm font-medium">预览</div>
        <Input
          className="mb-3"
          value={previewBase}
          onChange={(e) => setPreviewBase(e.target.value)}
          placeholder="节点原有 prompt"
        />
        {previewErrors.length ? (
          <p className="mb-2 text-sm text-destructive">{previewErrors.join("；")}</p>
        ) : null}
        <pre className="whitespace-pre-wrap rounded-md bg-muted/40 p-3 text-sm">{previewPrompt || "（空）"}</pre>
      </div>
    </div>
  );
}

export function defaultAppendDraft(label: string): AppendPromptToolConfig {
  return { kind: "append", menuLabel: label, appendText: "", joiner: "，" };
}
