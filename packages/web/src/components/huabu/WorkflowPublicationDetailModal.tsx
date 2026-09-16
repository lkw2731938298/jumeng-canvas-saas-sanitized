"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Copy, Eye, Heart, Maximize2, X } from "lucide-react";
import { toast } from "sonner";
import { useRouter } from "next/navigation";
import {
  copyWorkflowPublication,
  getPublicationPreview,
  getWorkflowPublication,
  togglePublicationLike,
  type WorkflowPublication,
} from "@/lib/api/workflowPublications";
import { ReadonlyWorkflowPreview } from "@/components/huabu/ReadonlyWorkflowPreview";
import { withBasePath } from "@/lib/basePath";
import { ApiError } from "@/lib/api/client";
import { useAuthStore } from "@/stores/authStore";

interface WorkflowPublicationDetailModalProps {
  publicationId: string | null;
  onClose: () => void;
  onLikeChange?: (id: string, liked: boolean, likeCount: number) => void;
}

/** 作品详情大弹窗：视频播放、立即观看、查看工作流、点赞、复制 */
export function WorkflowPublicationDetailModal({
  publicationId,
  onClose,
  onLikeChange,
}: WorkflowPublicationDetailModalProps) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [copying, setCopying] = useState(false);
  const [liking, setLiking] = useState(false);
  const [local, setLocal] = useState<WorkflowPublication | null>(null);
  /** 预览视频横竖屏 + 真实宽高比（避免写死 9:16） */
  const [videoOrient, setVideoOrient] = useState<"portrait" | "landscape" | "square" | null>(null);
  const [videoAspect, setVideoAspect] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["workflow-publication", publicationId],
    queryFn: () => getWorkflowPublication(publicationId!),
    enabled: Boolean(publicationId),
  });

  const previewQuery = useQuery({
    queryKey: ["workflow-publication-preview", publicationId],
    queryFn: () => getPublicationPreview(publicationId!),
    enabled: Boolean(publicationId) && showPreview,
  });

  useEffect(() => {
    if (data) setLocal(data);
  }, [data]);

  useEffect(() => {
    if (!publicationId) {
      setShowPreview(false);
      setLocal(null);
      setVideoOrient(null);
      setVideoAspect(null);
    }
  }, [publicationId]);

  useEffect(() => {
    // 换片时先清方向，等 loadedmetadata 再定
    setVideoOrient(null);
    setVideoAspect(null);
  }, [local?.videoUrl]);

  /** 根据 videoWidth/Height 判定横竖/方屏，并写入真实比例 */
  const applyVideoOrientation = (el: HTMLVideoElement) => {
    const w = el.videoWidth;
    const h = el.videoHeight;
    if (!w || !h) return;
    setVideoAspect(`${w} / ${h}`);
    const ratio = w / h;
    if (ratio > 1.05) setVideoOrient("landscape");
    else if (ratio < 0.95) setVideoOrient("portrait");
    else setVideoOrient("square");
  };

  if (!publicationId) return null;

  const item = local;

  const handleFullscreen = () => {
    const el = videoRef.current;
    if (!el) return;
    el.muted = false;
    void el.play().catch(() => {});
    const anyEl = el as HTMLVideoElement & {
      webkitRequestFullscreen?: () => void;
      msRequestFullscreen?: () => void;
    };
    if (el.requestFullscreen) void el.requestFullscreen();
    else if (anyEl.webkitRequestFullscreen) anyEl.webkitRequestFullscreen();
    else if (anyEl.msRequestFullscreen) anyEl.msRequestFullscreen();
  };

  const handleLike = async () => {
    if (!item || liking) return;
    // 未登录先引导登录，避免 401 静默跳转看起来像「点赞坏了」
    if (!user) {
      toast.error("请先登录后再点赞");
      router.push(`/login?next=${encodeURIComponent("/")}`);
      return;
    }
    setLiking(true);
    try {
      const res = await togglePublicationLike(item.id);
      setLocal({ ...item, liked: res.liked, likeCount: res.likeCount });
      onLikeChange?.(item.id, res.liked, res.likeCount);
      toast.success(res.liked ? "已点赞" : "已取消点赞");
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "操作失败";
      toast.error(msg);
    } finally {
      setLiking(false);
    }
  };

  const handleCopy = async () => {
    if (!item || copying) return;
    setCopying(true);
    try {
      const res = await copyWorkflowPublication(item.id);
      toast.success("已复制到我的项目");
      onClose();
      router.push(withBasePath(`/${res.projectId}`));
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : "复制失败";
      toast.error(msg);
    } finally {
      setCopying(false);
    }
  };

  return (
    <div className="wf-detail-overlay" role="dialog" aria-modal="true">
      <div className={`wf-detail-modal${showPreview ? " with-preview" : ""}`}>
        <header className="wf-detail-header">
          <div className="wf-detail-title-block">
            <h2>{item?.title || (isLoading ? "加载中…" : "工作流详情")}</h2>
            {item ? (
              <p>
                {item.authorName}
                {item.category ? ` · ${item.category}` : ""}
              </p>
            ) : null}
          </div>
          <div className="wf-detail-header-actions">
            {item ? (
              <button
                type="button"
                className="wf-detail-copy-btn"
                disabled={copying}
                onClick={() => void handleCopy()}
              >
                <Copy size={15} strokeWidth={2} />
                {copying ? "复制中…" : "复制工作流"}
              </button>
            ) : null}
            <button type="button" className="wf-pub-close" onClick={onClose} aria-label="关闭">
              <X size={18} strokeWidth={2} />
            </button>
          </div>
        </header>

        {error ? (
          <div className="wf-detail-error">加载失败，请稍后重试</div>
        ) : (
          <>
            <div
              className={`wf-detail-video-wrap${
                videoOrient ? ` is-${videoOrient}` : ""
              }`}
              style={videoAspect ? { aspectRatio: videoAspect } : undefined}
            >
                  {item?.videoUrl ? (
                <video
                  ref={videoRef}
                  src={item.videoUrl}
                  controls
                  playsInline
                  preload="metadata"
                  poster={item.coverUrl || undefined}
                  onLoadedMetadata={(e) => applyVideoOrientation(e.currentTarget)}
                />
              ) : (
                <div className="wf-detail-video-empty">{isLoading ? "加载中…" : "暂无视频"}</div>
              )}
            </div>

            {item ? (
              <div className="wf-detail-actions">
                <button type="button" onClick={handleFullscreen}>
                  <Maximize2 size={16} strokeWidth={2} />
                  立即观看
                </button>
                <button type="button" onClick={() => setShowPreview(true)}>
                  <Eye size={16} strokeWidth={2} />
                  查看工作流
                </button>
                <button
                  type="button"
                  className={item.liked ? "liked" : ""}
                  disabled={liking}
                  aria-pressed={item.liked}
                  aria-label={item.liked ? "取消点赞" : "点赞"}
                  onClick={() => void handleLike()}
                >
                  <Heart size={16} strokeWidth={2} fill={item.liked ? "currentColor" : "none"} />
                  {item.liked ? "已赞" : "点赞"}
                  {item.likeCount > 0 ? ` ${item.likeCount}` : ""}
                </button>
              </div>
            ) : null}

            {item?.description ? <p className="wf-detail-desc">{item.description}</p> : null}
          </>
        )}
      </div>

      {showPreview ? (
        <div className="wf-preview-overlay" role="dialog" aria-label="查看工作流">
          <div className="wf-preview-panel">
            <header>
              <strong>查看工作流 · {item?.title || ""}</strong>
              <button type="button" onClick={() => setShowPreview(false)} aria-label="关闭预览">
                <X size={16} />
              </button>
            </header>
            <div className="wf-preview-body">
              {previewQuery.isLoading ? (
                <div className="wf-preview-empty">加载预览…</div>
              ) : previewQuery.data ? (
                <ReadonlyWorkflowPreview preview={previewQuery.data} />
              ) : (
                <div className="wf-preview-empty">无法加载工作流预览</div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
