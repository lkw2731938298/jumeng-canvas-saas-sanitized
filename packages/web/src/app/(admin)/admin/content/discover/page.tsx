"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Film, ImagePlus, Loader2, Plus, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { validateCanvasImageFile } from "@/lib/canvas/imageSizePolicy";
import {
  getAdminDiscoverPage,
  putAdminDiscoverPage,
  uploadAdminDiscoverImage,
  uploadAdminDiscoverVideo,
} from "@/lib/api/admin";
import type {
  DiscoverGalleryItem,
  DiscoverHighlightFeature,
  DiscoverHeroVideo,
  DiscoverPageSettings,
  DiscoverPromptSuggestion,
} from "@/types/admin";
import { normalizeDiscoverCornerPopup } from "@/components/huabu/DiscoverCornerPopup";

function newId() {
  return crypto.randomUUID().replace(/-/g, "");
}

type ImageUploadFieldProps = {
  label: string;
  value: string;
  onChange: (url: string) => void;
  previewClassName?: string;
};

/** 发现页图片字段：支持本地上传到 OSS，也保留 URL 手工输入。 */
function ImageUploadField({
  label,
  value,
  onChange,
  previewClassName = "h-24 w-40",
}: ImageUploadFieldProps) {
  const [uploading, setUploading] = useState(false);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    const sizeError = validateCanvasImageFile(file);
    if (sizeError) {
      toast.error(sizeError);
      return;
    }
    setUploading(true);
    try {
      const uploaded = await uploadAdminDiscoverImage(file);
      onChange(uploaded.imageUrl);
      toast.success("图片上传成功");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "图片上传失败");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="space-y-2">
      <span className="block text-xs text-muted-foreground">{label}</span>
      <div className="flex flex-wrap items-center gap-3">
        {value ? (
          // 管理端需预览任意 OSS/外部运营图片，不能限定 Next Image 域名。
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={value}
            alt=""
            className={`${previewClassName} rounded-lg border border-border bg-muted object-cover`}
          />
        ) : (
          <div
            className={`${previewClassName} grid place-items-center rounded-lg border border-dashed border-border bg-muted/30 text-muted-foreground`}
          >
            <ImagePlus className="h-5 w-5" />
          </div>
        )}
        <div className="min-w-[220px] flex-1 space-y-2">
          <input
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            placeholder="图片 URL（也可点击上传）"
            value={value}
            onChange={(e) => onChange(e.target.value)}
          />
          <div className="flex gap-2">
            <label className="inline-flex h-8 cursor-pointer items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-accent">
              {uploading ? (
                <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              ) : (
                <ImagePlus className="mr-1.5 h-3.5 w-3.5" />
              )}
              {uploading ? "上传中…" : "上传图片"}
              <input
                type="file"
                accept="image/jpeg,image/png,image/gif,image/webp,image/bmp"
                className="hidden"
                disabled={uploading}
                onChange={(e) => {
                  void upload(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </label>
            {value ? (
              <Button type="button" size="sm" variant="ghost" onClick={() => onChange("")}>
                清除
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Hero 背景视频单条上限（与后端 DISCOVER_VIDEO_MAX_BYTES 对齐） */
const DISCOVER_HERO_VIDEO_MAX_BYTES = 80 * 1024 * 1024;
const DISCOVER_HERO_VIDEO_ACCEPT = "video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov";

/** 管理后台：发现页 Hero / 提示词 / 技能亮点 / 作品广场运营配置 */
export default function AdminDiscoverPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "discover-page"],
    queryFn: getAdminDiscoverPage,
  });
  const [draft, setDraft] = useState<DiscoverPageSettings | null>(null);
  const [heroVideoUploading, setHeroVideoUploading] = useState(false);

  useEffect(() => {
    if (data) {
      const next = structuredClone(data);
      next.cornerPopup = normalizeDiscoverCornerPopup(next.cornerPopup);
      setDraft(next);
    }
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: (payload: DiscoverPageSettings) => {
      // 保存前去掉空视频行，避免后端 url min_length 校验失败
      const heroVideos = (payload.heroVideos ?? [])
        .filter((v) => Boolean(String(v.url || "").trim()))
        .map((v, i) => ({ ...v, sortOrder: i }));
      return putAdminDiscoverPage({ ...payload, heroVideos });
    },
    onSuccess: async (saved) => {
      setDraft(structuredClone(saved));
      await queryClient.invalidateQueries({ queryKey: ["admin", "discover-page"] });
      await queryClient.invalidateQueries({ queryKey: ["site", "discover"] });
      await queryClient.invalidateQueries({ queryKey: ["workflow-publications"] });
      toast.success("发现页内容已保存");
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const filtersText = useMemo(
    () => (draft?.galleryFilters ?? []).join("\n"),
    [draft?.galleryFilters]
  );

  if (isLoading || !draft) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载发现页配置…
      </div>
    );
  }

  const update = (patch: Partial<DiscoverPageSettings>) =>
    setDraft((prev) => (prev ? { ...prev, ...patch } : prev));

  /** 上传本地视频到 OSS，并写入 / 更新 heroVideos 条目 */
  const uploadHeroVideo = async (file: File | undefined, replaceIndex?: number) => {
    if (!file || !draft) return;
    if (!file.type.startsWith("video/") && !/\.(mp4|webm|mov)$/i.test(file.name)) {
      toast.error("请选择 MP4 / WebM / MOV 视频");
      return;
    }
    if (file.size > DISCOVER_HERO_VIDEO_MAX_BYTES) {
      toast.error("视频不能超过 80MB");
      return;
    }
    setHeroVideoUploading(true);
    try {
      const uploaded = await uploadAdminDiscoverVideo(file);
      const url = uploaded.videoUrl;
      if (!url) {
        toast.error("上传成功但未返回视频地址");
        return;
      }
      if (typeof replaceIndex === "number" && replaceIndex >= 0) {
        const next = [...draft.heroVideos];
        const prev = next[replaceIndex];
        if (prev) {
          next[replaceIndex] = { ...prev, url };
          update({ heroVideos: next });
        }
      } else {
        update({
          heroVideos: [
            ...draft.heroVideos,
            {
              id: newId(),
              url,
              sortOrder: draft.heroVideos.length,
            } satisfies DiscoverHeroVideo,
          ],
        });
      }
      toast.success("视频上传成功，记得保存配置");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "视频上传失败");
    } finally {
      setHeroVideoUploading(false);
    }
  };

  return (
    <div className="p-8">
      <AdminHeader
        title="发现页内容"
        description="配置用户站首页（/）的 Hero、提示词芯片、技能亮点与作品广场。活动预览自动读取「算力活动」。"
      />

      <div className="mt-6 space-y-8">
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <h2 className="mb-1 text-base font-semibold">右上角弹窗</h2>
          <p className="mb-4 text-xs text-muted-foreground">
            出现在发现页右上角、顶栏下方，无全屏遮罩。关闭后下次进入发现页仍会再弹出。
          </p>
          <label className="mb-4 flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={Boolean(draft.cornerPopup?.enabled)}
              onChange={(e) =>
                update({
                  cornerPopup: {
                    ...normalizeDiscoverCornerPopup(draft.cornerPopup),
                    enabled: e.target.checked,
                  },
                })
              }
            />
            启用弹窗
          </label>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.cornerPopup?.showWhenLoggedIn !== false}
                onChange={(e) =>
                  update({
                    cornerPopup: {
                      ...normalizeDiscoverCornerPopup(draft.cornerPopup),
                      showWhenLoggedIn: e.target.checked,
                    },
                  })
                }
              />
              登录后显示
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.cornerPopup?.showWhenLoggedOut !== false}
                onChange={(e) =>
                  update({
                    cornerPopup: {
                      ...normalizeDiscoverCornerPopup(draft.cornerPopup),
                      showWhenLoggedOut: e.target.checked,
                    },
                  })
                }
              />
              未登录显示
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">弹窗比例</span>
              <select
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.cornerPopup?.aspectRatio === "9:16" ? "9:16" : "16:9"}
                onChange={(e) =>
                  update({
                    cornerPopup: {
                      ...normalizeDiscoverCornerPopup(draft.cornerPopup),
                      aspectRatio: e.target.value === "9:16" ? "9:16" : "16:9",
                    },
                  })
                }
              >
                <option value="16:9">16:9 横版</option>
                <option value="9:16">9:16 竖版</option>
              </select>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={Boolean(draft.cornerPopup?.imageFill)}
                onChange={(e) =>
                  update({
                    cornerPopup: {
                      ...normalizeDiscoverCornerPopup(draft.cornerPopup),
                      imageFill: e.target.checked,
                    },
                  })
                }
              />
              图片填满整个弹窗
            </label>
            <label className="text-sm md:col-span-2">
              <span className="mb-1 block text-muted-foreground">文案</span>
              <textarea
                className="min-h-[88px] w-full rounded-lg border border-border bg-background px-3 py-2"
                maxLength={2000}
                placeholder="可写公告、活动说明等"
                value={draft.cornerPopup?.text ?? ""}
                onChange={(e) =>
                  update({
                    cornerPopup: {
                      ...normalizeDiscoverCornerPopup(draft.cornerPopup),
                      text: e.target.value,
                    },
                  })
                }
              />
            </label>
            <div className="md:col-span-2">
              <ImageUploadField
                label="弹窗图片（可选）"
                value={draft.cornerPopup?.imageUrl ?? ""}
                previewClassName={
                  draft.cornerPopup?.aspectRatio === "9:16" ? "h-40 w-24" : "h-24 w-40"
                }
                onChange={(imageUrl) =>
                  update({
                    cornerPopup: {
                      ...normalizeDiscoverCornerPopup(draft.cornerPopup),
                      imageUrl,
                    },
                  })
                }
              />
            </div>
          </div>
        </section>

        {/* Hero */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <h2 className="mb-4 text-base font-semibold">Hero 区</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">主标题</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.heroTitle}
                onChange={(e) => update({ heroTitle: e.target.value })}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">强调文案</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.heroEm}
                onChange={(e) => update({ heroEm: e.target.value })}
              />
            </label>
            <label className="text-sm md:col-span-2">
              <span className="mb-1 block text-muted-foreground">创作框占位文案</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.creationPlaceholder}
                onChange={(e) => update({ creationPlaceholder: e.target.value })}
              />
            </label>
          </div>

          <div className="mt-5">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium">背景视频（可多条，随机/轮播；支持本地上传）</h3>
              <div className="flex flex-wrap items-center gap-2">
                <label className="inline-flex h-7 cursor-pointer items-center justify-center gap-1 rounded-[min(var(--radius-md),12px)] border border-border bg-background px-2.5 text-[0.8rem] font-medium hover:bg-muted disabled:opacity-50">
                  {heroVideoUploading ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Upload className="h-3.5 w-3.5" />
                  )}
                  {heroVideoUploading ? "上传中…" : "添加视频"}
                  <input
                    type="file"
                    accept={DISCOVER_HERO_VIDEO_ACCEPT}
                    className="hidden"
                    disabled={heroVideoUploading}
                    onChange={(e) => {
                      void uploadHeroVideo(e.target.files?.[0]);
                      e.target.value = "";
                    }}
                  />
                </label>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={heroVideoUploading}
                  onClick={() =>
                    update({
                      heroVideos: [
                        ...draft.heroVideos,
                        {
                          id: newId(),
                          url: "",
                          sortOrder: draft.heroVideos.length,
                        } satisfies DiscoverHeroVideo,
                      ],
                    })
                  }
                >
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  添加 URL
                </Button>
              </div>
            </div>
            <div className="space-y-3">
              {draft.heroVideos.map((video, index) => (
                <div
                  key={video.id}
                  className="flex flex-wrap items-start gap-3 rounded-lg border border-border/60 p-3"
                >
                  {video.url ? (
                    <video
                      src={video.url}
                      muted
                      playsInline
                      preload="metadata"
                      className="h-20 w-36 rounded-md border border-border bg-muted object-cover"
                    />
                  ) : (
                    <div className="grid h-20 w-36 place-items-center rounded-md border border-dashed border-border bg-muted/30 text-muted-foreground">
                      <Film className="h-5 w-5" />
                    </div>
                  )}
                  <div className="min-w-[220px] flex-1 space-y-2">
                    <input
                      className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                      placeholder="https://.../v1.mp4（或点击上传）"
                      value={video.url}
                      onChange={(e) => {
                        const next = [...draft.heroVideos];
                        next[index] = { ...video, url: e.target.value };
                        update({ heroVideos: next });
                      }}
                    />
                    <div className="flex flex-wrap gap-2">
                      <label className="inline-flex h-8 cursor-pointer items-center rounded-md border border-input bg-background px-3 text-xs font-medium hover:bg-accent">
                        {heroVideoUploading ? (
                          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Upload className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        {heroVideoUploading ? "上传中…" : "上传视频"}
                        <input
                          type="file"
                          accept={DISCOVER_HERO_VIDEO_ACCEPT}
                          className="hidden"
                          disabled={heroVideoUploading}
                          onChange={(e) => {
                            void uploadHeroVideo(e.target.files?.[0], index);
                            e.target.value = "";
                          }}
                        />
                      </label>
                      <Button
                        type="button"
                        size="icon"
                        variant="ghost"
                        disabled={heroVideoUploading}
                        onClick={() =>
                          update({
                            heroVideos: draft.heroVideos.filter((v) => v.id !== video.id),
                          })
                        }
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
              {draft.heroVideos.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  未配置时使用渐变光晕背景。点「添加视频」可直接上传 MP4/WebM/MOV（≤80MB）。
                </p>
              )}
            </div>
          </div>
        </section>

        {/* Prompt chips */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-base font-semibold">提示词芯片（技能按钮）</h2>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                update({
                  promptSuggestions: [
                    ...draft.promptSuggestions,
                    {
                      id: newId(),
                      label: "新技能",
                      imageUrl: "",
                      sortOrder: draft.promptSuggestions.length,
                    } satisfies DiscoverPromptSuggestion,
                  ],
                })
              }
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              添加
            </Button>
          </div>
          <div className="space-y-3">
            {draft.promptSuggestions.map((item, index) => (
              <div key={item.id} className="rounded-lg border border-border/60 p-3">
                <div className="mb-3 flex items-center gap-2">
                  <input
                    className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    placeholder="文案"
                    value={item.label}
                    onChange={(e) => {
                      const next = [...draft.promptSuggestions];
                      next[index] = { ...item, label: e.target.value };
                      update({ promptSuggestions: next });
                    }}
                  />
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    onClick={() =>
                      update({
                        promptSuggestions: draft.promptSuggestions.filter(
                          (x) => x.id !== item.id
                        ),
                      })
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                <ImageUploadField
                  label="技能按钮缩略图"
                  value={item.imageUrl}
                  previewClassName="h-16 w-16"
                  onChange={(imageUrl) => {
                    const next = [...draft.promptSuggestions];
                    next[index] = { ...item, imageUrl };
                    update({ promptSuggestions: next });
                  }}
                />
              </div>
            ))}
          </div>
        </section>

        {/* Skill highlights */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <h2 className="mb-4 text-base font-semibold">技能亮点</h2>
          <div className="mb-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">故事大卡标题</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.storyFeature.label}
                onChange={(e) =>
                  update({ storyFeature: { ...draft.storyFeature, label: e.target.value } })
                }
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">Skill 制造机标题</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.skillMachine.label}
                onChange={(e) =>
                  update({ skillMachine: { ...draft.skillMachine, label: e.target.value } })
                }
              />
            </label>
          </div>
          <div className="mb-5">
            <ImageUploadField
              label="故事大卡封面"
              value={draft.storyFeature.imageUrl}
              previewClassName="h-28 w-52"
              onChange={(imageUrl) =>
                update({ storyFeature: { ...draft.storyFeature, imageUrl } })
              }
            />
          </div>
          <div className="mb-5">
            <ImageUploadField
              label="Skill 技能制造机图片"
              value={draft.skillMachine.imageUrl}
              previewClassName="h-28 w-52"
              onChange={(imageUrl) =>
                update({ skillMachine: { ...draft.skillMachine, imageUrl } })
              }
            />
          </div>

          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-medium">迷你技能卡（建议 4 个）</h3>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                update({
                  highlightFeatures: [
                    ...draft.highlightFeatures,
                    {
                      id: newId(),
                      label: "新亮点",
                      subtitle: "",
                      tone: "purple",
                      isNew: false,
                      href: "/skills",
                      sortOrder: draft.highlightFeatures.length,
                    } satisfies DiscoverHighlightFeature,
                  ],
                })
              }
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              添加
            </Button>
          </div>
          <div className="space-y-2">
            {draft.highlightFeatures.map((item, index) => (
              <div
                key={item.id}
                className="grid gap-2 rounded-lg border border-border/60 p-3 md:grid-cols-[1.2fr_1.2fr_120px_80px_auto]"
              >
                <input
                  className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  placeholder="标题"
                  value={item.label}
                  onChange={(e) => {
                    const next = [...draft.highlightFeatures];
                    next[index] = { ...item, label: e.target.value };
                    update({ highlightFeatures: next });
                  }}
                />
                <input
                  className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
                  placeholder="小文案（副标题）"
                  maxLength={64}
                  value={item.subtitle || ""}
                  onChange={(e) => {
                    const next = [...draft.highlightFeatures];
                    next[index] = { ...item, subtitle: e.target.value };
                    update({ highlightFeatures: next });
                  }}
                />
                <select
                  className="rounded-lg border border-border bg-background px-2 py-2 text-sm"
                  value={item.tone}
                  onChange={(e) => {
                    const next = [...draft.highlightFeatures];
                    next[index] = { ...item, tone: e.target.value };
                    update({ highlightFeatures: next });
                  }}
                >
                  <option value="purple">purple</option>
                  <option value="blue">blue</option>
                  <option value="indigo">indigo</option>
                  <option value="gold">gold</option>
                </select>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={item.isNew}
                    onChange={(e) => {
                      const next = [...draft.highlightFeatures];
                      next[index] = { ...item, isNew: e.target.checked };
                      update({ highlightFeatures: next });
                    }}
                  />
                  New
                </label>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  onClick={() =>
                    update({
                      highlightFeatures: draft.highlightFeatures.filter((x) => x.id !== item.id),
                    })
                  }
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
        </section>

        {/* Gallery */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <h2 className="mb-4 text-base font-semibold">作品广场</h2>
          <label className="mb-4 block text-sm">
            <span className="mb-1 block text-muted-foreground">分类（每行一个，须含「全部」）</span>
            <textarea
              className="min-h-[120px] w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm"
              value={filtersText}
              onChange={(e) =>
                update({
                  galleryFilters: e.target.value
                    .split("\n")
                    .map((x) => x.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>

          <div className="mb-2 flex items-center justify-between">
            <h3 className="text-sm font-medium">作品列表</h3>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                update({
                  galleryItems: [
                    ...draft.galleryItems,
                    {
                      id: newId(),
                      title: "新作品",
                      author: "匿名",
                      avatarUrl: "",
                      caption: "",
                      imageUrl: "",
                      category: "全部",
                      scope: "templates",
                      sortOrder: draft.galleryItems.length,
                      isActive: true,
                    } satisfies DiscoverGalleryItem,
                  ],
                })
              }
            >
              <Plus className="mr-1 h-3.5 w-3.5" />
              添加作品
            </Button>
          </div>
          <div className="max-h-[520px] space-y-3 overflow-y-auto pr-1">
            {draft.galleryItems.map((item, index) => (
              <div key={item.id} className="rounded-lg border border-border/60 p-3">
                <div className="mb-2 grid gap-2 md:grid-cols-4">
                  <input
                    className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    placeholder="标题"
                    value={item.title}
                    onChange={(e) => {
                      const next = [...draft.galleryItems];
                      next[index] = { ...item, title: e.target.value };
                      update({ galleryItems: next });
                    }}
                  />
                  <input
                    className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    placeholder="作者"
                    value={item.author}
                    onChange={(e) => {
                      const next = [...draft.galleryItems];
                      next[index] = { ...item, author: e.target.value };
                      update({ galleryItems: next });
                    }}
                  />
                  <select
                    className="rounded-lg border border-border bg-background px-2 py-2 text-sm"
                    value={item.category}
                    onChange={(e) => {
                      const next = [...draft.galleryItems];
                      next[index] = { ...item, category: e.target.value };
                      update({ galleryItems: next });
                    }}
                  >
                    {draft.galleryFilters.map((f) => (
                      <option key={f} value={f}>
                        {f}
                      </option>
                    ))}
                  </select>
                  <select
                    className="rounded-lg border border-border bg-background px-2 py-2 text-sm"
                    value={item.scope}
                    onChange={(e) => {
                      const next = [...draft.galleryItems];
                      next[index] = { ...item, scope: e.target.value };
                      update({ galleryItems: next });
                    }}
                  >
                    <option value="templates">全部工作流</option>
                    <option value="published">我的发布</option>
                    <option value="purchased">我的使用</option>
                    <option value="all">全部 Tab 可见</option>
                  </select>
                </div>
                <div className="mb-4 grid gap-2 md:grid-cols-[1fr_auto_auto]">
                  <input
                    className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    placeholder="说明 caption"
                    value={item.caption}
                    onChange={(e) => {
                      const next = [...draft.galleryItems];
                      next[index] = { ...item, caption: e.target.value };
                      update({ galleryItems: next });
                    }}
                  />
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={item.isActive}
                      onChange={(e) => {
                        const next = [...draft.galleryItems];
                        next[index] = { ...item, isActive: e.target.checked };
                        update({ galleryItems: next });
                      }}
                    />
                    上架
                  </label>
                  <Button
                    type="button"
                    size="icon"
                    variant="ghost"
                    onClick={() =>
                      update({
                        galleryItems: draft.galleryItems.filter((x) => x.id !== item.id),
                      })
                    }
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
                <div className="grid gap-4 xl:grid-cols-2">
                  <ImageUploadField
                    label="作品封面"
                    value={item.imageUrl}
                    previewClassName="h-24 w-40"
                    onChange={(imageUrl) => {
                      const next = [...draft.galleryItems];
                      next[index] = { ...item, imageUrl };
                      update({ galleryItems: next });
                    }}
                  />
                  <ImageUploadField
                    label="作者头像"
                    value={item.avatarUrl}
                    previewClassName="h-16 w-16 rounded-full"
                    onChange={(avatarUrl) => {
                      const next = [...draft.galleryItems];
                      next[index] = { ...item, avatarUrl };
                      update({ galleryItems: next });
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        <div className="sticky bottom-4 flex justify-end gap-3 rounded-xl border border-border bg-background/95 p-4 shadow-lg backdrop-blur">
          <p className="mr-auto self-center text-xs text-muted-foreground">
            活动预览请到「算力活动」维护；发现页自动展示进行中活动。
          </p>
          <Button
            type="button"
            disabled={saveMutation.isPending}
            onClick={() => saveMutation.mutate(draft)}
          >
            {saveMutation.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                保存中…
              </>
            ) : (
              "保存发现页配置"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
