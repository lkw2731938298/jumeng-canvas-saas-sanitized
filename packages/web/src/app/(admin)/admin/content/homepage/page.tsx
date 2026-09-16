"use client";

import { useRef, useState, type RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, ImagePlus, Loader2, Minimize2, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { validateCanvasImageFile } from "@/lib/canvas/imageSizePolicy";
import { resolveVisualStyleImageUrl } from "@/lib/canvas/visualStyleImage";
import {
  clearAdminAuthGridImages,
  deleteAdminAuthGridImage,
  deleteAdminLegalDocument,
  deleteAdminLoginModalImage,
  getAdminHomepageSettings,
  recompressAdminAuthGridImages,
  uploadAdminAuthGridImage,
  uploadAdminLegalDocument,
  uploadAdminLoginModalImage,
  type AdminLegalDocType,
} from "@/lib/api/admin";
import type { LegalDocumentMeta } from "@/types/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";

export default function AdminHomepagePage() {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const loginModalFileRef = useRef<HTMLInputElement>(null);
  const userAgreementFileRef = useRef<HTMLInputElement>(null);
  const privacyPolicyFileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [loginModalUploading, setLoginModalUploading] = useState(false);
  const [legalUploading, setLegalUploading] = useState<AdminLegalDocType | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["admin", "homepage-settings"],
    queryFn: getAdminHomepageSettings,
  });

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["admin", "homepage-settings"] });
    await queryClient.invalidateQueries({ queryKey: ["site", "homepage"] });
  };

  const deleteMutation = useMutation({
    mutationFn: deleteAdminAuthGridImage,
    onSuccess: async () => {
      await invalidate();
      toast.success("已删除");
    },
    onError: (err: Error) => toast.error(err.message || "删除失败"),
  });

  const clearMutation = useMutation({
    mutationFn: clearAdminAuthGridImages,
    onSuccess: async () => {
      await invalidate();
      toast.success("已清空全部登录背景图");
    },
    onError: (err: Error) => toast.error(err.message || "清空失败"),
  });

  const recompressMutation = useMutation({
    mutationFn: recompressAdminAuthGridImages,
    onSuccess: async (res) => {
      await invalidate();
      toast.success(
        `重压完成：更新 ${res.rewritten} 张，跳过 ${res.skipped} 张` +
          (res.failed > 0 ? `，失败 ${res.failed} 张` : ""),
      );
    },
    onError: (err: Error) => toast.error(err.message || "重压失败"),
  });

  const clearLoginModalMutation = useMutation({
    mutationFn: deleteAdminLoginModalImage,
    onSuccess: async () => {
      await invalidate();
      toast.success("已清除登录弹窗左侧图");
    },
    onError: (err: Error) => toast.error(err.message || "清除失败"),
  });

  const clearLegalMutation = useMutation({
    mutationFn: deleteAdminLegalDocument,
    onSuccess: async () => {
      await invalidate();
      toast.success("已清除文档");
    },
    onError: (err: Error) => toast.error(err.message || "清除失败"),
  });

  const handleUploadFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    let successCount = 0;
    try {
      for (const file of Array.from(files)) {
        const sizeError = validateCanvasImageFile(file);
        if (sizeError) {
          toast.error(`${file.name}: ${sizeError}`);
          continue;
        }
        await uploadAdminAuthGridImage(file);
        successCount += 1;
      }
      await invalidate();
      if (successCount > 0) {
        toast.success(`已上传 ${successCount} 张登录背景图`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const handleLoginModalUpload = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    const sizeError = validateCanvasImageFile(file);
    if (sizeError) {
      toast.error(sizeError);
      return;
    }
    setLoginModalUploading(true);
    try {
      await uploadAdminLoginModalImage(file);
      await invalidate();
      toast.success("登录弹窗左侧图已更新");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
    } finally {
      setLoginModalUploading(false);
    }
  };

  const handleLegalUpload = async (docType: AdminLegalDocType, files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    const ext = file.name.toLowerCase();
    if (!ext.endsWith(".md") && !ext.endsWith(".docx")) {
      toast.error("仅支持 .md 或 .docx 文件");
      return;
    }
    setLegalUploading(docType);
    try {
      await uploadAdminLegalDocument(docType, file);
      await invalidate();
      toast.success(docType === "user-agreement" ? "用户协议已更新" : "隐私政策已更新");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
    } finally {
      setLegalUploading(null);
    }
  };

  const renderLegalSection = (
    docType: AdminLegalDocType,
    label: string,
    meta: LegalDocumentMeta | undefined,
    fileRef: RefObject<HTMLInputElement | null>,
  ) => {
    const configured = Boolean(meta?.configured);
    const uploadingThis = legalUploading === docType;
    return (
      <div className="rounded-lg border border-border/80 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">{label}</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {configured
                ? `已配置 · ${meta?.sourceFormat?.toUpperCase() || "MD"}${
                    meta?.updatedAt ? ` · 更新于 ${formatDateTimeCN(meta.updatedAt)}` : ""
                  }`
                : "未配置，登录弹窗点击时将提示暂无内容"}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".md,.docx,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              className="hidden"
              onChange={(e) => {
                void handleLegalUpload(docType, e.target.files);
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              size="sm"
              disabled={uploadingThis || isLoading}
              onClick={() => fileRef.current?.click()}
            >
              {uploadingThis ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <FileText className="mr-2 h-4 w-4" />
              )}
              {configured ? "更换文档" : "上传文档"}
            </Button>
            {configured ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={clearLegalMutation.isPending || uploadingThis}
                onClick={() => clearLegalMutation.mutate(docType)}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                清除
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    );
  };

  const images = data?.authGridImages ?? [];
  const loginModalPreview = resolveVisualStyleImageUrl(data?.loginModalImageUrl || "");
  const legalDocs = data?.legalDocuments;

  return (
    <div className="p-8">
      <AdminHeader
        title="登录页背景"
        description="管理登录/注册页网格背景，以及顶栏「登录/注册」弹窗的左侧运营图。"
      />

      <div className="mb-6 max-w-4xl space-y-4 rounded-xl border border-border bg-card p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">登录弹窗左侧图</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              用户点击顶栏「登录 / 注册」时弹出的左右分栏窗，左半边展示此图；未上传时显示默认占位。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              ref={loginModalFileRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
              className="hidden"
              onChange={(e) => {
                void handleLoginModalUpload(e.target.files);
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              disabled={loginModalUploading || isLoading}
              onClick={() => loginModalFileRef.current?.click()}
            >
              {loginModalUploading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <ImagePlus className="mr-2 h-4 w-4" />
              )}
              {loginModalPreview ? "更换图片" : "上传图片"}
            </Button>
            {loginModalPreview ? (
              <Button
                type="button"
                variant="outline"
                disabled={clearLoginModalMutation.isPending || loginModalUploading}
                onClick={() => clearLoginModalMutation.mutate()}
              >
                <Trash2 className="mr-2 h-4 w-4" />
                清除
              </Button>
            ) : null}
          </div>
        </div>
        {loginModalPreview ? (
          <div className="overflow-hidden rounded-xl border border-border bg-muted/30">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={loginModalPreview}
              alt="登录弹窗左侧图预览"
              className="mx-auto max-h-[320px] w-full object-contain"
            />
          </div>
        ) : (
          <div className="flex min-h-[140px] items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted-foreground">
            尚未上传，弹窗左侧将使用默认米色占位
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          建议竖图或接近弹窗左栏比例的运营图，支持 JPG / PNG / WebP，单张不超过 10MB。
        </p>
      </div>

      <div className="mb-6 max-w-4xl space-y-4 rounded-xl border border-border bg-card p-6">
        <div>
          <h2 className="text-base font-semibold">登录弹窗法务文档</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            配置登录弹窗底部「用户协议」「隐私政策」正文，支持上传 Markdown（.md）或 Word（.docx）。
          </p>
        </div>
        <div className="space-y-3">
          {renderLegalSection(
            "user-agreement",
            "用户协议",
            legalDocs?.userAgreement,
            userAgreementFileRef,
          )}
          {renderLegalSection(
            "privacy-policy",
            "隐私政策",
            legalDocs?.privacyPolicy,
            privacyPolicyFileRef,
          )}
        </div>
        <p className="text-xs text-muted-foreground">
          单文件不超过 2MB，页数建议 ≤ 50 页。Word 文档将自动提取正文并以 Markdown 形式展示。
        </p>
      </div>

      <div className="max-w-4xl space-y-6 rounded-xl border border-border bg-card p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold">登录页网格背景</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              已上传 {images.length} 张（最多 24 张）。用于独立 `/login` 全页动态网格背景。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif,image/bmp"
              multiple
              className="hidden"
              onChange={(e) => {
                void handleUploadFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <Button
              type="button"
              disabled={uploading || isLoading}
              onClick={() => fileRef.current?.click()}
            >
              {uploading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <ImagePlus className="mr-2 h-4 w-4" />
              )}
              上传图片
            </Button>
            {images.length > 0 ? (
              <>
                <Button
                  type="button"
                  variant="outline"
                  disabled={
                    recompressMutation.isPending || clearMutation.isPending || uploading
                  }
                  onClick={() => recompressMutation.mutate()}
                >
                  {recompressMutation.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Minimize2 className="mr-2 h-4 w-4" />
                  )}
                  一键压缩已有图
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={clearMutation.isPending || uploading || recompressMutation.isPending}
                  onClick={() => clearMutation.mutate()}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  清空全部
                </Button>
              </>
            ) : null}
          </div>
        </div>

        {images.length > 0 ? (
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6">
            {images.map((item) => {
              const previewUrl = resolveVisualStyleImageUrl(item.imageUrl);
              return (
                <div
                  key={item.id}
                  className="group relative overflow-hidden rounded-lg border border-border bg-muted/30"
                >
                  <div className="aspect-square">
                    {previewUrl ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={previewUrl}
                        alt="登录背景图"
                        loading="lazy"
                        decoding="async"
                        className="h-full w-full object-cover"
                      />
                    ) : null}
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="destructive"
                    className="absolute right-1.5 top-1.5 h-7 px-2 text-[11px] opacity-0 transition-opacity group-hover:opacity-100"
                    disabled={deleteMutation.isPending}
                    onClick={() => deleteMutation.mutate(item.id)}
                  >
                    删除
                  </Button>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex min-h-[200px] items-center justify-center rounded-xl border border-dashed border-border text-sm text-muted-foreground">
            尚未上传图片，登录页将使用系统默认背景网格
          </div>
        )}

        <p className="text-xs text-muted-foreground">
          建议上传横图或方图，支持 JPG / PNG / WebP，单张不超过 10MB。上传时会自动压缩为长边 ≤480
          的 WebP；登录页对唯一图降采样后共享显示。历史大图可点「一键压缩已有图」原地重压，无需删了重传。
        </p>
      </div>
    </div>
  );
}
