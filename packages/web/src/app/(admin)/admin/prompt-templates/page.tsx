"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import {
  PromptAppendEditor,
  defaultAppendDraft,
} from "@/components/admin/PromptAppendEditor";
import {
  PromptComposerEditor,
  defaultComposerDraft,
} from "@/components/admin/PromptComposerEditor";
import {
  PromptTextGenEditor,
  defaultTextGenDraft,
} from "@/components/admin/PromptTextGenEditor";
import {
  PromptVisualStylesEditor,
  defaultVisualStylesDraft,
} from "@/components/admin/PromptVisualStylesEditor";
import {
  PromptCreativeToolsEditor,
  defaultCreativeToolsDraft,
} from "@/components/admin/PromptCreativeToolsEditor";
import { PROMPT_TOOL_CATEGORIES } from "@/lib/admin/promptToolCategories";
import {
  getAdminPromptTool,
  resetAdminPromptTool,
  saveAdminPromptTool,
} from "@/lib/api/admin";
import { ApiError } from "@/lib/api/client";
import {
  validateAppendConfig,
  validateComposerConfig,
  validateCreativeToolsConfig,
  validateTextGenConfig,
  validateVisualStylesConfig,
  type AppendPromptToolConfig,
  type ComposerPromptToolConfig,
  type CreativeToolsPromptToolConfig,
  type PromptToolConfig,
  type TextGenPromptToolConfig,
  type VisualStylesPromptToolConfig,
} from "@/lib/canvas/renderToolPrompt";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function isComposer(config: PromptToolConfig | null): config is ComposerPromptToolConfig {
  return config?.kind === "composer";
}

function isAppend(config: PromptToolConfig | null): config is AppendPromptToolConfig {
  return config?.kind === "append";
}

function isTextGen(config: PromptToolConfig | null): config is TextGenPromptToolConfig {
  return config?.kind === "text_gen";
}

function isVisualStyles(config: PromptToolConfig | null): config is VisualStylesPromptToolConfig {
  return config?.kind === "visual_styles";
}

function isCreativeTools(config: PromptToolConfig | null): config is CreativeToolsPromptToolConfig {
  return config?.kind === "creative_tools";
}

export default function AdminPromptTemplatesPage() {
  const queryClient = useQueryClient();
  const [activeTool, setActiveTool] = useState<string>("multi_angle");
  const [draft, setDraft] = useState<PromptToolConfig | null>(null);
  const [dirty, setDirty] = useState(false);

  const activeMeta = PROMPT_TOOL_CATEGORIES.find((c) => c.id === activeTool);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["admin", "prompt-config", activeTool],
    queryFn: () => getAdminPromptTool(activeTool),
  });

  useEffect(() => {
    if (!data?.config) return;
    setDraft(data.config);
    setDirty(false);
  }, [activeTool, data?.config]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["admin", "prompt-config"] });
    queryClient.invalidateQueries({ queryKey: ["prompt-config"] });
    queryClient.invalidateQueries({ queryKey: ["prompt-templates"] });
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!draft) throw new Error("No draft");
      const errors =
        draft.kind === "composer"
          ? validateComposerConfig(draft, activeTool)
          : draft.kind === "text_gen"
            ? validateTextGenConfig(draft)
            : draft.kind === "visual_styles"
              ? validateVisualStylesConfig(draft)
              : draft.kind === "creative_tools"
                ? validateCreativeToolsConfig(draft)
                : validateAppendConfig(draft);
      if (errors.length) throw new ApiError(400, errors.join("；"));
      return saveAdminPromptTool(activeTool, draft);
    },
    onSuccess: (result) => {
      setDraft(result.config);
      setDirty(false);
      invalidate();
      toast.success("已保存");
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : "保存失败";
      toast.error(message);
    },
  });

  const resetMutation = useMutation({
    mutationFn: () => resetAdminPromptTool(activeTool),
    onSuccess: (result) => {
      setDraft(result.config);
      setDirty(false);
      invalidate();
      toast.success("已恢复默认");
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : "恢复失败";
      toast.error(message);
    },
  });

  const handleReload = async () => {
    if (dirty && !window.confirm("有未保存修改，确定重新加载？")) return;
    setDirty(false);
    await refetch();
  };

  const currentDraft =
    draft ??
    (activeMeta && "visualStyles" in activeMeta && activeMeta.visualStyles
      ? defaultVisualStylesDraft(activeMeta.label)
      : activeMeta && "creativeTools" in activeMeta && activeMeta.creativeTools
        ? defaultCreativeToolsDraft(activeMeta.label)
      : activeMeta && "textGen" in activeMeta && activeMeta.textGen
      ? defaultTextGenDraft(activeMeta.label)
      : activeMeta?.simpleSuffix
        ? defaultAppendDraft(activeMeta.label)
        : defaultComposerDraft());

  return (
    <>
      <AdminHeader
        title="Prompt 模板"
        description="按工具分开管理：多角度/打光用模板 + 函数映射；全景等用追加文案。UI 只传参，文案在此配置。"
      />

      <div className="mb-6 flex flex-wrap gap-2 border-b border-border pb-1">
        {PROMPT_TOOL_CATEGORIES.map((cat) => (
          <button
            key={cat.id}
            type="button"
            onClick={() => setActiveTool(cat.id)}
            className={cn(
              "rounded-t-lg px-4 py-2 text-sm transition-colors",
              activeTool === cat.id
                ? "bg-primary/15 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {cat.label}
          </button>
        ))}
      </div>

      {activeMeta ? (
        <p className="mb-4 text-sm text-muted-foreground">{activeMeta.description}</p>
      ) : null}

      <div className="sticky top-0 z-20 mb-4 flex flex-wrap items-center gap-2 border-b border-border bg-background/95 py-3 backdrop-blur">
        <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending || !dirty}>
          {saveMutation.isPending ? "保存中…" : "保存当前工具"}
        </Button>
        <Button variant="outline" onClick={handleReload} disabled={isFetching}>
          重新加载
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            if (!window.confirm(`恢复「${activeMeta?.label}」为内置默认？`)) return;
            resetMutation.mutate();
          }}
          disabled={resetMutation.isPending}
        >
          恢复默认
        </Button>
        {dirty ? (
          <span className="text-xs text-amber-600 dark:text-amber-400">有未保存修改</span>
        ) : null}
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : isError ? (
        <p className="text-sm text-destructive">
          加载失败
          {error instanceof ApiError && error.message ? `：${error.message}` : null}
        </p>
      ) : isComposer(currentDraft) ? (
        <PromptComposerEditor
          toolId={activeTool}
          value={currentDraft}
          onChange={(next) => {
            setDraft(next);
            setDirty(true);
          }}
        />
      ) : isAppend(currentDraft) ? (
        <PromptAppendEditor
          value={currentDraft}
          onChange={(next) => {
            setDraft(next);
            setDirty(true);
          }}
        />
      ) : isTextGen(currentDraft) ? (
        <PromptTextGenEditor
          value={currentDraft}
          showSubjectRules={
            activeTool === "text_subject"
              ? true
              : activeTool === "text_subject_role"
                ? "role"
                : false
          }
          onChange={(next) => {
            setDraft(next);
            setDirty(true);
          }}
        />
      ) : isVisualStyles(currentDraft) ? (
        <PromptVisualStylesEditor
          value={currentDraft}
          dirty={dirty}
          saving={saveMutation.isPending}
          onSave={() => saveMutation.mutate()}
          onChange={(next) => {
            setDraft(next);
            setDirty(true);
          }}
        />
      ) : isCreativeTools(currentDraft) ? (
        <PromptCreativeToolsEditor
          value={currentDraft}
          onChange={(next) => {
            setDraft(next);
            setDirty(true);
          }}
        />
      ) : null}
    </>
  );
}
