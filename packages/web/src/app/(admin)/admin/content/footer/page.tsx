"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ImagePlus, Loader2, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { validateCanvasImageFile } from "@/lib/canvas/imageSizePolicy";
import {
  getAdminSiteFooter,
  putAdminSiteFooter,
  uploadAdminFooterImage,
} from "@/lib/api/admin";
import type {
  SiteFooterFriendLink,
  SiteFooterQrCode,
  SiteFooterSettings,
  SiteFooterSocialLink,
} from "@/types/admin";

function newId() {
  return crypto.randomUUID().replace(/-/g, "");
}

/** 兼容旧配置缺省友情链接 / 备案字段 */
function normalizeFooterDraft(raw: SiteFooterSettings): SiteFooterSettings {
  return {
    ...raw,
    friendLinks: Array.isArray(raw.friendLinks) ? raw.friendLinks : [],
    friendLinksLabel: raw.friendLinksLabel?.trim() || "友情链接",
    icpNumber: raw.icpNumber ?? "",
    icpHref: raw.icpHref?.trim() || "https://beian.miit.gov.cn/",
  };
}

type ImageUploadFieldProps = {
  label: string;
  value: string;
  onChange: (url: string) => void;
  previewClassName?: string;
};

/** Footer 二维码图片：本地上传 OSS 或粘贴 URL */
function ImageUploadField({
  label,
  value,
  onChange,
  previewClassName = "h-28 w-28",
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
      const uploaded = await uploadAdminFooterImage(file);
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

/** 管理后台：首页 Footer（关于我们 / 联系我们二维码 / 社交跳转） */
export default function AdminFooterContentPage() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin", "site-footer"],
    queryFn: getAdminSiteFooter,
  });
  const [draft, setDraft] = useState<SiteFooterSettings | null>(null);

  useEffect(() => {
    if (data) setDraft(normalizeFooterDraft(structuredClone(data)));
  }, [data]);

  const saveMutation = useMutation({
    mutationFn: putAdminSiteFooter,
    onSuccess: async (saved) => {
      setDraft(normalizeFooterDraft(structuredClone(saved)));
      await queryClient.invalidateQueries({ queryKey: ["admin", "site-footer"] });
      await queryClient.invalidateQueries({ queryKey: ["site", "footer"] });
      toast.success("首页内容已保存");
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  if (isLoading || !draft) {
    return (
      <div className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载首页内容配置…
      </div>
    );
  }

  const update = (patch: Partial<SiteFooterSettings>) =>
    setDraft((prev) => (prev ? { ...prev, ...patch } : prev));

  const updateQr = (index: number, patch: Partial<SiteFooterQrCode>) => {
    const next = draft.contactUs.qrCodes.map((item, i) =>
      i === index ? { ...item, ...patch } : item
    );
    update({ contactUs: { ...draft.contactUs, qrCodes: next } });
  };

  const updateSocial = (index: number, patch: Partial<SiteFooterSocialLink>) => {
    const next = draft.socialLinks.map((item, i) => (i === index ? { ...item, ...patch } : item));
    update({ socialLinks: next });
  };

  const updateFriend = (index: number, patch: Partial<SiteFooterFriendLink>) => {
    const next = (draft.friendLinks ?? []).map((item, i) =>
      i === index ? { ...item, ...patch } : item
    );
    update({ friendLinks: next });
  };

  return (
    <div className="p-8">
      <AdminHeader
        title="首页内容"
        description="配置顶栏/侧栏教程链接、发现页 Footer：关于我们、联系我们二维码、社交平台跳转、友情链接。"
      />

      <div className="mt-6 space-y-8">
        {/* 顶栏 / 侧栏教程（同一 helpUrl） */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <h2 className="mb-4 text-base font-semibold">教程按钮（顶栏 / 侧栏）</h2>
          <p className="mb-4 text-xs text-muted-foreground">
            用户点击顶栏「？」或侧栏「教程」时打开此链接。建议填写完整 https://
            地址；也可填站内路径如 /skills。留空则提示暂未配置。
          </p>
          <label className="block text-sm md:w-2/3">
            <span className="mb-1 block text-muted-foreground">教程链接</span>
            <input
              className="w-full rounded-lg border border-border bg-background px-3 py-2"
              placeholder="https://..."
              value={draft.helpUrl || ""}
              onChange={(e) => update({ helpUrl: e.target.value })}
            />
          </label>
        </section>

        {/* 品牌文案 */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <h2 className="mb-4 text-base font-semibold">品牌与版权</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm md:col-span-2">
              <span className="mb-1 block text-muted-foreground">品牌文案</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.brandText}
                onChange={(e) => update({ brandText: e.target.value })}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">版权信息</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.copyright}
                onChange={(e) => update({ copyright: e.target.value })}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">标语</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.tagline}
                onChange={(e) => update({ tagline: e.target.value })}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">ICP 备案号</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                placeholder="皖ICP备xxxxxxxx号-xx"
                value={draft.icpNumber || ""}
                onChange={(e) => update({ icpNumber: e.target.value })}
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">备案查询链接</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                placeholder="https://beian.miit.gov.cn/"
                value={draft.icpHref || ""}
                onChange={(e) => update({ icpHref: e.target.value })}
              />
            </label>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            备案号展示在发现页 footer 底部；留空则不显示。默认跳转工信部备案查询。
          </p>
        </section>

        {/* 关于我们 */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <h2 className="mb-4 text-base font-semibold">关于我们</h2>
          <p className="mb-4 text-xs text-muted-foreground">
            用户点击后跳转到下方网页链接（建议填写完整 https:// 地址）。
          </p>
          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">显示文案</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                value={draft.aboutUs.label}
                onChange={(e) =>
                  update({ aboutUs: { ...draft.aboutUs, label: e.target.value } })
                }
              />
            </label>
            <label className="text-sm">
              <span className="mb-1 block text-muted-foreground">跳转链接</span>
              <input
                className="w-full rounded-lg border border-border bg-background px-3 py-2"
                placeholder="https://..."
                value={draft.aboutUs.href}
                onChange={(e) =>
                  update({ aboutUs: { ...draft.aboutUs, href: e.target.value } })
                }
              />
            </label>
          </div>
        </section>

        {/* 联系我们 · 二维码 */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">联系我们 · 微信二维码</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                同时用于：左侧栏「微信」按钮悬停弹层、发现页底部「联系我们」。可上传多个（如官方资讯、活动群、企微等）。
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                update({
                  contactUs: {
                    ...draft.contactUs,
                    qrCodes: [
                      ...draft.contactUs.qrCodes,
                      {
                        id: newId(),
                        label: `二维码${draft.contactUs.qrCodes.length + 1}`,
                        imageUrl: "",
                        sortOrder: draft.contactUs.qrCodes.length,
                      },
                    ],
                  },
                })
              }
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              添加二维码
            </Button>
          </div>

          <label className="mb-4 block text-sm md:w-1/2">
            <span className="mb-1 block text-muted-foreground">显示文案</span>
            <input
              className="w-full rounded-lg border border-border bg-background px-3 py-2"
              value={draft.contactUs.label}
              onChange={(e) =>
                update({ contactUs: { ...draft.contactUs, label: e.target.value } })
              }
            />
          </label>

          {draft.contactUs.qrCodes.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
              暂无二维码，请点击「添加二维码」上传
            </p>
          ) : (
            <div className="space-y-4">
              {draft.contactUs.qrCodes.map((qr, index) => (
                <div
                  key={qr.id}
                  className="rounded-lg border border-border bg-background/50 p-4"
                >
                  <div className="mb-3 flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">二维码 #{index + 1}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="text-destructive"
                      onClick={() =>
                        update({
                          contactUs: {
                            ...draft.contactUs,
                            qrCodes: draft.contactUs.qrCodes.filter((_, i) => i !== index),
                          },
                        })
                      }
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" />
                      删除
                    </Button>
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <label className="text-sm">
                      <span className="mb-1 block text-muted-foreground">名称（如微信客服）</span>
                      <input
                        className="w-full rounded-lg border border-border bg-background px-3 py-2"
                        value={qr.label}
                        onChange={(e) => updateQr(index, { label: e.target.value })}
                      />
                    </label>
                    <label className="text-sm">
                      <span className="mb-1 block text-muted-foreground">排序</span>
                      <input
                        type="number"
                        min={0}
                        className="w-full rounded-lg border border-border bg-background px-3 py-2"
                        value={qr.sortOrder}
                        onChange={(e) =>
                          updateQr(index, { sortOrder: Number(e.target.value) || 0 })
                        }
                      />
                    </label>
                    <div className="md:col-span-2">
                      <ImageUploadField
                        label="二维码图片"
                        value={qr.imageUrl}
                        onChange={(imageUrl) => updateQr(index, { imageUrl })}
                      />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* 社交关注 */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">关注 · 社交跳转</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                配置小红书 / 哔哩哔哩 / 抖音 / 视频号等跳转链接；可增删条目。
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                update({
                  socialLinks: [
                    ...draft.socialLinks,
                    {
                      id: newId(),
                      label: "新平台",
                      href: "",
                      sortOrder: draft.socialLinks.length,
                    },
                  ],
                })
              }
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              添加平台
            </Button>
          </div>

          <div className="space-y-3">
            {draft.socialLinks.map((link, index) => (
              <div
                key={link.id}
                className="grid items-end gap-3 rounded-lg border border-border bg-background/50 p-3 md:grid-cols-[1fr_2fr_100px_auto]"
              >
                <label className="text-sm">
                  <span className="mb-1 block text-muted-foreground">名称</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background px-3 py-2"
                    value={link.label}
                    onChange={(e) => updateSocial(index, { label: e.target.value })}
                  />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-muted-foreground">跳转链接</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background px-3 py-2"
                    placeholder="https://..."
                    value={link.href}
                    onChange={(e) => updateSocial(index, { href: e.target.value })}
                  />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-muted-foreground">排序</span>
                  <input
                    type="number"
                    min={0}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2"
                    value={link.sortOrder}
                    onChange={(e) =>
                      updateSocial(index, { sortOrder: Number(e.target.value) || 0 })
                    }
                  />
                </label>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-destructive"
                  onClick={() =>
                    update({
                      socialLinks: draft.socialLinks.filter((_, i) => i !== index),
                    })
                  }
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
          </div>
        </section>

        {/* 友情链接：发现页 footer 底部「友情链接」+ 紫色横排链接 */}
        <section className="rounded-xl border border-border bg-card/40 p-5">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">友情链接</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                展示在发现页底部；前缀「友情链接」后接主题紫色横排文字链。仅保存带 https 链接的条目，空列表则不展示。
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                update({
                  friendLinks: [
                    ...(draft.friendLinks ?? []),
                    {
                      id: newId(),
                      label: "新友链",
                      href: "https://",
                      sortOrder: (draft.friendLinks ?? []).length,
                    },
                  ],
                })
              }
            >
              <Plus className="mr-1.5 h-3.5 w-3.5" />
              添加友链
            </Button>
          </div>

          <label className="mb-4 block max-w-xs text-sm">
            <span className="mb-1 block text-muted-foreground">前缀文案</span>
            <input
              className="w-full rounded-lg border border-border bg-background px-3 py-2"
              value={draft.friendLinksLabel || "友情链接"}
              onChange={(e) => update({ friendLinksLabel: e.target.value })}
              placeholder="友情链接"
            />
          </label>

          <div className="space-y-3">
            {(draft.friendLinks ?? []).map((link, index) => (
              <div
                key={link.id}
                className="grid items-end gap-3 rounded-lg border border-border bg-background/50 p-3 md:grid-cols-[1fr_2fr_100px_auto]"
              >
                <label className="text-sm">
                  <span className="mb-1 block text-muted-foreground">显示名称</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background px-3 py-2"
                    value={link.label}
                    onChange={(e) => updateFriend(index, { label: e.target.value })}
                  />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-muted-foreground">跳转链接（须 https）</span>
                  <input
                    className="w-full rounded-lg border border-border bg-background px-3 py-2"
                    placeholder="https://..."
                    value={link.href}
                    onChange={(e) => updateFriend(index, { href: e.target.value })}
                  />
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-muted-foreground">排序</span>
                  <input
                    type="number"
                    min={0}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2"
                    value={link.sortOrder}
                    onChange={(e) =>
                      updateFriend(index, { sortOrder: Number(e.target.value) || 0 })
                    }
                  />
                </label>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-destructive"
                  onClick={() =>
                    update({
                      friendLinks: (draft.friendLinks ?? []).filter((_, i) => i !== index),
                    })
                  }
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
            {(draft.friendLinks ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无友链，点击「添加友链」配置。</p>
            ) : null}
          </div>
        </section>

        <div className="flex justify-end gap-3">
          <Button
            type="button"
            variant="outline"
            disabled={!data || saveMutation.isPending}
            onClick={() => data && setDraft(normalizeFooterDraft(structuredClone(data)))}
          >
            重置
          </Button>
          <Button
            type="button"
            disabled={saveMutation.isPending}
            onClick={() => {
              // 过滤掉未上传图片的空二维码，避免校验失败
              const payload: SiteFooterSettings = {
                ...draft,
                friendLinksLabel: draft.friendLinksLabel?.trim() || "友情链接",
                icpNumber: (draft.icpNumber || "").trim(),
                icpHref:
                  (draft.icpHref || "").trim() || "https://beian.miit.gov.cn/",
                contactUs: {
                  ...draft.contactUs,
                  qrCodes: draft.contactUs.qrCodes.filter((q) => q.imageUrl.trim()),
                },
                socialLinks: draft.socialLinks
                  .filter((s) => s.label.trim())
                  .map((s, i) => ({ ...s, sortOrder: s.sortOrder ?? i })),
                // 友链须名称 + http(s)；不完整项丢弃，避免后端校验失败
                friendLinks: (draft.friendLinks ?? [])
                  .filter((s) => {
                    const href = s.href.trim().toLowerCase();
                    return (
                      s.label.trim() &&
                      (href.startsWith("https://") || href.startsWith("http://"))
                    );
                  })
                  .map((s, i) => ({
                    ...s,
                    label: s.label.trim(),
                    href: s.href.trim(),
                    sortOrder: s.sortOrder ?? i,
                  })),
              };
              saveMutation.mutate(payload);
            }}
          >
            {saveMutation.isPending ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                保存中…
              </>
            ) : (
              "保存"
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
