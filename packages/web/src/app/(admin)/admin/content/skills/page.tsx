"use client";

/**
 * 管理端 Skill 列表、社区审核、分类设置；可改名称与封面。
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  DisplayStatusBadge,
  ReviewHistoryPanel,
  SKILL_DISPLAY_STATUS_FILTERS,
} from "@/components/admin/ContentReviewBadges";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  getAdminManagedSkillDoc,
  getAdminSkillCategories,
  listAdminSkills,
  patchAdminSkillCategory,
  patchAdminSkillMeta,
  putAdminSkillCategories,
  reviewAdminSkill,
  uploadAdminDiscoverImage,
  type AdminSkillListItem,
} from "@/lib/api/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { ensureHttpsOssUrl } from "@/lib/signedUrl";
import { validateImageAspectRatio } from "@/lib/validateImageAspectRatio";

export default function AdminSkillsManagePage() {
  const queryClient = useQueryClient();
  const coverInputRef = useRef<HTMLInputElement>(null);
  const [displayStatus, setDisplayStatus] = useState("pending");
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [docPreview, setDocPreview] = useState<{
    slug: string;
    title: string;
    markdown: string;
  } | null>(null);
  const [confirmAction, setConfirmAction] = useState<{
    slug: string;
    title: string;
    action: "approve" | "reject" | "unpublish";
  } | null>(null);
  const [actionNote, setActionNote] = useState("");
  const [actionCategory, setActionCategory] = useState("");
  const [showCategoryPanel, setShowCategoryPanel] = useState(false);
  const [categoriesText, setCategoriesText] = useState("");
  // 编辑名称 / 封面
  const [editItem, setEditItem] = useState<AdminSkillListItem | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editCoverUrl, setEditCoverUrl] = useState("");
  const [uploadingCover, setUploadingCover] = useState(false);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin", "skills-manage", displayStatus, search, page],
    queryFn: () =>
      listAdminSkills({
        displayStatus: displayStatus === "all" ? undefined : displayStatus,
        q: search || undefined,
        page,
        pageSize: 20,
      }),
  });

  const categoriesQuery = useQuery({
    queryKey: ["admin", "skill-categories"],
    queryFn: getAdminSkillCategories,
  });

  const categoryOptions = categoriesQuery.data?.categories ?? [];

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "skills-manage"] });
  };

  const reviewMutation = useMutation({
    mutationFn: ({
      slug,
      action,
      note,
      category,
    }: {
      slug: string;
      action: "approve" | "reject" | "unpublish";
      note?: string;
      category?: string;
    }) => reviewAdminSkill(slug, action, note, category),
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
    mutationFn: ({ slug, category }: { slug: string; category: string }) =>
      patchAdminSkillCategory(slug, category),
    onSuccess: () => {
      toast.success("分类已更新");
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "分类更新失败"),
  });

  const metaMutation = useMutation({
    mutationFn: ({
      slug,
      title,
      coverUrl,
      clearCover,
    }: {
      slug: string;
      title: string;
      coverUrl?: string;
      clearCover?: boolean;
    }) =>
      patchAdminSkillMeta(slug, {
        title,
        ...(clearCover
          ? { clearCover: true }
          : coverUrl
            ? { coverUrl }
            : {}),
      }),
    onSuccess: () => {
      toast.success("名称/封面已保存");
      setEditItem(null);
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const saveCategoriesMutation = useMutation({
    mutationFn: (categories: string[]) => putAdminSkillCategories(categories),
    onSuccess: (res) => {
      toast.success("分类配置已保存");
      void queryClient.invalidateQueries({ queryKey: ["admin", "skill-categories"] });
      setCategoriesText((res.categories || []).join("\n"));
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pageSize = data?.pageSize ?? 20;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const statusCounts = data?.statusCounts ?? {};

  const openDoc = async (item: AdminSkillListItem) => {
    try {
      const doc = await getAdminManagedSkillDoc(item.slug);
      setDocPreview({ slug: doc.slug, title: doc.title, markdown: doc.markdown });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "加载文档失败");
    }
  };

  const openEdit = (item: AdminSkillListItem) => {
    setEditItem(item);
    setEditTitle(item.title || "");
    setEditCoverUrl(
      ensureHttpsOssUrl(item.coverUrl || "") || item.coverUrl || ""
    );
    setUploadingCover(false);
  };

  const handleEditCover = async (file: File | null) => {
    if (!file || uploadingCover) return;
    /* 校验封面必须为 9:16 竖版比例（允许 ±5% 偏差） */
    const ok = await validateImageAspectRatio(file, 9, 16).catch(() => false);
    if (!ok) {
      toast.error("封面须为 9:16 竖版比例（宽:高 = 9:16），请重新选择");
      if (coverInputRef.current) coverInputRef.current.value = "";
      return;
    }
    setUploadingCover(true);
    try {
      const up = await uploadAdminDiscoverImage(file);
      setEditCoverUrl(ensureHttpsOssUrl(up.imageUrl) || up.imageUrl);
      toast.success("封面已上传");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "封面上传失败");
    } finally {
      setUploadingCover(false);
      if (coverInputRef.current) coverInputRef.current.value = "";
    }
  };

  const openConfirm = (
    item: AdminSkillListItem,
    action: "approve" | "reject" | "unpublish"
  ) => {
    setConfirmAction({ slug: item.slug, title: item.title, action });
    setActionNote("");
    setActionCategory(item.category || categoryOptions[0] || "通用技能");
  };

  const categoryPanelText = useMemo(() => {
    if (categoriesText) return categoriesText;
    return (categoriesQuery.data?.categories ?? []).join("\n");
  }, [categoriesText, categoriesQuery.data?.categories]);

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <AdminHeader
        title="Skill 管理"
        description="审核用户提交的社区 Skill；通过后出现在技能页公开目录。可维护分类 Tab，并可直接修改名称与封面。"
      />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {SKILL_DISPLAY_STATUS_FILTERS.map((f) => {
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
              if (!categoriesText && categoriesQuery.data?.categories) {
                setCategoriesText(categoriesQuery.data.categories.join("\n"));
              }
            }}
          >
            {showCategoryPanel ? "收起分类设置" : "分类设置"}
          </Button>
          <Input
            className="w-48"
            placeholder="搜索标题/slug"
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
            每行一个分类（不含「推荐」）。技能页 Tab = 推荐 + 下列分类。
          </p>
          <textarea
            className="mb-3 min-h-[140px] w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm"
            value={categoryPanelText}
            onChange={(e) => setCategoriesText(e.target.value)}
          />
          <Button
            size="sm"
            disabled={saveCategoriesMutation.isPending}
            onClick={() => {
              const cats = categoriesText
                .split("\n")
                .map((x) => x.trim())
                .filter(Boolean);
              saveCategoriesMutation.mutate(cats);
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
                <th className="px-3 py-2">封面</th>
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
                        {item.coverUrl ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={item.coverUrl}
                            alt=""
                            className="h-10 w-10 rounded object-cover"
                          />
                        ) : (
                          <div className="flex h-10 w-10 items-center justify-center rounded bg-muted text-[10px] text-muted-foreground">
                            无
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <div className="font-medium">{item.title}</div>
                        <div className="text-xs text-muted-foreground">{item.slug}</div>
                      </td>
                      <td className="px-3 py-2">
                        <select
                          className="max-w-[9rem] rounded-md border border-border bg-background px-2 py-1 text-xs"
                          value={item.category || ""}
                          disabled={categoryMutation.isPending}
                          onChange={(e) => {
                            const next = e.target.value;
                            if (!next || next === item.category) return;
                            categoryMutation.mutate({ slug: item.slug, category: next });
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
                          onClick={() =>
                            setExpandedId(expanded ? null : item.id)
                          }
                        >
                          {expanded ? "收起记录" : "审核记录"}
                        </button>
                      </td>
                      <td className="px-3 py-2 text-muted-foreground">
                        {item.ownerUserId || "平台"}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex flex-wrap gap-1">
                          <Button size="sm" variant="ghost" onClick={() => openEdit(item)}>
                            编辑
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => void openDoc(item)}>
                            预览
                          </Button>
                          {item.ownerUserId ? (
                            <>
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
                            </>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                    {expanded ? (
                      <tr className="border-t bg-muted/20">
                        <td colSpan={7} className="px-3 py-3">
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

      {docPreview ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setDocPreview(null)}
        >
          <div
            className="max-h-[80vh] w-full max-w-2xl overflow-auto rounded-xl bg-background p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <h3 className="font-semibold">{docPreview.title}</h3>
              <Button size="sm" variant="ghost" onClick={() => setDocPreview(null)}>
                关闭
              </Button>
            </div>
            <pre className="whitespace-pre-wrap text-xs leading-relaxed">{docPreview.markdown}</pre>
          </div>
        </div>
      ) : null}

      {editItem ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setEditItem(null)}
        >
          <div
            className="w-full max-w-md rounded-xl bg-background p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="mb-1 font-semibold">编辑名称 / 封面</h3>
            <p className="mb-3 text-xs text-muted-foreground">
              {editItem.slug}
              {editItem.ownerUserId ? "" : " · 平台 Skill"}
            </p>
            <label className="mb-3 block text-sm">
              <span className="mb-1 block text-muted-foreground">名称</span>
              <Input
                value={editTitle}
                maxLength={64}
                disabled={metaMutation.isPending}
                onChange={(e) => setEditTitle(e.target.value)}
              />
            </label>
            <div className="mb-4 text-sm">
              <span className="mb-1 block text-muted-foreground">封面</span>
              <div className="flex items-center gap-3">
                {editCoverUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={editCoverUrl}
                    alt=""
                    className="h-16 w-16 rounded object-cover"
                  />
                ) : (
                  <div className="flex h-16 w-16 items-center justify-center rounded bg-muted text-xs text-muted-foreground">
                    无
                  </div>
                )}
                <div className="flex flex-col gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={uploadingCover || metaMutation.isPending}
                    onClick={() => coverInputRef.current?.click()}
                  >
                    {uploadingCover ? "上传中…" : editCoverUrl ? "更换封面" : "选择图片"}
                  </Button>
                  {editCoverUrl ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={metaMutation.isPending}
                      onClick={() => setEditCoverUrl("")}
                    >
                      清除封面
                    </Button>
                  ) : null}
                </div>
              </div>
              <input
                ref={coverInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/gif"
                className="sr-only"
                onChange={(e) => void handleEditCover(e.target.files?.[0] ?? null)}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setEditItem(null)}>
                取消
              </Button>
              <Button
                size="sm"
                disabled={metaMutation.isPending || uploadingCover}
                onClick={() => {
                  const t = editTitle.trim();
                  if (!t) {
                    toast.error("名称不能为空");
                    return;
                  }
                  const clearCover = !editCoverUrl.trim() && Boolean(editItem.coverUrl);
                  metaMutation.mutate({
                    slug: editItem.slug,
                    title: t,
                    ...(clearCover
                      ? { clearCover: true }
                      : editCoverUrl.trim()
                        ? { coverUrl: editCoverUrl.trim() }
                        : {}),
                  });
                }}
              >
                保存
              </Button>
            </div>
          </div>
        </div>
      ) : null}

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
                    slug: confirmAction.slug,
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
