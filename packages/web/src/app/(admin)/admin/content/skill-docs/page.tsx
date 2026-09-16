"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ChevronRight, FileText, Folder } from "lucide-react";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ApiError } from "@/lib/api/client";
import {
  getAdminSkillDoc,
  listAdminSkillDocs,
  putAdminSkillDoc,
  resetAdminSkillDoc,
  type AdminSkillDocDetail,
  type AdminSkillDocFileNode,
} from "@/lib/api/admin";

/** 无列表数据时的兜底 Tab（与后端 ALLOWED_SKILL_DOC_SLUGS 对齐） */
const FALLBACK_SKILL_DOC_TABS = [
  {
    slug: "viral_remake",
    label: "爆款拉片复刻",
    description: "编辑 viral_remake 完整 SKILL.md（含 frontmatter 与 pipeline YAML）。",
  },
  {
    slug: "overseas_localize",
    label: "一键出海",
    description: "编辑 overseas_localize 完整 SKILL.md（含 frontmatter 与 pipeline YAML）。",
  },
  {
    slug: "product_cinematic_commercial",
    label: "单一产品电影级宣传片",
    description:
      "多文件技能包：展示完整目录（SKILL.md / README / references），可按文件编辑与覆盖。",
  },
  {
    slug: "pov_tearjerker_short",
    label: "第一视角催泪短片导演",
    description:
      "多文件技能包：展示完整目录（SKILL.md / README / references），可按文件编辑与覆盖。",
  },
] as const;

function SourceBadge({ source, hasOverride }: { source: string; hasOverride: boolean }) {
  const isOverride = source === "override" || hasOverride;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
        isOverride
          ? "bg-amber-500/15 text-amber-700 dark:text-amber-400"
          : "bg-muted text-muted-foreground"
      )}
    >
      {isOverride ? "覆盖" : "默认"}
    </span>
  );
}

function FileTree({
  nodes,
  activePath,
  overridePaths,
  onSelect,
  depth = 0,
}: {
  nodes: AdminSkillDocFileNode[];
  activePath: string;
  overridePaths: Set<string>;
  onSelect: (path: string) => void;
  depth?: number;
}) {
  return (
    <ul className={cn("space-y-0.5", depth === 0 && "mt-1")}>
      {nodes.map((node) => {
        if (node.type === "dir") {
          return (
            <li key={node.path || node.name}>
              <div
                className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-muted-foreground"
                style={{ paddingLeft: 8 + depth * 12 }}
              >
                <Folder className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate font-medium">{node.name}</span>
              </div>
              {node.children?.length ? (
                <FileTree
                  nodes={node.children}
                  activePath={activePath}
                  overridePaths={overridePaths}
                  onSelect={onSelect}
                  depth={depth + 1}
                />
              ) : null}
            </li>
          );
        }
        const active = node.path === activePath;
        const overridden = overridePaths.has(node.path);
        return (
          <li key={node.path}>
            <button
              type="button"
              onClick={() => onSelect(node.path)}
              className={cn(
                "flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-xs transition-colors",
                active
                  ? "bg-primary/15 font-medium text-primary"
                  : "text-foreground hover:bg-muted"
              )}
              style={{ paddingLeft: 8 + depth * 12 }}
            >
              <FileText className="h-3.5 w-3.5 shrink-0 opacity-70" />
              <span className="min-w-0 flex-1 truncate">{node.name}</span>
              {overridden ? (
                <span className="shrink-0 text-[10px] text-amber-600 dark:text-amber-400">覆</span>
              ) : null}
              {active ? <ChevronRight className="h-3 w-3 shrink-0 opacity-60" /> : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default function AdminSkillDocsPage() {
  const queryClient = useQueryClient();
  const [activeSlug, setActiveSlug] = useState<string>("");
  const [activePath, setActivePath] = useState<string>("");
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);

  const listQuery = useQuery({
    queryKey: ["admin", "skill-docs", "list"],
    queryFn: listAdminSkillDocs,
  });

  const tabs = useMemo(() => {
    const items = listQuery.data?.items;
    if (items?.length) {
      return items.map((it) => {
        const fb = FALLBACK_SKILL_DOC_TABS.find((t) => t.slug === it.slug);
        const pkgHint = it.package
          ? `多文件技能包（${it.fileCount ?? "?"} 个文件）`
          : null;
        return {
          slug: it.slug,
          label: it.title || fb?.label || it.slug,
          description:
            pkgHint ||
            fb?.description ||
            `编辑 ${it.slug} 完整 SKILL.md（含 frontmatter 与 pipeline；保存后前台立即生效）。`,
          package: Boolean(it.package),
        };
      });
    }
    return FALLBACK_SKILL_DOC_TABS.map((t) => ({
      ...t,
      package:
        t.slug === "product_cinematic_commercial" || t.slug === "pov_tearjerker_short",
    }));
  }, [listQuery.data?.items]);

  useEffect(() => {
    if (!tabs.length) return;
    if (!activeSlug || !tabs.some((t) => t.slug === activeSlug)) {
      setActiveSlug(tabs[0].slug);
      setActivePath("");
    }
  }, [tabs, activeSlug]);

  const activeMeta = tabs.find((t) => t.slug === activeSlug);

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["admin", "skill-docs", activeSlug, activePath || "_default"],
    queryFn: () => getAdminSkillDoc(activeSlug, activePath || undefined),
    enabled: Boolean(activeSlug),
  });

  useEffect(() => {
    if (data == null) return;
    setDraft(data.document || "");
    setDirty(false);
  }, [activeSlug, activePath, data?.document]);

  useEffect(() => {
    if (!activePath && data?.activePath) {
      setActivePath(data.activePath);
    }
  }, [data?.activePath, activePath]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["admin", "skill-docs"] });
  };

  const applyServerDoc = (result: AdminSkillDocDetail) => {
    setDraft(result.document || "");
    setDirty(false);
    if (result.activePath) setActivePath(result.activePath);
    queryClient.setQueryData(
      ["admin", "skill-docs", activeSlug, result.activePath || activePath || "_default"],
      result
    );
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      if (!draft.trim()) throw new ApiError(422, "文档内容不能为空");
      const path = data?.package ? activePath || data.activePath || "SKILL.md" : undefined;
      return putAdminSkillDoc(activeSlug, draft, path);
    },
    onSuccess: (result) => {
      applyServerDoc(result);
      invalidate();
      toast.success("已保存");
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : "保存失败");
    },
  });

  const resetMutation = useMutation({
    mutationFn: () => resetAdminSkillDoc(activeSlug),
    onSuccess: (result) => {
      applyServerDoc(result);
      invalidate();
      toast.success("已恢复默认");
    },
    onError: (err) => {
      toast.error(err instanceof ApiError ? err.message : "恢复失败");
    },
  });

  const handleReload = async () => {
    if (dirty && !window.confirm("有未保存修改，确定重新加载？")) return;
    setDirty(false);
    await refetch();
  };

  const handleSelectFile = (path: string) => {
    if (path === activePath) return;
    if (dirty && !window.confirm("有未保存修改，确定切换文件？")) return;
    setDirty(false);
    setActivePath(path);
  };

  const preview = data;
  const overridePaths = useMemo(() => {
    const set = new Set<string>();
    for (const f of data?.files || []) {
      if (f.source === "override") set.add(f.path);
    }
    return set;
  }, [data?.files]);

  const isPackage = Boolean(data?.package || activeMeta?.package);

  return (
    <>
      <AdminHeader
        title="Skill 文档"
        description="编辑平台 Skill Markdown。单文件 Skill 直接编辑；多文件技能包可浏览完整目录并按文件保存覆盖，「恢复默认」回退仓库文件。"
      />

      <div className="mb-6 flex flex-wrap gap-2 border-b border-border pb-1">
        {tabs.map((tab) => (
          <button
            key={tab.slug}
            type="button"
            onClick={() => {
              if (dirty && tab.slug !== activeSlug) {
                if (!window.confirm("有未保存修改，确定切换？")) return;
              }
              setActiveSlug(tab.slug);
              setActivePath("");
              setDirty(false);
            }}
            className={cn(
              "rounded-t-lg px-4 py-2 text-sm transition-colors",
              activeSlug === tab.slug
                ? "bg-primary/15 font-medium text-primary"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {tab.label}
            {tab.package ? (
              <span className="ml-1 text-[10px] text-muted-foreground">包</span>
            ) : null}
          </button>
        ))}
      </div>

      {activeMeta ? (
        <p className="mb-4 text-sm text-muted-foreground">{activeMeta.description}</p>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || !dirty || !activeSlug}
        >
          {saveMutation.isPending ? "保存中…" : "保存"}
        </Button>
        <Button variant="outline" onClick={handleReload} disabled={isFetching || !activeSlug}>
          重新加载
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            if (!window.confirm(`恢复「${activeMeta?.label}」为仓库默认文档？`)) return;
            resetMutation.mutate();
          }}
          disabled={resetMutation.isPending || !activeSlug}
        >
          恢复默认
        </Button>
        {dirty ? (
          <span className="text-xs text-amber-600 dark:text-amber-400">有未保存修改</span>
        ) : null}
        {isPackage && activePath ? (
          <span className="text-xs text-muted-foreground">当前文件：{activePath}</span>
        ) : null}
      </div>

      {!activeSlug || isLoading ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : isError ? (
        <p className="text-sm text-destructive">
          加载失败
          {error instanceof ApiError && error.message ? `：${error.message}` : null}
        </p>
      ) : (
        <div
          className={cn(
            "grid gap-4",
            isPackage
              ? "lg:grid-cols-[220px_minmax(0,1fr)_260px]"
              : "lg:grid-cols-[minmax(0,1fr)_280px]"
          )}
        >
          {isPackage ? (
            <aside className="max-h-[560px] overflow-auto rounded-lg border border-border bg-muted/20 p-3">
              <div className="mb-2 text-xs font-medium text-muted-foreground">
                技能包目录
                {preview?.fileCount ? `（${preview.fileCount}）` : null}
              </div>
              {preview?.tree?.length ? (
                <FileTree
                  nodes={preview.tree}
                  activePath={activePath || preview.activePath || "SKILL.md"}
                  overridePaths={overridePaths}
                  onSelect={handleSelectFile}
                />
              ) : (
                <p className="text-xs text-muted-foreground">暂无文件</p>
              )}
            </aside>
          ) : null}

          <textarea
            value={draft}
            onChange={(e) => {
              setDraft(e.target.value);
              setDirty(true);
            }}
            spellCheck={false}
            className="min-h-[560px] w-full resize-y rounded-lg border border-border bg-background px-3 py-2 font-mono text-xs leading-relaxed text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
            placeholder="完整 SKILL.md…"
          />

          <aside className="space-y-3 rounded-lg border border-border bg-muted/20 p-4 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">来源</span>
              <SourceBadge
                source={preview?.fileSource || preview?.source || "file"}
                hasOverride={Boolean(preview?.hasOverride)}
              />
            </div>
            <div>
              <div className="text-xs text-muted-foreground">标题</div>
              <div className="mt-0.5 font-medium">{preview?.title || "—"}</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">摘要</div>
              <p className="mt-0.5 whitespace-pre-wrap text-muted-foreground">
                {preview?.summary || "—"}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-xs text-muted-foreground">pipeline 步数</div>
                <div className="mt-0.5 font-medium">{preview?.pipelineStepCount ?? 0}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">execution</div>
                <div className="mt-0.5 font-medium break-all">
                  {preview?.execution || "—"}
                </div>
              </div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">entryKind</div>
              <div className="mt-0.5 font-medium">{preview?.entryKind || "—"}</div>
            </div>
            {isPackage ? (
              <div>
                <div className="text-xs text-muted-foreground">形态</div>
                <div className="mt-0.5 font-medium">多文件技能包</div>
              </div>
            ) : null}
            <p className="text-xs text-muted-foreground">
              侧栏摘要取自上次加载/保存结果；编辑中请先保存后再核对解析字段。技能包中仅
              SKILL.md 影响 Runner / Agent 必读。
            </p>
          </aside>
        </div>
      )}
    </>
  );
}
