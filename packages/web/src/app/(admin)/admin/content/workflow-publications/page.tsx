"use client";

/**
 * 管理端工作流发布列表、审核与分类维护。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  DISPLAY_STATUS_FILTERS,
  DisplayStatusBadge,
  ReviewHistoryPanel,
} from "@/components/admin/ContentReviewBadges";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getAdminDiscoverPage,
  listAdminWorkflowPublications,
  patchAdminWorkflowPublicationCategory,
  putAdminDiscoverPage,
  reviewAdminWorkflowPublication,
  type AdminWorkflowPublicationItem,
} from "@/lib/api/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { galleryFilters as fallbackGalleryFilters } from "@/components/huabu/huabuData";

export default function AdminWorkflowPublicationsPage() {
  const queryClient = useQueryClient();
  const [displayStatus, setDisplayStatus] = useState("pending");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<{
    id: string;
    title: string;
    action: "approve" | "reject" | "unpublish";
  } | null>(null);
  const [actionNote, setActionNote] = useState("");
  const [actionCategory, setActionCategory] = useState("");
  const [showCategoryPanel, setShowCategoryPanel] = useState(false);
  const [filtersText, setFiltersText] = useState("");

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin", "workflow-pubs", displayStatus, search, page],
    queryFn: () =>
      listAdminWorkflowPublications({
        displayStatus: displayStatus === "all" ? undefined : displayStatus,
        q: search || undefined,
        page,
        pageSize: 20,
      }),
  });

  const discoverQuery = useQuery({
    queryKey: ["admin", "discover-page", "for-workflow-cats"],
    queryFn: getAdminDiscoverPage,
  });

  const categoryOptions = useMemo(() => {
    const filters = discoverQuery.data?.galleryFilters ?? [];
    const fromConfig = filters.filter((c) => c && c !== "全部");
    if (fromConfig.length > 0) return fromConfig;
    // 配置未加载或为空时与默认作品广场分类一致，避免下拉空白
    return fallbackGalleryFilters.filter((c) => c && c !== "全部");
  }, [discoverQuery.data?.galleryFilters]);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "workflow-pubs"] });
  };

  const reviewMutation = useMutation({
    mutationFn: ({
      id,
      action,
      note,
      category,
    }: {
      id: string;
      action: "approve" | "reject" | "unpublish";
      note?: string;
      category?: string;
    }) => reviewAdminWorkflowPublication(id, action, note, category),
    onSuccess: () => {
      toast.success("已更新审核状态");
      setConfirmAction(null);
      setActionNote("");
      setActionCategory("");
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "操作失败"),
  });

  const categoryMutation = useMutation({
    mutationFn: ({ id, category }: { id: string; category: string }) =>
      patchAdminWorkflowPublicationCategory(id, category),
    onSuccess: () => {
      toast.success("分类已更新");
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "分类更新失败"),
  });

  const saveFiltersMutation = useMutation({
    mutationFn: async (lines: string[]) => {
      const current = await getAdminDiscoverPage();
      let filters = lines.map((x) => x.trim()).filter(Boolean);
      if (!filters.includes("全部")) {
        filters = ["全部", ...filters];
      }
      return putAdminDiscoverPage({ ...current, galleryFilters: filters });
    },
    onSuccess: (res) => {
      toast.success("工作流分类已保存");
      void queryClient.invalidateQueries({
        queryKey: ["admin", "discover-page"],
      });
      // 用户站作品广场 Tab / 发布弹窗同步刷新
      void queryClient.invalidateQueries({ queryKey: ["site", "discover"] });
      void queryClient.invalidateQueries({ queryKey: ["workflow-publications"] });
      setFiltersText((res.galleryFilters || []).join("\n"));
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pageSize = data?.pageSize ?? 20;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const statusCounts = data?.statusCounts ?? {};

  const openConfirm = (
    item: AdminWorkflowPublicationItem,
    action: "approve" | "reject" | "unpublish"
  ) => {
    setConfirmAction({ id: item.id, title: item.title, action });
    setActionNote("");
    setActionCategory(item.category || categoryOptions[0] || "短剧漫剧");
  };

  const categoryPanelText = useMemo(() => {
    if (filtersText) return filtersText;
    return (discoverQuery.data?.galleryFilters ?? []).join("\n");
  }, [filtersText, discoverQuery.data?.galleryFilters]);

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <AdminHeader
        title="工作流发布管理"
        description="审核用户发布到作品广场的工作流；通过后才出现在「全部工作流」。分类与发现页作品广场共用。"
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {DISPLAY_STATUS_FILTERS.map((f) => {
          const count = statusCounts[f.id];
          return (
            <Button
              key={f.id}
              size="sm"
              variant={displayStatus === f.id ? "default" : "outline"}
              onClick={() => {
                setDisplayStatus(f.id);
                setPage(1);
              }}
            >
              {f.label}
              {typeof count === "number" ? ` (${count})` : ""}
            </Button>
          );
        })}
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setShowCategoryPanel((v) => !v);
              if (!filtersText && discoverQuery.data?.galleryFilters) {
                setFiltersText(discoverQuery.data.galleryFilters.join("\n"));
              }
            }}
          >
            {showCategoryPanel ? "收起分类设置" : "分类设置"}
          </Button>
          <Input
            className="w-48"
            placeholder="搜索标题/描述"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setSearch(q.trim());
                setPage(1);
              }
            }}
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setSearch(q.trim());
              setPage(1);
            }}
          >
            搜索
          </Button>
        </div>
      </div>

      {showCategoryPanel ? (
        <div className="mb-4 rounded-lg border border-border bg-card/40 p-4">
          <p className="mb-2 text-sm text-muted-foreground">
            每行一个分类，须含「全部」。保存后同步到发现页作品广场 Tab、用户发布选项与审核分类下拉。
          </p>
          <textarea
            className="mb-3 min-h-[140px] w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm"
            value={categoryPanelText}
            onChange={(e) => setFiltersText(e.target.value)}
          />
          <Button
            size="sm"
            disabled={saveFiltersMutation.isPending}
            onClick={() => {
              const lines = filtersText
                .split("\n")
                .map((x) => x.trim())
                .filter(Boolean);
              saveFiltersMutation.mutate(lines);
            }}
          >
            保存分类
          </Button>
        </div>
      ) : null}

      {isLoading || isFetching ? (
        <p className="text-sm text-muted-foreground">加载中…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无数据</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-left text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="px-3 py-2">标题</th>
                <th className="px-3 py-2">分类</th>
                <th className="px-3 py-2">展示态</th>
                <th className="px-3 py-2">最近审核</th>
                <th className="px-3 py-2">作者</th>
                <th className="px-3 py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const expanded = expandedId === item.id;
                return (
                  <Fragment key={item.id}>
                    <tr className="border-t">
                      <td className="px-3 py-2">
                        <div className="font-medium">{item.title}</div>
                        <div className="line-clamp-2 text-xs text-muted-foreground">
                          {item.description}
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <select
                          className="max-w-[9rem] rounded-md border border-border bg-background px-2 py-1 text-xs"
                          value={item.category || ""}
                          disabled={categoryMutation.isPending}
                          onChange={(e) => {
                            const next = e.target.value;
                            if (!next || next === item.category) return;
                            categoryMutation.mutate({ id: item.id, category: next });
                          }}
                        >
                          {!categoryOptions.includes(item.category || "") && item.category ? (
                            <option value={item.category}>{item.category}</option>
                          ) : null}
                          {categoryOptions.map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-3 py-2">
                        <DisplayStatusBadge status={item.displayStatus} />
                      </td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        <div>{item.reviewedAt ? formatDateTimeCN(item.reviewedAt) : "—"}</div>
                        <div className="line-clamp-1">
                          {item.reviewNote || item.reviewedByName || "无备注"}
                        </div>
                        <button
                          type="button"
                          className="mt-0.5 text-primary underline-offset-2 hover:underline"
                          onClick={() => setExpandedId(expanded ? null : item.id)}
                        >
                          {expanded ? "收起记录" : "审核记录"}
                        </button>
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {item.authorName || item.authorId || "—"}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          {item.videoUrl ? (
                            <a
                              className="inline-flex h-8 items-center rounded-md px-2 text-xs text-primary underline-offset-2 hover:underline"
                              href={item.videoUrl}
                              target="_blank"
                              rel="noreferrer"
                            >
                              预览视频
                            </a>
                          ) : null}
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={reviewMutation.isPending}
                            onClick={() => openConfirm(item, "approve")}
                          >
                            通过
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={reviewMutation.isPending}
                            onClick={() => openConfirm(item, "reject")}
                          >
                            驳回
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={reviewMutation.isPending}
                            onClick={() => openConfirm(item, "unpublish")}
                          >
                            下架
                          </Button>
                        </div>
                      </td>
                    </tr>
                    {expanded ? (
                      <tr className="border-t bg-muted/20">
                        <td colSpan={6} className="px-3 py-3">
                          <ReviewHistoryPanel
                            note={item.reviewNote}
                            reviewedAt={item.reviewedAt}
                            reviewedByName={item.reviewedByName}
                            history={item.reviewHistory}
                          />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
        <span>
          共 {total} 条 · 第 {page}/{totalPages} 页
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            上一页
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </Button>
        </div>
      </div>

      {confirmAction ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setConfirmAction(null)}
        >
          <div
            className="w-full max-w-md rounded-xl bg-background p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-2 font-semibold">
              {confirmAction.action === "approve"
                ? "通过审核"
                : confirmAction.action === "reject"
                  ? "驳回"
                  : "下架"}
              ：{confirmAction.title}
            </h3>
            {confirmAction.action === "approve" ? (
              <label className="mb-3 block text-sm">
                <span className="mb-1 block text-muted-foreground">分类</span>
                <select
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  value={actionCategory}
                  onChange={(e) => setActionCategory(e.target.value)}
                >
                  {categoryOptions.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="mb-3 block text-sm">
              <span className="mb-1 block text-muted-foreground">
                {confirmAction.action === "approve" ? "备注（可选）" : "原因（可选）"}
              </span>
              <Input
                value={actionNote}
                onChange={(e) => setActionNote(e.target.value)}
                placeholder="写入审核记录"
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setConfirmAction(null)}>
                取消
              </Button>
              <Button
                size="sm"
                disabled={reviewMutation.isPending}
                onClick={() =>
                  reviewMutation.mutate({
                    id: confirmAction.id,
                    action: confirmAction.action,
                    note: actionNote.trim() || undefined,
                    category:
                      confirmAction.action === "approve"
                        ? actionCategory || undefined
                        : undefined,
                  })
                }
              >
                确认
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
