"use client";

/**
 * 发现页「爆款复刻」入口弹窗：上传参考视频、可选替换素材与画幅，建项后进入画布自动拉片。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Upload, Film, ImagePlus, X } from "lucide-react";
import { toast } from "sonner";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { createProject, deleteProject } from "@/lib/api/projects";
import { uploadAsset } from "@/lib/api/assets";
import {
  saveViralRemakeSession,
  type ViralRemakeAspect,
  type ViralRemakeClarity,
  VIRAL_REMAKE_SKILL,
} from "@/lib/canvas/viralRemakeSession";
import { attachAgentSession } from "@/lib/api/agentSessions";
import { useAuthStore } from "@/stores/authStore";

const MAX_VIDEO_MB = 200;
/** 整片源视频建议上限（秒）；更长请先裁剪。单段参考视频仍≤12s（SD2.0） */
const MAX_SOURCE_DURATION_SEC = 180;
/** Seedance 2.0 等参考视频单段上限（秒），切镜后按此分段 */
const MAX_REF_CLIP_SEC = 12;
const ACCEPT_VIDEO = "video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.webm,.mov,.mkv";
const ACCEPT_IMAGE = "image/jpeg,image/png,image/webp,image/gif";

/** 读取本地视频文件时长（秒） */
function probeVideoDurationSec(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    video.preload = "metadata";
    const cleanup = () => {
      video.removeAttribute("src");
      video.load();
      URL.revokeObjectURL(url);
    };
    video.onloadedmetadata = () => {
      const d = video.duration;
      cleanup();
      if (!Number.isFinite(d) || d <= 0) {
        reject(new Error("无法读取视频时长"));
        return;
      }
      resolve(d);
    };
    video.onerror = () => {
      cleanup();
      reject(new Error("视频无法播放，请更换格式后重试"));
    };
    video.src = url;
  });
}

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function ViralRemakeDialog({ open, onOpenChange }: Props) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const videoInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  const [videoFile, setVideoFile] = useState<File | null>(null);
  /** 已选视频时长（秒），用于展示 */
  const [videoDurationSec, setVideoDurationSec] = useState<number | null>(null);
  const [replaceFiles, setReplaceFiles] = useState<File[]>([]);
  const [aspectRatio, setAspectRatio] = useState<ViralRemakeAspect>("9:16");
  const [clarity, setClarity] = useState<ViralRemakeClarity>("1080p");
  const [replaceNotes, setReplaceNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const replacePreviews = useMemo(
    () => replaceFiles.map((file) => ({ file, url: URL.createObjectURL(file) })),
    [replaceFiles]
  );

  useEffect(() => {
    return () => {
      replacePreviews.forEach((p) => URL.revokeObjectURL(p.url));
    };
  }, [replacePreviews]);

  const reset = useCallback(() => {
    setVideoFile(null);
    setVideoDurationSec(null);
    setReplaceFiles([]);
    setAspectRatio("9:16");
    setClarity("1080p");
    setReplaceNotes("");
    setSubmitting(false);
  }, []);

  const handleClose = (next: boolean) => {
    if (submitting) return;
    if (!next) reset();
    onOpenChange(next);
  };

  const onPickVideo = async (file: File | null) => {
    if (!file) return;
    if (file.size > MAX_VIDEO_MB * 1024 * 1024) {
      toast.error(`视频不能超过 ${MAX_VIDEO_MB}MB`);
      return;
    }
    try {
      const duration = await probeVideoDurationSec(file);
      if (duration > MAX_SOURCE_DURATION_SEC + 0.05) {
        toast.error(
          `参考视频最长 ${MAX_SOURCE_DURATION_SEC} 秒（当前约 ${duration.toFixed(1)} 秒），请裁剪后重试`
        );
        return;
      }
      setVideoFile(file);
      setVideoDurationSec(duration);
      if (duration > MAX_REF_CLIP_SEC + 0.05) {
        toast.message(
          `视频约 ${duration.toFixed(1)} 秒，将按镜头切段；每段参考视频不超过 ${MAX_REF_CLIP_SEC} 秒（适配 SD2.0）`
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "无法读取视频时长");
      return;
    }
  };

  const onPickReplaceImages = (files: FileList | null) => {
    if (!files?.length) return;
    const next = [...replaceFiles, ...Array.from(files)].slice(0, 4);
    setReplaceFiles(next);
  };

  const handleSubmit = async () => {
    if (!user) {
      router.push("/login?next=%2F");
      return;
    }
    if (!videoFile) {
      toast.error("请先上传参考视频");
      return;
    }

    setSubmitting(true);
    let createdProjectId: string | null = null;
    try {
      const project = await createProject("爆款复刻");
      createdProjectId = project.id;
      const videoAsset = await uploadAsset({
        file: videoFile,
        projectId: project.id,
        category: "video",
        subcategory: "爆款参考",
        title: videoFile.name.replace(/\.[^.]+$/, "") || "参考爆款",
      });

      const replaceImageAssetIds: string[] = [];
      const replaceImageUrls: string[] = [];
      for (const file of replaceFiles) {
        const asset = await uploadAsset({
          file,
          projectId: project.id,
          category: "image",
          subcategory: "替换素材",
          title: file.name.replace(/\.[^.]+$/, "") || "替换素材",
        });
        replaceImageAssetIds.push(asset.id);
        replaceImageUrls.push(asset.fileUrl);
      }

      saveViralRemakeSession(project.id, {
        videoAssetId: videoAsset.id,
        videoFileUrl: videoAsset.fileUrl,
        videoTitle: videoAsset.title,
        aspectRatio,
        clarity,
        replaceNotes: replaceNotes.trim(),
        replaceImageAssetIds,
        replaceImageUrls,
        autoStart: true,
      });

      let sessionQs = "";
      try {
        const agent = await attachAgentSession({
          projectId: project.id,
          skillSlug: VIRAL_REMAKE_SKILL,
          message: "启动爆款拉片复刻",
        });
        sessionQs = `&session=${agent.sessionId}`;
      } catch {
        /* Session 补绑失败不阻断向导 */
      }

      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      onOpenChange(false);
      reset();
      router.push(`/${project.id}?skill=${VIRAL_REMAKE_SKILL}${sessionQs}`);
      toast.success("已创建项目，正在进入画布拉片…");
    } catch (err) {
      // 上传中途失败：清理空项目，避免列表残留「爆款复刻」空壳
      if (createdProjectId) {
        try {
          await deleteProject(createdProjectId);
          void queryClient.invalidateQueries({ queryKey: ["projects"] });
        } catch {
          /* ignore cleanup errors */
        }
      }
      toast.error(err instanceof Error ? err.message : "创建失败，请稍后重试");
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-lg" showCloseButton={!submitting}>
        <DialogHeader>
          <DialogTitle>爆款拉片复刻</DialogTitle>
          <DialogDescription>
            上传参考视频（最长 3 分钟），AI 按镜头切段拉片；成片时每镜用对应片段作参考（单段≤12
            秒，适配 Seedance 2.0）。替换角色/产品后可一键生成同款。
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">参考视频（必填）</p>
            <input
              ref={videoInputRef}
              type="file"
              accept={ACCEPT_VIDEO}
              className="hidden"
              onChange={(e) => {
                void onPickVideo(e.target.files?.[0] ?? null);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              disabled={submitting}
              onClick={() => videoInputRef.current?.click()}
              className="flex w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/80 bg-muted/30 px-4 py-8 text-sm transition hover:border-primary/50 hover:bg-muted/50"
            >
              {videoFile ? (
                <>
                  <Film className="h-7 w-7 text-primary" />
                  <span className="max-w-full truncate font-medium">{videoFile.name}</span>
                  <span className="text-xs text-muted-foreground">
                    {(videoFile.size / (1024 * 1024)).toFixed(1)} MB
                    {videoDurationSec != null
                      ? ` · ${videoDurationSec.toFixed(1)} 秒`
                      : ""}{" "}
                    · 点击更换
                  </span>
                </>
              ) : (
                <>
                  <Upload className="h-7 w-7 text-muted-foreground" />
                  <span>点击上传 mp4 / mov / webm</span>
                  <span className="text-xs text-muted-foreground">
                    最长 {MAX_SOURCE_DURATION_SEC} 秒，最大 {MAX_VIDEO_MB}MB；超{" "}
                    {MAX_REF_CLIP_SEC} 秒将按镜头分段作参考
                  </span>
                </>
              )}
            </button>
          </div>

          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">替换素材（可选，最多 4 张）</p>
            <input
              ref={imageInputRef}
              type="file"
              accept={ACCEPT_IMAGE}
              multiple
              className="hidden"
              onChange={(e) => {
                onPickReplaceImages(e.target.files);
                e.target.value = "";
              }}
            />
            <div className="flex flex-wrap gap-2">
              {replacePreviews.map(({ file, url }, i) => (
                <div
                  key={`${file.name}-${i}`}
                  className="relative h-16 w-16 overflow-hidden rounded-md border border-border/60"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={url} alt="" className="h-full w-full object-cover" />
                  <button
                    type="button"
                    disabled={submitting}
                    className="absolute right-0.5 top-0.5 rounded-full bg-black/70 p-0.5 text-white"
                    onClick={() => setReplaceFiles((prev) => prev.filter((_, j) => j !== i))}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </div>
              ))}
              {replaceFiles.length < 4 ? (
                <button
                  type="button"
                  disabled={submitting}
                  onClick={() => imageInputRef.current?.click()}
                  className="flex h-16 w-16 flex-col items-center justify-center rounded-md border border-dashed border-border/70 text-muted-foreground hover:border-primary/50"
                >
                  <ImagePlus className="h-5 w-5" />
                </button>
              ) : null}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">目标画幅</span>
              <select
                className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                value={aspectRatio}
                disabled={submitting}
                onChange={(e) => setAspectRatio(e.target.value as ViralRemakeAspect)}
              >
                <option value="9:16">9:16 竖屏</option>
                <option value="16:9">16:9 横屏</option>
                <option value="1:1">1:1 方屏</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">清晰度</span>
              <select
                className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                value={clarity}
                disabled={submitting}
                onChange={(e) => setClarity(e.target.value as ViralRemakeClarity)}
              >
                <option value="1080p">1080p</option>
                <option value="720p">720p</option>
              </select>
            </label>
          </div>

          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">替换说明（可选）</span>
            <textarea
              className="min-h-[72px] resize-y rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              placeholder="例如：将女主换成我上传的角色脸与服装，产品换成图中耳机，其它运镜与节奏保持不变"
              value={replaceNotes}
              disabled={submitting}
              onChange={(e) => setReplaceNotes(e.target.value)}
            />
          </label>
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" disabled={submitting} onClick={() => handleClose(false)}>
            取消
          </Button>
          <Button type="button" disabled={submitting || !videoFile} onClick={() => void handleSubmit()}>
            {submitting ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                上传中…
              </>
            ) : (
              "进入画布拉片"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
