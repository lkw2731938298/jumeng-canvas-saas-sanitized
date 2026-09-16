"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { HelpCircle, ImagePlus, Video, X } from "lucide-react";
import { toast } from "sonner";
import { listProjects } from "@/lib/api/projects";
import { fetchProjectAssetManifest, type Asset } from "@/lib/api/assets";
import {
  publishWorkflow,
  type WorkflowPublication,
} from "@/lib/api/workflowPublications";
import { galleryFilters as fallbackGalleryFilters } from "@/components/huabu/huabuData";
import { getSiteDiscover } from "@/lib/api/site";
import { ApiError } from "@/lib/api/client";

interface PublishWorkflowModalProps {
  open: boolean;
  onClose: () => void;
  onPublished?: (item: WorkflowPublication) => void;
}

/** 发布工作流弹窗：封面图 + 预览视频均选自关联项目资产；默认展示封面，悬浮播视频 */
export function PublishWorkflowModal({ open, onClose, onPublished }: PublishWorkflowModalProps) {
  const [projectId, setProjectId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("短剧漫剧");
  const [videoAssetId, setVideoAssetId] = useState("");
  const [coverAssetId, setCoverAssetId] = useState("");
  const [isPublic, setIsPublic] = useState(true);
  const [showProjectPicker, setShowProjectPicker] = useState(false);
  const [showVideoPicker, setShowVideoPicker] = useState(false);
  const [showCoverPicker, setShowCoverPicker] = useState(false);
  const [previewHover, setPreviewHover] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const previewVideoRef = useRef<HTMLVideoElement | null>(null);

  const { data: projects = [] } = useQuery({
    queryKey: ["projects", "publish-picker"],
    queryFn: listProjects,
    enabled: open,
  });

  // 分类权威源：发现页 galleryFilters（与管理端/广场 Tab 一致；共用 site.discover 缓存）
  const { data: discoverPage } = useQuery({
    queryKey: ["site", "discover"],
    queryFn: getSiteDiscover,
    enabled: open,
    staleTime: 60_000,
  });

  const categories = useMemo(() => {
    const filters = discoverPage?.galleryFilters?.length
      ? discoverPage.galleryFilters
      : fallbackGalleryFilters;
    return filters.filter((c) => c && c !== "全部");
  }, [discoverPage?.galleryFilters]);

  const { data: assets = [], isFetching: assetsLoading } = useQuery({
    queryKey: ["project-assets", projectId, "publish"],
    queryFn: () => fetchProjectAssetManifest(projectId),
    enabled: open && Boolean(projectId),
  });

  const videoAssets = useMemo(
    () => assets.filter((a) => a.category === "video"),
    [assets]
  );
  const imageAssets = useMemo(
    () => assets.filter((a) => a.category === "image"),
    [assets]
  );

  const selectedProject = projects.find((p) => p.id === projectId);
  const selectedVideo = videoAssets.find((a) => a.id === videoAssetId);
  const selectedCover = imageAssets.find((a) => a.id === coverAssetId);

  useEffect(() => {
    if (!open) {
      setProjectId("");
      setTitle("");
      setDescription("");
      setCategory("短剧漫剧");
      setVideoAssetId("");
      setCoverAssetId("");
      setIsPublic(true);
      setShowProjectPicker(false);
      setShowVideoPicker(false);
      setShowCoverPicker(false);
      setPreviewHover(false);
      setSubmitting(false);
    }
  }, [open]);

  // 分类列表就绪后：缺省选中第一项；当前值不在列表内时纠正
  useEffect(() => {
    if (!open || categories.length === 0) return;
    setCategory((prev) => (categories.includes(prev) ? prev : categories[0]));
  }, [open, categories]);

  useEffect(() => {
    setVideoAssetId("");
    setCoverAssetId("");
  }, [projectId]);

  // 悬浮时播视频，移出暂停并回到片头
  useEffect(() => {
    const el = previewVideoRef.current;
    if (!el) return;
    if (previewHover) {
      el.currentTime = 0;
      void el.play().catch(() => {});
    } else {
      el.pause();
      el.currentTime = 0;
    }
  }, [previewHover, videoAssetId]);

  if (!open) return null;

  const canSubmit =
    Boolean(projectId) &&
    Boolean(videoAssetId) &&
    Boolean(coverAssetId) &&
    title.trim().length > 0 &&
    title.trim().length <= 20 &&
    description.trim().length > 0 &&
    description.trim().length <= 100 &&
    Boolean(category);

  const ensureProjectThen = (openPicker: () => void) => {
    if (!projectId) {
      toast.message("请先选择关联项目");
      setShowProjectPicker(true);
      return;
    }
    openPicker();
  };

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    try {
      const item = await publishWorkflow({
        projectId,
        title: title.trim(),
        description: description.trim(),
        category,
        videoAssetId,
        coverAssetId,
        isPublic,
      });
      toast.success(
        isPublic
          ? "已提交审核，通过后出现在作品广场"
          : "已保存到我的发布（仅自己可见）"
      );
      onPublished?.(item);
      onClose();
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "发布失败";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const coverUrl = selectedCover?.fileUrl || selectedCover?.thumbnailUrl || "";
  const videoUrl = selectedVideo?.fileUrl || "";
  const hasPreview = Boolean(coverUrl || videoUrl);

  return (
    <div className="wf-pub-overlay" role="dialog" aria-modal="true" aria-labelledby="wf-pub-title">
      <div className="wf-pub-modal">
        <header className="wf-pub-header">
          <h2 id="wf-pub-title">发布到全部工作流</h2>
          <button type="button" className="wf-pub-close" onClick={onClose} aria-label="关闭">
            <X size={18} strokeWidth={2} />
          </button>
        </header>

        <div className="wf-pub-body">
          <p className="wf-pub-hint" style={{ margin: "0 0 12px", fontSize: 12, lineHeight: 1.5, opacity: 0.72 }}>
            发布会冻结当前工作流快照。他人复制的是本次发布内容；你之后再改项目不会自动更新广场副本，需重新发布才会刷新。
          </p>

          {/* 预览区：默认封面，悬浮播视频；点击换封面 */}
          <div
            className={`wf-pub-preview${hasPreview ? " has-media" : ""}${previewHover && videoUrl ? " is-playing" : ""}`}
            onMouseEnter={() => {
              setPreviewHover(true);
              // 悬浮时尝试播放已选视频
            }}
            onMouseLeave={() => setPreviewHover(false)}
          >
            {hasPreview ? (
              <>
                {videoUrl ? (
                  <video
                    key={videoUrl}
                    ref={previewVideoRef}
                    className="wf-pub-preview-video"
                    src={videoUrl}
                    muted
                    loop
                    playsInline
                    preload="metadata"
                  />
                ) : null}
                {coverUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    className="wf-pub-preview-cover"
                    src={coverUrl}
                    alt="封面预览"
                  />
                ) : null}
                <div className="wf-pub-preview-actions">
                  <button
                    type="button"
                    onClick={() => ensureProjectThen(() => setShowCoverPicker(true))}
                  >
                    更换封面
                  </button>
                  <button
                    type="button"
                    onClick={() => ensureProjectThen(() => setShowVideoPicker(true))}
                  >
                    更换视频
                  </button>
                </div>
              </>
            ) : (
              <div className="wf-pub-preview-empty">
                <ImagePlus size={28} strokeWidth={1.6} />
                <strong>
                  选择封面与视频 <span className="req">*</span>
                </strong>
                <span>从关联项目素材中选择；默认展示封面，鼠标悬浮播放视频</span>
                <div className="wf-pub-preview-actions static">
                  <button
                    type="button"
                    onClick={() => ensureProjectThen(() => setShowCoverPicker(true))}
                  >
                    上传封面
                  </button>
                  <button
                    type="button"
                    onClick={() => ensureProjectThen(() => setShowVideoPicker(true))}
                  >
                    上传视频
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="wf-pub-media-row">
            <button
              type="button"
              className={`wf-pub-media-chip${selectedCover ? " on" : ""}`}
              onClick={() => ensureProjectThen(() => setShowCoverPicker(true))}
            >
              <ImagePlus size={14} strokeWidth={1.8} />
              {selectedCover ? selectedCover.title || "已选封面" : "封面 *"}
            </button>
            <button
              type="button"
              className={`wf-pub-media-chip${selectedVideo ? " on" : ""}`}
              onClick={() => ensureProjectThen(() => setShowVideoPicker(true))}
            >
              <Video size={14} strokeWidth={1.8} />
              {selectedVideo ? selectedVideo.title || "已选视频" : "视频 *"}
            </button>
          </div>

          <div className="wf-pub-field row">
            <label>
              关联项目 <span className="req">*</span>
            </label>
            <button
              type="button"
              className="wf-pub-select-btn"
              onClick={() => setShowProjectPicker(true)}
            >
              {selectedProject ? selectedProject.title : "+ 选择项目"}
            </button>
          </div>

          <div className="wf-pub-field">
            <label>
              作品名称 <span className="req">*</span>
            </label>
            <div className="wf-pub-input-wrap">
              <input
                value={title}
                maxLength={20}
                placeholder="请输入作品名称"
                onChange={(e) => setTitle(e.target.value)}
              />
              <span className="wf-pub-counter">
                {title.length}/20
              </span>
            </div>
          </div>

          <div className="wf-pub-field">
            <label>
              作品描述 <span className="req">*</span>
            </label>
            <div className="wf-pub-input-wrap">
              <textarea
                value={description}
                maxLength={100}
                rows={3}
                placeholder="请描述您的作品内容"
                onChange={(e) => setDescription(e.target.value)}
              />
              <span className="wf-pub-counter">
                {description.length}/100
              </span>
            </div>
          </div>

          <div className="wf-pub-field">
            <label>分类</label>
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>

          <div className="wf-pub-field row">
            <label className="wf-pub-toggle-label">
              公开画布
              <span className="wf-pub-help" title="关闭后仅出现在「我的发布」；开启后在「全部工作流」对所有用户可见并可复制">
                <HelpCircle size={14} strokeWidth={2} />
              </span>
            </label>
            <button
              type="button"
              role="switch"
              aria-checked={isPublic}
              className={`wf-pub-switch${isPublic ? " on" : ""}`}
              onClick={() => setIsPublic((v) => !v)}
            >
              <span />
            </button>
          </div>
        </div>

        <footer className="wf-pub-footer">
          <button
            type="button"
            className="wf-pub-submit"
            disabled={!canSubmit || submitting}
            onClick={() => void handleSubmit()}
          >
            {submitting ? "发布中…" : "发布"}
          </button>
          <p className="wf-pub-legal">
            继续即表示你同意我们的 <span>创作许可协议</span>。
          </p>
        </footer>
      </div>

      {showProjectPicker ? (
        <div className="wf-pub-picker" role="dialog" aria-label="选择项目">
          <div className="wf-pub-picker-panel">
            <header>
              <strong>选择关联项目</strong>
              <button type="button" onClick={() => setShowProjectPicker(false)} aria-label="关闭">
                <X size={16} />
              </button>
            </header>
            <ul>
              {projects.length === 0 ? (
                <li className="empty">暂无项目，请先创建项目</li>
              ) : (
                projects.map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => {
                        setProjectId(p.id);
                        if (!title.trim()) setTitle(p.title.slice(0, 20));
                        setShowProjectPicker(false);
                      }}
                    >
                      {p.title}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </div>
      ) : null}

      {showVideoPicker ? (
        <div className="wf-pub-picker" role="dialog" aria-label="选择视频">
          <div className="wf-pub-picker-panel">
            <header>
              <strong>选择项目视频</strong>
              <button type="button" onClick={() => setShowVideoPicker(false)} aria-label="关闭">
                <X size={16} />
              </button>
            </header>
            <ul className="wf-pub-video-list">
              {assetsLoading ? (
                <li className="empty">加载中…</li>
              ) : videoAssets.length === 0 ? (
                <li className="empty">该项目暂无视频素材</li>
              ) : (
                videoAssets.map((v: Asset) => (
                  <li key={v.id}>
                    <button
                      type="button"
                      className="wf-pub-video-item"
                      onClick={() => {
                        setVideoAssetId(v.id);
                        setShowVideoPicker(false);
                      }}
                    >
                      <video src={v.fileUrl} muted playsInline preload="metadata" />
                      <span>{v.title || "未命名视频"}</span>
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </div>
      ) : null}

      {showCoverPicker ? (
        <div className="wf-pub-picker" role="dialog" aria-label="选择封面">
          <div className="wf-pub-picker-panel">
            <header>
              <strong>选择封面图片</strong>
              <button type="button" onClick={() => setShowCoverPicker(false)} aria-label="关闭">
                <X size={16} />
              </button>
            </header>
            <ul className="wf-pub-video-list">
              {assetsLoading ? (
                <li className="empty">加载中…</li>
              ) : imageAssets.length === 0 ? (
                <li className="empty">该项目暂无图片素材</li>
              ) : (
                imageAssets.map((img: Asset) => (
                  <li key={img.id}>
                    <button
                      type="button"
                      className="wf-pub-video-item"
                      onClick={() => {
                        setCoverAssetId(img.id);
                        setShowCoverPicker(false);
                      }}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={img.thumbnailUrl || img.fileUrl}
                        alt=""
                      />
                      <span>{img.title || "未命名图片"}</span>
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </div>
      ) : null}
    </div>
  );
}
