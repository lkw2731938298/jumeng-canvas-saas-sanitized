"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Film, ImagePlus, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { validateCanvasImageFile } from "@/lib/canvas/imageSizePolicy";
import {
  createAdminMaterialLibraryItem,
  createAdminPromptLibraryCategory,
  deleteAdminMaterialLibraryItem,
  deleteAdminPromptLibraryCategory,
  listAdminMaterialLibrary,
  updateAdminMaterialLibraryItem,
  updateAdminPromptLibraryCategory,
  type AdminMaterialLibraryItem,
  type AdminPromptLibraryCategory,
} from "@/lib/api/admin";

type Category = "style" | "effect" | "character" | "prompt";

const TABS: {
  key: Category;
  label: string;
  media: "image" | "video" | "effect" | "prompt";
  hint: string;
}[] = [
  { key: "style", label: "风格库", media: "image", hint: "上传图片 + 标题；画布落图节点作参考" },
  {
    key: "effect",
    label: "特效库",
    media: "effect",
    hint: "可上传视频（MP4/WebM/MOV/MKV/AVI）或动图/静图（GIF/WebP/PNG/JPG）；视频落视频节点可连 SD2.0，动图落图节点",
  },
  { key: "character", label: "角色库", media: "image", hint: "上传图片 + 标题；画布落图节点作参考" },
  {
    key: "prompt",
    label: "提示词库",
    media: "prompt",
    hint: "上传图片或视频作封面预览，并填写提示词正文；可设置分类供用户侧筛选。用户选用后落只读文本节点。",
  },
];

const EFFECT_ACCEPT =
  "video/mp4,video/webm,video/quicktime,video/x-matroska,video/x-msvideo,video/avi,video/x-m4v,.mp4,.webm,.mov,.mkv,.avi,.m4v,.mpeg,.mpg,image/gif,image/webp,image/png,image/jpeg,.gif,.webp,.png,.jpg,.jpeg";

const PROMPT_ACCEPT =
  "image/jpeg,image/png,image/gif,image/webp,image/bmp,video/mp4,video/webm,video/quicktime,video/x-matroska,video/x-msvideo,video/avi,video/x-m4v,.jpg,.jpeg,.png,.gif,.webp,.bmp,.mp4,.webm,.mov,.mkv,.avi,.m4v";

const PROMPT_TEXT_MAX = 8000;

const QUERY_KEY = ["admin", "material-library"] as const;

/**
 * 管理后台 · 素材库上传页（仅管理员可上传，用户侧只读选用）。
 */
export default function AdminMaterialLibraryPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Category>("style");
  const [title, setTitle] = useState("");
  const [promptText, setPromptText] = useState("");
  const [promptCategoryId, setPromptCategoryId] = useState("");
  const [listFilter, setListFilter] = useState("all");
  const [newCatName, setNewCatName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  const currentTab = TABS.find((t) => t.key === tab)!;

  const { data, isLoading } = useQuery({
    queryKey: [...QUERY_KEY, tab],
    queryFn: () => listAdminMaterialLibrary({ category: tab, includeInactive: true }),
  });

  const items = useMemo(() => data?.items ?? [], [data?.items]);
  const promptCategories = useMemo(
    () => data?.promptCategories ?? [],
    [data?.promptCategories]
  );
  const visibleItems = useMemo(() => {
    if (tab !== "prompt" || listFilter === "all") return items;
    if (listFilter === "uncategorized") {
      return items.filter((item) => !String(item.promptCategoryId || "").trim());
    }
    return items.filter((item) => String(item.promptCategoryId || "") === listFilter);
  }, [tab, items, listFilter]);

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: QUERY_KEY });

  const toggleMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) =>
      updateAdminMaterialLibraryItem(id, { isActive }),
    onSuccess: () => {
      invalidate();
      toast.success("已更新");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "更新失败"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAdminMaterialLibraryItem(id),
    onSuccess: () => {
      invalidate();
      toast.success("已删除");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "删除失败"),
  });

  const itemCategoryMutation = useMutation({
    mutationFn: ({ id, promptCategoryId: next }: { id: string; promptCategoryId: string }) =>
      updateAdminMaterialLibraryItem(id, { promptCategoryId: next || null }),
    onSuccess: () => {
      invalidate();
      toast.success("分类已更新");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "分类更新失败"),
  });

  const createCatMutation = useMutation({
    mutationFn: (name: string) => createAdminPromptLibraryCategory({ name }),
    onSuccess: () => {
      setNewCatName("");
      invalidate();
      toast.success("分类已添加");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "添加失败"),
  });

  const updateCatMutation = useMutation({
    mutationFn: ({
      id,
      payload,
    }: {
      id: string;
      payload: { name?: string; isActive?: boolean };
    }) => updateAdminPromptLibraryCategory(id, payload),
    onSuccess: () => {
      invalidate();
      toast.success("分类已更新");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "更新失败"),
  });

  const deleteCatMutation = useMutation({
    mutationFn: (id: string) => deleteAdminPromptLibraryCategory(id),
    onSuccess: () => {
      invalidate();
      toast.success("分类已删除");
    },
    onError: (err) => toast.error(err instanceof Error ? err.message : "删除失败"),
  });

  const handleUpload = async () => {
    if (!file) {
      toast.error(
        currentTab.media === "effect" || currentTab.media === "prompt"
          ? "请选择图片或视频文件"
          : currentTab.media === "video"
            ? "请选择视频文件"
            : "请选择图片文件"
      );
      return;
    }
    const cleanTitle = title.trim();
    if (!cleanTitle) {
      toast.error("请填写标题");
      return;
    }
    const cleanPrompt = promptText.trim();
    if (tab === "prompt" && !cleanPrompt) {
      toast.error("请填写提示词正文");
      return;
    }
    // 风格/角色图，或特效/提示词库里的图片：走图片体积校验
    const isImageLike =
      currentTab.media === "image" ||
      ((currentTab.media === "effect" || currentTab.media === "prompt") &&
        (file.type.startsWith("image/") ||
          /\.(gif|webp|png|jpe?g|bmp)$/i.test(file.name)));
    if (isImageLike) {
      const sizeError = validateCanvasImageFile(file);
      if (sizeError) {
        toast.error(sizeError);
        return;
      }
    }
    setUploading(true);
    try {
      await createAdminMaterialLibraryItem({
        category: tab,
        title: cleanTitle,
        file,
        promptText: tab === "prompt" ? cleanPrompt : undefined,
        promptCategoryId: tab === "prompt" ? promptCategoryId || undefined : undefined,
      });
      setTitle("");
      setPromptText("");
      setFile(null);
      invalidate();
      toast.success("上传成功");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-6">
      <AdminHeader
        title="素材库"
        description="风格库 / 特效库 / 角色库 / 提示词库：仅管理员可上传；用户在画布侧栏只读选用。"
      />

      <div className="flex flex-wrap gap-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => {
              setTab(t.key);
              setTitle("");
              setPromptText("");
              setFile(null);
              setListFilter("all");
            }}
            className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
              tab === t.key
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <p className="text-xs text-muted-foreground">{currentTab.hint}</p>

      {tab === "prompt" ? (
        <PromptCategoryManager
          categories={promptCategories}
          newName={newCatName}
          onNewNameChange={setNewCatName}
          creating={createCatMutation.isPending}
          onCreate={() => {
            const name = newCatName.trim();
            if (!name) {
              toast.error("请填写分类名称");
              return;
            }
            createCatMutation.mutate(name);
          }}
          onRename={(id, name) => updateCatMutation.mutate({ id, payload: { name } })}
          onToggle={(id, isActive) => updateCatMutation.mutate({ id, payload: { isActive } })}
          onDelete={(id, name) => {
            if (!window.confirm(`确定删除分类「${name}」？其下提示词将变为未分类。`)) return;
            deleteCatMutation.mutate(id);
          }}
        />
      ) : null}

      <div className="rounded-xl border border-border bg-card p-4 space-y-3">
        <div className="text-sm font-medium">上传到{currentTab.label}</div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-[200px] flex-1 space-y-1">
            <span className="text-xs text-muted-foreground">标题</span>
            <input
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              value={title}
              maxLength={128}
              placeholder="显示名称"
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          {tab === "prompt" ? (
            <label className="min-w-[160px] space-y-1">
              <span className="text-xs text-muted-foreground">分类</span>
              <select
                className="h-9 w-full rounded-lg border border-border bg-background px-2 text-sm"
                value={promptCategoryId}
                onChange={(e) => setPromptCategoryId(e.target.value)}
              >
                <option value="">未分类</option>
                {promptCategories
                  .filter((c) => c.isActive)
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </label>
          ) : null}
          <label className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-accent">
            {currentTab.media === "image" ? (
              <ImagePlus className="h-3.5 w-3.5" />
            ) : (
              <Film className="h-3.5 w-3.5" />
            )}
            {file
              ? file.name
              : currentTab.media === "effect" || currentTab.media === "prompt"
                ? "选择图片/视频"
                : currentTab.media === "video"
                  ? "选择视频"
                  : "选择图片"}
            <input
              type="file"
              className="hidden"
              accept={
                currentTab.media === "effect"
                  ? EFFECT_ACCEPT
                  : currentTab.media === "prompt"
                    ? PROMPT_ACCEPT
                    : currentTab.media === "video"
                      ? "video/mp4,video/webm,video/quicktime,.mov,.mkv,.avi,.m4v"
                      : "image/jpeg,image/png,image/gif,image/webp,image/bmp"
              }
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <Button type="button" onClick={() => void handleUpload()} disabled={uploading}>
            {uploading ? (
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-1.5 h-4 w-4" />
            )}
            {uploading ? "上传中…" : "上传"}
          </Button>
        </div>
        {/* 提示词库：正文文本框（落画布可编辑文本节点） */}
        {tab === "prompt" ? (
          <label className="block space-y-1">
            <span className="text-xs text-muted-foreground">
              提示词正文（用户选用后写入可编辑文本节点）
            </span>
            <textarea
              className="min-h-[120px] w-full rounded-lg border border-border bg-background px-3 py-2 text-sm leading-relaxed"
              value={promptText}
              maxLength={PROMPT_TEXT_MAX}
              placeholder="填写提示词，例如画面描述、风格约束、镜头说明…"
              onChange={(e) => setPromptText(e.target.value)}
            />
            <span className="text-[10px] text-muted-foreground">
              {promptText.length}/{PROMPT_TEXT_MAX}
            </span>
          </label>
        ) : null}
      </div>

      {tab === "prompt" && promptCategories.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {[
            { id: "all", label: "全部" },
            ...promptCategories.map((c) => ({ id: c.id, label: c.isActive ? c.name : `${c.name}（下架）` })),
            { id: "uncategorized", label: "未分类" },
          ].map((chip) => (
            <button
              key={chip.id}
              type="button"
              onClick={() => setListFilter(chip.id)}
              className={`rounded-full px-2.5 py-1 text-xs transition-colors ${
                listFilter === chip.id
                  ? "bg-primary text-primary-foreground"
                  : "bg-muted text-muted-foreground hover:text-foreground"
              }`}
            >
              {chip.label}
            </button>
          ))}
        </div>
      ) : null}

      <div className="rounded-xl border border-border bg-card">
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载中…
          </div>
        ) : visibleItems.length === 0 ? (
          <div className="py-16 text-center text-sm text-muted-foreground">暂无素材，请先上传</div>
        ) : (
          <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {visibleItems.map((item) => (
              <MaterialCard
                key={item.id}
                item={item}
                promptCategories={promptCategories}
                onToggle={() =>
                  toggleMutation.mutate({ id: item.id, isActive: !item.isActive })
                }
                onChangeCategory={(next) =>
                  itemCategoryMutation.mutate({ id: item.id, promptCategoryId: next })
                }
                onDelete={() => {
                  if (!window.confirm(`确定删除「${item.title}」？`)) return;
                  deleteMutation.mutate(item.id);
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PromptCategoryManager({
  categories,
  newName,
  onNewNameChange,
  creating,
  onCreate,
  onRename,
  onToggle,
  onDelete,
}: {
  categories: AdminPromptLibraryCategory[];
  newName: string;
  onNewNameChange: (v: string) => void;
  creating: boolean;
  onCreate: () => void;
  onRename: (id: string, name: string) => void;
  onToggle: (id: string, isActive: boolean) => void;
  onDelete: (id: string, name: string) => void;
}) {
  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="text-sm font-medium">提示词库分类</div>
      <p className="text-xs text-muted-foreground">
        分类会出现在用户素材库「提示词库」顶部，便于筛选。删除分类不会删除提示词。
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <label className="min-w-[200px] flex-1 space-y-1">
          <span className="text-xs text-muted-foreground">新分类名称</span>
          <input
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            value={newName}
            maxLength={32}
            placeholder="例如：明信片 / 电影感 / 电商"
            onChange={(e) => onNewNameChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onCreate();
              }
            }}
          />
        </label>
        <Button type="button" variant="secondary" onClick={onCreate} disabled={creating}>
          {creating ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <Plus className="mr-1.5 h-4 w-4" />}
          添加分类
        </Button>
      </div>
      {categories.length === 0 ? (
        <p className="text-xs text-muted-foreground">暂无分类，添加后可在上传时选择。</p>
      ) : (
        <ul className="space-y-2">
          {categories.map((cat) => (
            <li
              key={cat.id}
              className="flex flex-wrap items-center gap-2 rounded-lg border border-border/70 px-3 py-2"
            >
              <input
                className="min-w-[140px] flex-1 rounded-md border border-border bg-background px-2 py-1 text-sm"
                defaultValue={cat.name}
                maxLength={32}
                onBlur={(e) => {
                  const next = e.target.value.trim();
                  if (!next || next === cat.name) {
                    e.target.value = cat.name;
                    return;
                  }
                  onRename(cat.id, next);
                }}
              />
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground"
                onClick={() => onToggle(cat.id, !cat.isActive)}
              >
                {cat.isActive ? "已上架" : "已下架"}
              </button>
              <button
                type="button"
                className="text-muted-foreground hover:text-destructive"
                title="删除分类"
                onClick={() => onDelete(cat.id, cat.name)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MaterialCard({
  item,
  promptCategories,
  onToggle,
  onChangeCategory,
  onDelete,
}: {
  item: AdminMaterialLibraryItem;
  promptCategories: AdminPromptLibraryCategory[];
  onToggle: () => void;
  onChangeCategory: (categoryId: string) => void;
  onDelete: () => void;
}) {
  const promptPreview = String(item.promptText || "").trim();
  return (
    <div
      className={`group relative overflow-hidden rounded-lg border bg-muted/20 ${
        item.isActive ? "border-border" : "border-dashed border-border opacity-60"
      }`}
    >
      {/* 封面统一 1:1；提示词库横竖图 object-contain 适应 */}
      <div className="aspect-square bg-black/5">
        {item.mediaType === "video" ? (
          <video
            src={item.mediaUrl}
            className={`h-full w-full ${
              item.category === "prompt" ? "object-contain" : "object-cover"
            }`}
            muted
            playsInline
            loop
            preload="metadata"
          />
        ) : (
          // 管理端预览运营素材，不限定 Next Image 域名
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={item.mediaUrl}
            alt=""
            className={`h-full w-full ${
              item.category === "prompt" ? "object-contain" : "object-cover"
            }`}
          />
        )}
      </div>
      <div className="space-y-1 p-2">
        <div className="truncate text-xs font-medium" title={item.title}>
          {item.title}
        </div>
        {item.category === "prompt" ? (
          <select
            className="w-full rounded border border-border bg-background px-1 py-0.5 text-[10px]"
            value={item.promptCategoryId || ""}
            onChange={(e) => onChangeCategory(e.target.value)}
            title="所属分类"
          >
            <option value="">未分类</option>
            {promptCategories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.isActive ? c.name : `${c.name}（下架）`}
              </option>
            ))}
          </select>
        ) : null}
        {item.category === "prompt" && promptPreview ? (
          <p className="line-clamp-2 text-[10px] text-muted-foreground" title={promptPreview}>
            {promptPreview}
          </p>
        ) : null}
        <div className="flex items-center justify-between gap-1">
          <button
            type="button"
            onClick={onToggle}
            className="text-[10px] text-muted-foreground hover:text-foreground"
          >
            {item.isActive ? "已上架" : "已下架"}
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
            title="删除"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
