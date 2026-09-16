"use client";

import { useMemo, useState } from "react";
import { Input } from "@/components/ui/input";
import {
  buildTextGenUserContent,
  validateTextGenConfig,
  type TextGenPromptToolConfig,
} from "@/lib/canvas/renderToolPrompt";

export function defaultTextGenDraft(label: string): TextGenPromptToolConfig {
  return {
    kind: "text_gen",
    menuLabel: label,
    systemPrompt: "",
    userPrefix: "",
    roleRule: "",
    sceneRule: "",
    propRule: "",
  };
}

export function PromptTextGenEditor({
  value,
  onChange,
  showSubjectRules = false,
}: {
  value: TextGenPromptToolConfig;
  onChange: (next: TextGenPromptToolConfig) => void;
  /** true = all rules; role/scene/prop = single rule section */
  showSubjectRules?: boolean | "role" | "scene" | "prop";
}) {
  const [previewContent, setPreviewContent] = useState("一位少年在古城墙下与师父告别。");

  const previewPrompt = useMemo(
    () => buildTextGenUserContent(value, { content: previewContent }),
    [value, previewContent]
  );
  const previewErrors = useMemo(() => validateTextGenConfig(value), [value]);

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <label className="text-sm font-medium">菜单名称</label>
        <Input
          value={value.menuLabel}
          onChange={(e) => onChange({ ...value, menuLabel: e.target.value })}
        />
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium">System Prompt（LLM 系统提示词）</label>
        <textarea
          value={value.systemPrompt}
          onChange={(e) => onChange({ ...value, systemPrompt: e.target.value })}
          rows={8}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
        />
      </div>

      <div className="space-y-1.5">
        <label className="text-sm font-medium">User 前缀（可选，拼在用户输入前）</label>
        <textarea
          value={value.userPrefix ?? ""}
          onChange={(e) => onChange({ ...value, userPrefix: e.target.value })}
          rows={6}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
        />
      </div>

      {showSubjectRules === true || showSubjectRules === "role" ? (
        <div className="space-y-1.5">
          <label className="text-sm font-medium">角色提取规则</label>
          <textarea
            value={value.roleRule ?? ""}
            onChange={(e) => onChange({ ...value, roleRule: e.target.value })}
            rows={6}
            className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
          />
        </div>
      ) : null}
      {showSubjectRules === true || showSubjectRules === "scene" ? (
        <div className="space-y-1.5">
          <label className="text-sm font-medium">场景提取规则</label>
          <textarea
            value={value.sceneRule ?? ""}
            onChange={(e) => onChange({ ...value, sceneRule: e.target.value })}
            rows={4}
            className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
          />
        </div>
      ) : null}
      {showSubjectRules === true || showSubjectRules === "prop" ? (
        <div className="space-y-1.5">
          <label className="text-sm font-medium">道具提取规则</label>
          <textarea
            value={value.propRule ?? ""}
            onChange={(e) => onChange({ ...value, propRule: e.target.value })}
            rows={4}
            className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
          />
        </div>
      ) : null}

      <div className="rounded-xl border border-border p-4">
        <div className="mb-2 text-sm font-medium">User 消息预览</div>
        <Input
          className="mb-3"
          value={previewContent}
          onChange={(e) => setPreviewContent(e.target.value)}
          placeholder="示例用户输入"
        />
        {previewErrors.length ? (
          <p className="mb-2 text-xs text-destructive">{previewErrors.join("；")}</p>
        ) : null}
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-3 text-xs">
          {previewPrompt || "（空）"}
        </pre>
      </div>
    </div>
  );
}
