"use client";

/**
 * 「我的」Skill 编辑：封面 / 名称 / 分类，以及技能包 references 与画布规则。
 * 已发布修改后需重新审核。与 CreateSkillFromPromptDialog 共用样式类名。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ImagePlus, Loader2, Plus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import {
  listSkills,
  updateSkillMeta,
  uploadSkillCover,
  type SkillExecutionMode,
  type SkillItem,
  type SkillPackageFiles,
} from "@/lib/api/skills";
import { validateImageAspectRatio } from "@/lib/validateImageAspectRatio";
import { ApiError } from "@/lib/api/client";
import { ensureHttpsOssUrl } from "@/lib/signedUrl";
import { cn } from "@/lib/utils";

type Props = {
  open: boolean;
  skill: SkillItem | null;
  onClose: () => void;
  onSaved?: (skill: SkillItem) => void;
};

type PackageFileRow = { path: string; content: string };

function packageFilesToRows(files: SkillPackageFiles | undefined | null): PackageFileRow[] {
  if (!files) return [];
  return Object.entries(files).map(([path, content]) => ({
    path,
    content: String(content),
  }));
}

function packageFilesToDict(rows: PackageFileRow[]): SkillPackageFiles | undefined {
  const out: SkillPackageFiles = {};
  for (const row of rows) {
    const path = row.path.trim();
    const content = row.content.trim();
    if (path && content) out[path] = content;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

export function EditSkillMetaDialog({ open, skill, onClose, onSaved }: Props) {
  const queryClient = useQueryClient();
  const coverInputRef = useRef<HTMLInputElement>(null);
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [coverUrl, setCoverUrl] = useState("");
  const [executionMode, setExecutionMode] = useState<SkillExecutionMode>("canvas_manual");
  const [canvasRulesMarkdown, setCanvasRulesMarkdown] = useState("");
  const [packageFileRows, setPackageFileRows] = useState<PackageFileRow[]>([]);
  const [pkgExpanded, setPkgExpanded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [uploadingCover, setUploadingCover] = useState(false);

  const catsQuery = useQuery({
    queryKey: ["skills", "catalog", "edit-meta-cats"],
    queryFn: () => listSkills(),
    enabled: open,
    staleTime: 60_000,
  });

  const categoryOptions = useMemo(() => {
    const raw = catsQuery.data?.categories ?? [];
    return raw.filter((c) => c && c !== "推荐" && c !== "我的 Skill");
  }, [catsQuery.data?.categories]);

  useEffect(() => {
    if (!open || !skill) return;
    setTitle(skill.title || "");
    setCategory(skill.category || categoryOptions[0] || "通用技能");
    setCoverUrl(ensureHttpsOssUrl(skill.coverUrl || "") || skill.coverUrl || "");
    setExecutionMode((skill.executionMode as SkillExecutionMode) || "canvas_manual");
    setCanvasRulesMarkdown(skill.canvasRulesMarkdown || "");
    setPackageFileRows(packageFilesToRows(skill.packageFiles));
    setPkgExpanded(Boolean(skill.packageFiles && Object.keys(skill.packageFiles).length > 0));
    setSubmitting(false);
    setUploadingCover(false);
  }, [open, skill, categoryOptions]);

  if (!open || !skill) return null;

  const handleCover = async (file: File | null) => {
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
      const up = await uploadSkillCover(file);
      setCoverUrl(ensureHttpsOssUrl(up.imageUrl) || up.imageUrl);
      toast.success("封面已上传");
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "封面上传失败"
      );
    } finally {
      setUploadingCover(false);
      if (coverInputRef.current) coverInputRef.current.value = "";
    }
  };

  const handleSubmit = async () => {
    const t = title.trim();
    if (!t) {
      toast.error("请填写名称");
      return;
    }
    if (!category.trim()) {
      toast.error("请选择分类");
      return;
    }
    setSubmitting(true);
    try {
      const clearCover = !coverUrl.trim() && Boolean(skill.coverUrl);
      const pkg = packageFilesToDict(packageFileRows);
      const updated = await updateSkillMeta(skill.slug, {
        title: t,
        category: category.trim(),
        ...(clearCover
          ? { clearCover: true }
          : coverUrl.trim()
            ? { coverUrl: coverUrl.trim() }
            : {}),
        executionMode: executionMode || "canvas_manual",
        canvasRulesMarkdown: canvasRulesMarkdown.trim(),
        ...(pkg ? { packageFiles: pkg } : { clearPackageFiles: true }),
      });
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      const needReview =
        skill.reviewStatus === "approved" || skill.reviewStatus === "pending";
      toast.success(
        needReview
          ? "已保存；已发布内容修改后需重新审核"
          : "已保存技能包"
      );
      onSaved?.(updated);
      onClose();
    } catch (err) {
      toast.error(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "保存失败"
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="create-skill-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="edit-skill-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="create-skill-dialog" style={{ maxWidth: 720 }}>
        <header className="csd-header">
          <div className="csd-header-copy">
            <h2 id="edit-skill-title">编辑 Skill</h2>
            <p className="csd-sub">可改名称、封面、执行模式与技能包参考文件</p>
          </div>
          <div className="csd-header-actions">
            <button
              type="button"
              className="csd-btn-save"
              disabled={submitting}
              onClick={() => void handleSubmit()}
            >
              {submitting ? <Loader2 size={15} className="animate-spin" /> : null}
              保存
            </button>
            <button
              type="button"
              className="csd-btn-icon"
              disabled={submitting}
              onClick={onClose}
              aria-label="关闭"
            >
              <X size={17} />
            </button>
          </div>
        </header>
        <div className="csd-body">
          <p className="csd-hint" style={{ marginBottom: 12 }}>
            若该 Skill 已公开或审核中，保存后将撤回公开并重新进入审核。
          </p>
          <label className="csd-field">
            <span className="csd-label">
              名称 <i aria-hidden>*</i>
            </span>
            <input
              className="csd-input"
              value={title}
              maxLength={64}
              disabled={submitting}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label className="csd-field" style={{ marginTop: 12 }}>
            <span className="csd-label">
              分类 <i aria-hidden>*</i>
            </span>
            <select
              className="csd-input"
              value={category}
              disabled={submitting}
              onChange={(e) => setCategory(e.target.value)}
            >
              {!categoryOptions.includes(category) && category ? (
                <option value={category}>{category}</option>
              ) : null}
              {categoryOptions.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
          <div className="csd-field" style={{ marginTop: 12 }}>
            <span className="csd-label">封面</span>
            <div className="csd-cover">
              <div className="csd-cover-thumb">
                {coverUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={coverUrl} alt="" />
                ) : (
                  <ImagePlus size={18} strokeWidth={1.6} />
                )}
              </div>
              <div className="csd-cover-copy">
                <p>封面须为 9:16 竖版（宽:高），10MB 以内</p>
                <div className="csd-cover-actions">
                  <button
                    type="button"
                    className="csd-btn-ghost"
                    disabled={uploadingCover || submitting}
                    onClick={() => coverInputRef.current?.click()}
                  >
                    {uploadingCover ? "上传中…" : coverUrl ? "更换封面" : "选择图片"}
                  </button>
                  {coverUrl ? (
                    <button
                      type="button"
                      className="csd-btn-ghost"
                      disabled={submitting}
                      onClick={() => setCoverUrl("")}
                    >
                      清除
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
            <input
              ref={coverInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              className="sr-only"
              onChange={(e) => void handleCover(e.target.files?.[0] ?? null)}
            />
          </div>

          <label className="csd-field" style={{ marginTop: 16 }}>
            <span className="csd-label">执行模式</span>
            <select
              className="csd-input"
              value={executionMode}
              disabled={submitting}
              onChange={(e) => setExecutionMode(e.target.value as SkillExecutionMode)}
            >
              <option value="canvas_manual">精细画布操控（技能包，推荐）</option>
              <option value="team">智能编排（按节点配方投影）</option>
            </select>
          </label>
          <label className="csd-field" style={{ marginTop: 12 }}>
            <span className="csd-label">画布规则覆盖</span>
            <textarea
              className="csd-textarea"
              value={canvasRulesMarkdown}
              maxLength={8000}
              rows={4}
              placeholder="该技能对 AI 操控画布的专属规则（选填）"
              disabled={submitting}
              onChange={(e) => setCanvasRulesMarkdown(e.target.value)}
            />
          </label>

          <section className={cn("csd-fold", pkgExpanded && "is-open")} style={{ marginTop: 16 }}>
            <button
              type="button"
              className="csd-fold-toggle"
              aria-expanded={pkgExpanded}
              disabled={submitting}
              onClick={() => setPkgExpanded((v) => !v)}
            >
              <span className="csd-fold-toggle-main">
                <span>技能包参考文件</span>
              </span>
              <span className="csd-fold-meta">
                <span className="csd-fold-hint">
                  {pkgExpanded
                    ? "收起"
                    : `${packageFileRows.length} 个文件 · flow / 节点表 / 生成规则`}
                </span>
                <ChevronDown
                  size={16}
                  className={cn("csd-ai-chevron", pkgExpanded && "is-open")}
                  aria-hidden
                />
              </span>
            </button>
            {pkgExpanded ? (
              <div className="csd-fold-panel">
                {packageFileRows.length === 0 ? (
                  <p className="csd-hint">暂无参考文件。可新增 references/flow.md 等。</p>
                ) : null}
                {packageFileRows.map((row, idx) => (
                  <div key={`${row.path}-${idx}`} className="csd-pkg-file-row" style={{ marginBottom: 10 }}>
                    <div className="csd-grid-2">
                      <input
                        className="csd-input"
                        value={row.path}
                        maxLength={120}
                        placeholder="references/flow.md"
                        disabled={submitting}
                        onChange={(e) => {
                          const path = e.target.value;
                          setPackageFileRows((rows) =>
                            rows.map((r, i) => (i === idx ? { ...r, path } : r))
                          );
                        }}
                      />
                      <button
                        type="button"
                        className="csd-btn-icon csd-flow-remove"
                        disabled={submitting}
                        aria-label="删除文件"
                        onClick={() =>
                          setPackageFileRows((rows) => rows.filter((_, i) => i !== idx))
                        }
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    <textarea
                      className="csd-textarea"
                      value={row.content}
                      rows={8}
                      disabled={submitting}
                      onChange={(e) => {
                        const content = e.target.value;
                        setPackageFileRows((rows) =>
                          rows.map((r, i) => (i === idx ? { ...r, content } : r))
                        );
                      }}
                    />
                  </div>
                ))}
                <button
                  type="button"
                  className="csd-btn-ghost"
                  disabled={submitting}
                  onClick={() =>
                    setPackageFileRows((rows) => [
                      ...rows,
                      { path: "references/custom.md", content: "" },
                    ])
                  }
                >
                  <Plus size={14} /> 新增参考文件
                </button>
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
