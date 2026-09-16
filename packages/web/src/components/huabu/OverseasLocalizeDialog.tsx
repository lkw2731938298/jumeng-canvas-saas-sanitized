"use client";

/**
 * 发现页「一键出海」入口弹窗：上传参考视频、选择目标市场与画幅，建项后进入画布自动拉片本地化。
 */

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Upload, Film, Globe2 } from "lucide-react";
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
  OVERSEAS_MARKETS,
  type OverseasMarketId,
} from "@/lib/canvas/overseasMarkets";
import {
  saveOverseasSession,
  OVERSEAS_LOCALIZE_SKILL,
} from "@/lib/canvas/overseasSession";
import type { ViralRemakeAspect, ViralRemakeClarity } from "@/lib/canvas/viralRemakeSession";
import { attachAgentSession } from "@/lib/api/agentSessions";
import { useAuthStore } from "@/stores/authStore";

const MAX_VIDEO_MB = 200;
/** 整片源视频建议上限（秒）；更长请先裁剪。单段参考视频仍≤12s */
const MAX_SOURCE_DURATION_SEC = 180;
const MAX_REF_CLIP_SEC = 12;
const ACCEPT_VIDEO = "video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.webm,.mov,.mkv";

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

export function OverseasLocalizeDialog({ open, onOpenChange }: Props) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const videoInputRef = useRef<HTMLInputElement>(null);

  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [videoDurationSec, setVideoDurationSec] = useState<number | null>(null);
  const [targetMarketId, setTargetMarketId] = useState<OverseasMarketId>("US");
  const [aspectRatio, setAspectRatio] = useState<ViralRemakeAspect>("9:16");
  const [clarity, setClarity] = useState<ViralRemakeClarity>("1080p");
  const [localeNotes, setLocaleNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reset = useCallback(() => {
    setVideoFile(null);
    setVideoDurationSec(null);
    setTargetMarketId("US");
    setAspectRatio("9:16");
    setClarity("1080p");
    setLocaleNotes("");
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
          `视频约 ${duration.toFixed(1)} 秒，将按镜头切段；每段参考视频不超过 ${MAX_REF_CLIP_SEC} 秒`
        );
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "无法读取视频时长");
    }
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
    const market = OVERSEAS_MARKETS.find((m) => m.id === targetMarketId);
    const projectTitle = `一键出海·${market?.label ?? targetMarketId}`;
    try {
      const project = await createProject(projectTitle);
      createdProjectId = project.id;
      const videoAsset = await uploadAsset({
        file: videoFile,
        projectId: project.id,
        category: "video",
        subcategory: "出海参考",
        title: videoFile.name.replace(/\.[^.]+$/, "") || "出海参考视频",
      });

      saveOverseasSession(project.id, {
        videoAssetId: videoAsset.id,
        videoFileUrl: videoAsset.fileUrl,
        videoTitle: videoAsset.title,
        aspectRatio,
        clarity,
        targetMarketId,
        localeNotes: localeNotes.trim(),
        autoStart: true,
      });

      let sessionQs = "";
      try {
        const agent = await attachAgentSession({
          projectId: project.id,
          skillSlug: OVERSEAS_LOCALIZE_SKILL,
          message: `启动一键出海（${targetMarketId}）`,
        });
        sessionQs = `&session=${agent.sessionId}`;
      } catch {
        /* Session 补绑失败不阻断向导 */
      }

      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      onOpenChange(false);
      reset();
      router.push(`/${project.id}?skill=${OVERSEAS_LOCALIZE_SKILL}${sessionQs}`);
      toast.success("已创建项目，正在进入画布本地化拉片…");
    } catch (err) {
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
          <DialogTitle className="flex items-center gap-2">
            <Globe2 className="h-4 w-4 text-violet-400" />
            一键出海
          </DialogTitle>
          <DialogDescription>
            上传参考视频，选择目标国家/地区。AI 将保留原片运镜与节奏，把风格、主体、场景与对白语言改写成当地版本，再一键生成同款视频。
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
                    {videoDurationSec != null ? ` · ${videoDurationSec.toFixed(1)} 秒` : ""} · 点击更换
                  </span>
                </>
              ) : (
                <>
                  <Upload className="h-7 w-7 text-muted-foreground" />
                  <span>点击上传 mp4 / mov / webm</span>
                  <span className="text-xs text-muted-foreground">
                    最长 {MAX_SOURCE_DURATION_SEC} 秒，最大 {MAX_VIDEO_MB}MB
                  </span>
                </>
              )}
            </button>
          </div>

          <label className="flex flex-col gap-1 text-xs">
            <span className="text-muted-foreground">目标市场（必选）</span>
            <select
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              value={targetMarketId}
              disabled={submitting}
              onChange={(e) => setTargetMarketId(e.target.value as OverseasMarketId)}
            >
              {OVERSEAS_MARKETS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} · {m.language}
                </option>
              ))}
            </select>
          </label>

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
            <span className="text-muted-foreground">补充说明（可选）</span>
            <textarea
              className="min-h-[72px] resize-y rounded-md border border-border bg-background px-2 py-1.5 text-sm"
              placeholder="例如：产品保持不变，女主改成当地模特气质；对白全英；避免中文招牌"
              value={localeNotes}
              disabled={submitting}
              onChange={(e) => setLocaleNotes(e.target.value)}
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
              "进入画布出海"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
