"use client";

/**
 * 发现页 / 技能页共用创作框：智能体技能 / 视觉风格 / 素材库 + 发送 → Agent Session。
 * 控制器文本模型由后台配置（默认豆包 Seed Evolving），用户侧不展示、不可切换。
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUp,
  ChevronDown,
  Globe2,
  LayoutGrid,
  Loader2,
  Palette,
  Paperclip,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import { listMySkills, listSkills, type SkillItem } from "@/lib/api/skills";
import {
  bindAgentSessionReferenceAssets,
  createAgentSession,
} from "@/lib/api/agentSessions";
import { uploadAsset } from "@/lib/api/assets";
import { formatCreditLabel, getAgentSkillPricing } from "@/lib/api/credits";
import type { MaterialLibraryItem } from "@/lib/api/materialLibrary";
import { useAuthStore } from "@/stores/authStore";
import { useVisualStyles } from "@/lib/canvas/useVisualStyles";
import { VisualStylePickerDialog } from "@/components/canvas/VisualStylePickerDialog";
import { CreationMaterialLibraryDialog } from "@/components/huabu/CreationMaterialLibraryDialog";
import { FixedAboveMenu } from "@/components/huabu/FixedAboveMenu";
import { DEFAULT_VISUAL_STYLE_ID } from "@/lib/canvas/renderToolPrompt";
import { OVERSEAS_LOCALIZE_SKILL } from "@/lib/canvas/overseasSession";
import {
  VIRAL_REMAKE_SKILL,
  type ViralRemakeAspect,
  type ViralRemakeClarity,
} from "@/lib/canvas/viralRemakeSession";
import {
  OVERSEAS_MARKETS,
  overseasMarketLabel,
  SKILL_COMPOSER_ASPECT_OPTIONS,
  SKILL_COMPOSER_CLARITY_OPTIONS,
} from "@/lib/canvas/skillComposerOptions";
import type { OverseasMarketId } from "@/lib/canvas/overseasMarkets";
import { ApiError } from "@/lib/api/client";
import { ensureHttpsOssUrl } from "@/lib/signedUrl";
import { inferAssetCategory } from "@/lib/canvas/fileDrop";
import { cn } from "@/lib/utils";

/** AI 操控默认文本模型：豆包 Seed Evolving（后台可改白名单，前端不暴露选型） */
const DEFAULT_CONTROLLER_MODEL_ID = "doubao_seed_evolving";

type PendingAttach = {
  localId: string;
  name: string;
  file: File;
  previewUrl: string;
};

/** 从平台素材库选用的参考（提交时再拉取上传到项目） */
type PendingLibraryRef = {
  localId: string;
  item: MaterialLibraryItem;
};

type Props = {
  /** 登录回跳路径（不含 basePath） */
  loginNext: string;
  placeholder?: string;
  className?: string;
  /** 打开爆款弹窗 */
  onOpenViralRemake?: () => void;
  /** 打开出海弹窗 */
  onOpenOverseas?: () => void;
  /** 额外 class 挂在外层 creation-box */
  boxClassName?: string;
};

/** 将素材库媒体拉取为 File，供建 Session 后上传项目资产 */
async function fetchLibraryItemAsFile(item: MaterialLibraryItem): Promise<File> {
  const url = ensureHttpsOssUrl(item.mediaUrl) || item.mediaUrl;
  if (!url) throw new Error("素材地址无效");
  const res = await fetch(url, { mode: "cors" });
  if (!res.ok) throw new Error(`素材下载失败（${res.status}）`);
  const blob = await res.blob();
  const isVideo = item.mediaType === "video";
  const fallbackType = isVideo ? "video/mp4" : "image/jpeg";
  const ext = isVideo ? "mp4" : "jpg";
  const safeName = (item.title || "素材").replace(/[\\/:*?"<>|]/g, "_").slice(0, 64);
  return new File([blob], `${safeName}.${ext}`, { type: blob.type || fallbackType });
}

export function CreationPromptBox({
  loginNext,
  placeholder = "拖拽 / 粘贴图片到这里，试试智能体技能、视觉风格、素材库",
  className,
  boxClassName,
  onOpenViralRemake,
  onOpenOverseas,
}: Props) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  /** 技能按钮容器（用于点击外部关闭判断） */
  const skillMenuRef = useRef<HTMLDivElement>(null);
  const skillButtonRef = useRef<HTMLButtonElement>(null);
  /** portal 后的技能菜单（不在 creation-tools 内，避免 overflow 裁剪） */
  const skillDropdownRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  /** 出海市场 / 画幅 / 清晰度触发器（菜单 portal，躲开 tools overflow） */
  const marketButtonRef = useRef<HTMLButtonElement>(null);
  const aspectButtonRef = useRef<HTMLButtonElement>(null);
  const clarityButtonRef = useRef<HTMLButtonElement>(null);

  const [text, setText] = useState("");
  const [selectedSkill, setSelectedSkill] = useState<SkillItem | null>(null);
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const [skillMenuPos, setSkillMenuPos] = useState<{ top: number; left: number } | null>(
    null
  );
  const [portalReady, setPortalReady] = useState(false);
  const [styleId, setStyleId] = useState(DEFAULT_VISUAL_STYLE_ID);
  const [styleDialogOpen, setStyleDialogOpen] = useState(false);
  const [libraryDialogOpen, setLibraryDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [pendingAttaches, setPendingAttaches] = useState<PendingAttach[]>([]);
  const [pendingLibraryRefs, setPendingLibraryRefs] = useState<PendingLibraryRef[]>([]);
  const [dragOver, setDragOver] = useState(false);
  // 爆款 / 出海：创作框底部参数（提交写入会话）
  const [composerAspect, setComposerAspect] = useState<ViralRemakeAspect>("9:16");
  const [composerClarity, setComposerClarity] = useState<ViralRemakeClarity>("1080p");
  const [overseasMarketId, setOverseasMarketId] = useState<OverseasMarketId>("US");
  const [aspectMenuOpen, setAspectMenuOpen] = useState(false);
  const [clarityMenuOpen, setClarityMenuOpen] = useState(false);
  const [marketMenuOpen, setMarketMenuOpen] = useState(false);

  const skillsQuery = useQuery({
    queryKey: ["skills", "catalog"],
    queryFn: () => listSkills(),
    staleTime: 60_000,
  });

  const mineQuery = useQuery({
    queryKey: ["skills", "mine"],
    queryFn: listMySkills,
    enabled: Boolean(user),
    staleTime: 30_000,
  });

  const { styles: visualStyles } = useVisualStyles();

  const skillOptions = useMemo(() => {
    const pub = skillsQuery.data?.items ?? [];
    const mine = mineQuery.data ?? [];
    const seen = new Set(pub.map((s) => s.slug));
    return [...pub, ...mine.filter((s) => !seen.has(s.slug))];
  }, [skillsQuery.data?.items, mineQuery.data]);

  // 自由创作 / 无 Wizard 的 Skill 才展示轨 S
  const needsOrchestration = useMemo(() => {
    const skill = selectedSkill;
    if (!skill) return true;
    const kind = skill.entryKind || skill.slug;
    return kind !== "viral_remake" && kind !== "overseas_localize" && kind !== "overseas";
  }, [selectedSkill]);

  const selectedSkillKind = selectedSkill?.entryKind || selectedSkill?.slug || "";
  const showViralOptions =
    selectedSkillKind === "viral_remake" || selectedSkillKind === VIRAL_REMAKE_SKILL;
  const showOverseasOptions =
    selectedSkillKind === "overseas_localize" ||
    selectedSkillKind === "overseas" ||
    selectedSkillKind === OVERSEAS_LOCALIZE_SKILL;
  const showVideoOptions = showViralOptions || showOverseasOptions;

  const closeComposerParamMenus = useCallback((keep?: "aspect" | "clarity" | "market") => {
    if (keep !== "aspect") setAspectMenuOpen(false);
    if (keep !== "clarity") setClarityMenuOpen(false);
    if (keep !== "market") setMarketMenuOpen(false);
  }, []);

  useEffect(() => {
    if (!showVideoOptions && !showOverseasOptions) {
      setAspectMenuOpen(false);
      setClarityMenuOpen(false);
      setMarketMenuOpen(false);
    }
  }, [showVideoOptions, showOverseasOptions]);

  const orchQuoteQuery = useQuery({
    queryKey: ["credits", "agent-skill-pricing", selectedSkill?.slug ?? ""],
    queryFn: () => getAgentSkillPricing(selectedSkill?.slug),
    enabled: Boolean(user) && needsOrchestration,
    staleTime: 60_000,
  });

  const orchCost = orchQuoteQuery.data?.quote?.total ?? 0;
  const orchEnabled = orchQuoteQuery.data?.creditsEnabled !== false;

  const styleLabel = useMemo(() => {
    const hit = visualStyles.find((s) => s.id === styleId);
    if (!hit || styleId === DEFAULT_VISUAL_STYLE_ID) return "视觉风格";
    return hit.label || "视觉风格";
  }, [visualStyles, styleId]);

  const refCount = pendingAttaches.length + pendingLibraryRefs.length;

  /** 应用目录卡预选：命中前不删 session，避免 mine 列表未就绪时丢 slug */
  const applyPreselectSkill = useCallback(() => {
    try {
      const pre = sessionStorage.getItem("jumeng:preselect_skill");
      if (!pre) return;
      const hit = skillOptions.find(
        (s) =>
          s.slug === pre ||
          s.entryKind === pre ||
          (pre === OVERSEAS_LOCALIZE_SKILL &&
            (s.entryKind === "overseas" || s.slug === "overseas")) ||
          (pre === "overseas" &&
            (s.slug === OVERSEAS_LOCALIZE_SKILL || s.entryKind === OVERSEAS_LOCALIZE_SKILL)) ||
          // 爆款复刻：slug / entryKind 互认
          (pre === VIRAL_REMAKE_SKILL &&
            (s.entryKind === "viral_remake" || s.slug === VIRAL_REMAKE_SKILL)) ||
          (pre === "viral_remake" &&
            (s.slug === VIRAL_REMAKE_SKILL || s.entryKind === VIRAL_REMAKE_SKILL))
      );
      if (!hit) return;
      sessionStorage.removeItem("jumeng:preselect_skill");
      setSelectedSkill(hit);
      if (hit.defaultStyleId && hit.defaultStyleId !== DEFAULT_VISUAL_STYLE_ID) {
        setStyleId(hit.defaultStyleId);
      }
    } catch {
      /* ignore */
    }
  }, [skillOptions]);

  useEffect(() => {
    applyPreselectSkill();
  }, [applyPreselectSkill]);

  // 技能页点卡后立刻同步芯片（不依赖 skillOptions 变化）
  useEffect(() => {
    const onPre = () => applyPreselectSkill();
    window.addEventListener("jumeng:preselect-skill", onPre);
    return () => window.removeEventListener("jumeng:preselect-skill", onPre);
  }, [applyPreselectSkill]);

  useEffect(() => {
    setPortalReady(true);
  }, []);

  /** 技能菜单贴按钮上方，随滚动/缩放更新位置 */
  useLayoutEffect(() => {
    if (!skillMenuOpen || !skillButtonRef.current) {
      setSkillMenuPos(null);
      return;
    }
    const update = () => {
      const btn = skillButtonRef.current;
      if (!btn) return;
      const r = btn.getBoundingClientRect();
      const menuWidth = 256;
      const left = Math.min(
        Math.max(8, r.left),
        Math.max(8, window.innerWidth - menuWidth - 8)
      );
      setSkillMenuPos({ top: r.top - 8, left });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [skillMenuOpen]);

  useEffect(() => {
    if (!skillMenuOpen) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      // 按钮与 portal 菜单都算「内部」，避免点选项时被先关掉
      if (skillMenuRef.current?.contains(t)) return;
      if (skillDropdownRef.current?.contains(t)) return;
      setSkillMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [skillMenuOpen]);

  useEffect(() => {
    return () => {
      for (const a of pendingAttaches) {
        try {
          URL.revokeObjectURL(a.previewUrl);
        } catch {
          /* ignore */
        }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅卸载时清理
  }, []);

  const ensureLogin = useCallback(() => {
    if (user) return true;
    router.push(`/login?next=${encodeURIComponent(loginNext)}`);
    return false;
  }, [user, router, loginNext]);

  const addFiles = useCallback((files: FileList | File[]) => {
    // 创作框统一支持图片 / 视频 / 音频（不再仅爆款/出海才可选视频）
    const list = Array.from(files).filter((f) => !!inferAssetCategory(f));
    if (!list.length) {
      toast.error("请添加图片、视频或音频文件");
      return;
    }
    setPendingAttaches((prev) => {
      const next = [...prev];
      for (const file of list.slice(0, 8 - prev.length)) {
        next.push({
          localId: `att_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
          name: file.name,
          file,
          previewUrl: URL.createObjectURL(file),
        });
      }
      return next.slice(0, 8);
    });
  }, []);

  const removeAttach = useCallback((id: string) => {
    setPendingAttaches((prev) => {
      const hit = prev.find((a) => a.localId === id);
      if (hit) {
        try {
          URL.revokeObjectURL(hit.previewUrl);
        } catch {
          /* ignore */
        }
      }
      return prev.filter((a) => a.localId !== id);
    });
  }, []);

  const removeLibraryRef = useCallback((id: string) => {
    setPendingLibraryRefs((prev) => prev.filter((a) => a.localId !== id));
  }, []);

  /** 从素材库选用：追加参考，不替换已有附件 */
  const handleLibrarySelect = useCallback((item: MaterialLibraryItem) => {
    setPendingLibraryRefs((prev) => {
      if (prev.some((p) => p.item.id === item.id)) {
        toast.message(`「${item.title}」已在参考中`);
        return prev;
      }
      if (prev.length + pendingAttaches.length >= 8) {
        toast.error("参考最多 8 个");
        return prev;
      }
      return [
        ...prev,
        {
          localId: `lib_${item.id}`,
          item,
        },
      ];
    });
    setLibraryDialogOpen(false);
    toast.success(`已添加素材「${item.title}」`);
  }, [pendingAttaches.length]);

  /** 历史弹窗入口已废弃：一律走创作框建 Session（保留 props 兼容调用方） */
  const openSkillEntry = useCallback((_skill: SkillItem) => {
    void _skill;
    void onOpenOverseas;
    void onOpenViralRemake;
    return false;
  }, [onOpenOverseas, onOpenViralRemake]);

  /** 选中智能体技能（菜单已 portal，避免被 overflow / 推荐条挡住） */
  const pickSkill = useCallback(
    (skill: SkillItem | null) => {
      setSelectedSkill(skill);
      setSkillMenuOpen(false);
      if (skill) {
        const styleFromSkill =
          skill.defaults?.styleId || skill.defaultStyleId || undefined;
        if (styleFromSkill && styleFromSkill !== DEFAULT_VISUAL_STYLE_ID) {
          setStyleId(styleFromSkill);
        }
        // 我的 Skill 与其它技能一样：只选中引用，不预填正文（用户自己写 / 加附件）
        openSkillEntry(skill);
      }
    },
    [openSkillEntry]
  );

  const handleSubmit = async () => {
    if (!ensureLogin()) return;
    const message = text.trim();
    const skill = selectedSkill;

    if (skill && openSkillEntry(skill)) {
      setSkillMenuOpen(false);
      return;
    }

    if (!message && !skill && !pendingAttaches.length && !pendingLibraryRefs.length) {
      toast.error("请输入想法，或先选择智能体技能 / 添加参考");
      return;
    }

    setSubmitting(true);
    try {
      const effectiveStyle =
        styleId && styleId !== DEFAULT_VISUAL_STYLE_ID
          ? styleId
          : skill?.defaultStyleId || undefined;

      const libNames = pendingLibraryRefs.map((r) => r.item.title).filter(Boolean);
      const attachHintParts: string[] = [];
      if (pendingAttaches.length) {
        const videoN = pendingAttaches.filter((a) => a.file.type.startsWith("video/")).length;
        const imageN = pendingAttaches.length - videoN;
        const parts: string[] = [];
        if (imageN) parts.push(`${imageN} 张本地参考图`);
        if (videoN) parts.push(`${videoN} 个本地参考视频`);
        attachHintParts.push(
          `用户附带 ${parts.join("、")}，请作为角色/场景/运镜一致性约束`
        );
      }
      if (libNames.length) {
        attachHintParts.push(
          `用户从素材库选用：${libNames.join("、")}，请作为参考约束`
        );
      }
      // 提示词库：把管理端配置的正文注入会话，供 Agent 作图/视频参考
      const promptBodies = pendingLibraryRefs
        .filter((r) => r.item.category === "prompt")
        .map((r) => String(r.item.promptText || "").trim())
        .filter(Boolean);
      if (promptBodies.length) {
        attachHintParts.push(
          `提示词库正文：\n${promptBodies.join("\n---\n")}`
        );
      }
      const attachHint = attachHintParts.length
        ? `\n\n[${attachHintParts.join("；")}]`
        : "";

      const skillKind = skill?.entryKind || skill?.slug || "";
      const isOverseas =
        skillKind === "overseas_localize" ||
        skillKind === "overseas" ||
        skillKind === OVERSEAS_LOCALIZE_SKILL;
      const isViral =
        skillKind === "viral_remake" || skillKind === VIRAL_REMAKE_SKILL;
      const isMine = Boolean(
        skill && (skill.visibility === "private" || skill.ownerUserId)
      );
      const mineDefaults = skill?.defaults ?? {};
      // 须有正文或附件；我的 Skill 不自动用另存想法顶替用户输入
      if (!message && !pendingAttaches.length && !pendingLibraryRefs.length) {
        toast.error("请输入内容，或添加附件后再发送");
        setSubmitting(false);
        return;
      }
      const seedMessage =
        message ||
        (skill ? `使用 Skill「${skill.title}」创作` : "开始创作");

      const session = await createAgentSession({
        message: seedMessage + attachHint,
        skillSlug: skill?.slug,
        styleId: effectiveStyle || mineDefaults.styleId || undefined,
        controllerModel:
          mineDefaults.controllerModel || DEFAULT_CONTROLLER_MODEL_ID,
        genMode:
          (mineDefaults.genMode as "smart" | "canvas" | "chat" | undefined) ||
          undefined,
        // 我的 Skill / 爆款出海：画幅清晰度取创作框选项或另存默认
        aspectRatio: isViral || isOverseas
          ? composerAspect
          : isMine
            ? mineDefaults.aspectRatio || undefined
            : undefined,
        clarity: isViral || isOverseas
          ? composerClarity
          : isMine
            ? mineDefaults.clarity || undefined
            : undefined,
        targetMarketId: isOverseas ? overseasMarketId : undefined,
      });

      // 建项后立刻上传参考；全部写入会话 referenceAssetIds，供画布 Agent Composer 回显
      const boundAssetIds: string[] = [];
      for (const a of pendingAttaches) {
        try {
          const category = inferAssetCategory(a.file) || "image";
          const asset = await uploadAsset({
            projectId: session.projectId,
            file: a.file,
            category,
            title: a.name,
          });
          if (!asset?.id) continue;
          boundAssetIds.push(asset.id);
        } catch {
          /* 单张失败不阻断进画布 */
        }
      }
      for (const ref of pendingLibraryRefs) {
        try {
          const file = await fetchLibraryItemAsFile(ref.item);
          const isVideo = ref.item.mediaType === "video";
          const asset = await uploadAsset({
            projectId: session.projectId,
            file,
            category: isVideo ? "video" : "image",
            title: ref.item.title,
          });
          if (!asset?.id) continue;
          boundAssetIds.push(asset.id);
        } catch {
          /* 单条失败不阻断 */
        }
      }
      if (boundAssetIds.length > 0) {
        try {
          await bindAgentSessionReferenceAssets(session.sessionId, boundAssetIds);
          toast.message(`已附带 ${boundAssetIds.length} 个参考到对话`);
        } catch {
          toast.message(`已上传 ${boundAssetIds.length} 个参考到项目素材`);
        }
      }

      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      void queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
      setText("");
      setSelectedSkill(null);
      for (const a of pendingAttaches) {
        try {
          URL.revokeObjectURL(a.previewUrl);
        } catch {
          /* ignore */
        }
      }
      setPendingAttaches([]);
      setPendingLibraryRefs([]);
      // 用户补充说明同步到画布 Agent 输入框（与附件芯片同路径）
      if (message) {
        try {
          sessionStorage.setItem("jumeng:prefill_agent_draft", message);
        } catch {
          /* ignore */
        }
      }
      const skillQs = skill?.slug ? `&skill=${encodeURIComponent(skill.slug)}` : "";
      router.push(`/${session.projectId}?session=${session.sessionId}${skillQs}`);
      toast.success("已创建项目，正在进入画布…");
    } catch (err) {
      if (err instanceof ApiError && (err.code === "INSUFFICIENT_CREDITS" || err.code === "5501")) {
        toast.error(err.message || "算力不足，请先充值");
      } else {
        toast.error(err instanceof Error ? err.message : "创建失败，请稍后重试");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const skillLabel = useMemo(() => {
    if (selectedSkill) return selectedSkill.title;
    return "技能";
  }, [selectedSkill]);

  const creditHint =
    user && needsOrchestration && orchCost > 0
      ? `对话约 ${formatCreditLabel(orchCost, orchEnabled)}；生图/生视频按模型另计`
      : "开始创作";

  return (
    <div
      className={cn(
        "creation-box",
        boxClassName,
        className,
        dragOver && "ring-2 ring-violet-400/50"
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (!ensureLogin()) return;
        if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*,video/*,audio/*,.mp4,.webm,.mov,.mkv,.avi,.m4v,.jpg,.jpeg,.png,.gif,.webp,.bmp,.svg,.mp3,.wav,.m4a,.aac,.ogg,.flac"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) addFiles(e.target.files);
          e.target.value = "";
        }}
      />
      <div className="creation-top">
        {refCount > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5 px-0.5">
            {pendingAttaches.map((a) => (
              <span
                key={a.localId}
                className="inline-flex max-w-[120px] items-center gap-1 rounded-lg border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-white/80"
              >
                {inferAssetCategory(a.file) === "video" ? (
                  <video
                    src={a.previewUrl}
                    className="size-4 rounded object-cover"
                    muted
                    playsInline
                  />
                ) : inferAssetCategory(a.file) === "audio" ? (
                  <span className="flex size-4 items-center justify-center text-[9px] opacity-70">
                    ♪
                  </span>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={a.previewUrl} alt="" className="size-4 rounded object-cover" />
                )}
                <span className="truncate">{a.name}</span>
                <button
                  type="button"
                  className="opacity-50 hover:opacity-100"
                  onClick={() => removeAttach(a.localId)}
                >
                  <X size={10} />
                </button>
              </span>
            ))}
            {pendingLibraryRefs.map((ref) => {
              const url = ensureHttpsOssUrl(ref.item.mediaUrl) || ref.item.mediaUrl;
              return (
                <span
                  key={ref.localId}
                  className="inline-flex max-w-[120px] items-center gap-1 rounded-lg border border-white/10 bg-white/5 px-1.5 py-0.5 text-[10px] text-white/80"
                  title="素材库"
                >
                  {ref.item.mediaType === "video" && url ? (
                    <video src={url} className="size-4 rounded object-cover" muted playsInline />
                  ) : url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={url} alt="" className="size-4 rounded object-cover" />
                  ) : (
                    <LayoutGrid size={12} className="opacity-50" />
                  )}
                  <span className="truncate">{ref.item.title}</span>
                  <button
                    type="button"
                    className="opacity-50 hover:opacity-100"
                    onClick={() => removeLibraryRef(ref.localId)}
                  >
                    <X size={10} />
                  </button>
                </span>
              );
            })}
          </div>
        ) : null}
        {/* 已选 Skill：输入区上方可移除芯片（对齐参考「×技能名」） */}
        {selectedSkill ? (
          <div className="creation-skill-chip-row">
            <button
              type="button"
              className="creation-skill-chip"
              title="点击取消选择该 Skill"
              onClick={() => setSelectedSkill(null)}
            >
              <span className="creation-skill-chip-x" aria-hidden>
                ×
              </span>
              <span className="creation-skill-chip-label">{selectedSkill.title}</span>
            </button>
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          aria-label="创作描述"
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onPaste={(e) => {
            const items = e.clipboardData?.items;
            if (!items) return;
            const files: File[] = [];
            for (const it of Array.from(items)) {
              if (it.type.startsWith("image/")) {
                const f = it.getAsFile();
                if (f) files.push(f);
              }
            }
            if (files.length) {
              if (!ensureLogin()) return;
              addFiles(files);
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleSubmit();
            }
          }}
          disabled={submitting}
        />
      </div>
      <div className="creation-bottom">
        <div className="creation-tools">
          <button
            type="button"
            className="tool-icon-btn"
            aria-label="添加参考图/视频/音频"
            title="添加参考图、视频或音频"
            disabled={submitting}
            onClick={() => {
              if (!ensureLogin()) return;
              fileInputRef.current?.click();
            }}
          >
            <Paperclip size={16} strokeWidth={1.8} />
          </button>
          <span className="tool-divider" aria-hidden="true" />
          <div className="relative" ref={skillMenuRef}>
            <button
              ref={skillButtonRef}
              type="button"
              aria-expanded={skillMenuOpen}
              title="选择智能体技能"
              onClick={() => {
                if (!ensureLogin()) return;
                closeComposerParamMenus();
                setSkillMenuOpen((v) => !v);
              }}
            >
              <Zap size={14} strokeWidth={1.8} />
              <span className="max-w-[5.5rem] truncate">{skillLabel}</span>
              <ChevronDown size={12} strokeWidth={2.2} />
            </button>
          </div>

          {/* 按技能展示参数：爆款=比例+清晰度；出海=市场+比例+清晰度（菜单 portal，避免 tools overflow 裁切） */}
          {showOverseasOptions ? (
            <div className="relative" data-creation-skill-param>
              <button
                ref={marketButtonRef}
                type="button"
                title="选择出海国家版本"
                disabled={submitting}
                aria-expanded={marketMenuOpen}
                onClick={() => {
                  if (!ensureLogin()) return;
                  setMarketMenuOpen((v) => !v);
                  closeComposerParamMenus("market");
                }}
              >
                <Globe2 size={14} strokeWidth={1.8} />
                <span className="max-w-[5.5rem] truncate">
                  {overseasMarketLabel(overseasMarketId)}
                </span>
                <ChevronDown size={12} strokeWidth={2.2} />
              </button>
              <FixedAboveMenu
                open={marketMenuOpen}
                anchorEl={marketButtonRef.current}
                minWidth={168}
                onRequestClose={() => setMarketMenuOpen(false)}
              >
                {OVERSEAS_MARKETS.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    className={cn(
                      "block w-full px-3 py-1.5 text-left text-[11px] text-white/80 hover:bg-white/10",
                      m.id === overseasMarketId && "text-violet-300"
                    )}
                    onClick={() => {
                      setOverseasMarketId(m.id);
                      setMarketMenuOpen(false);
                    }}
                  >
                    <div className="font-medium">{m.label}</div>
                    <div className="text-[10px] text-white/40">{m.language}</div>
                  </button>
                ))}
              </FixedAboveMenu>
            </div>
          ) : null}

          {showVideoOptions ? (
            <>
              <div className="relative" data-creation-skill-param>
                <button
                  ref={aspectButtonRef}
                  type="button"
                  title="视频比例"
                  disabled={submitting}
                  aria-expanded={aspectMenuOpen}
                  onClick={() => {
                    if (!ensureLogin()) return;
                    setAspectMenuOpen((v) => !v);
                    closeComposerParamMenus("aspect");
                  }}
                >
                  <span>{composerAspect}</span>
                  <ChevronDown size={12} strokeWidth={2.2} />
                </button>
                <FixedAboveMenu
                  open={aspectMenuOpen}
                  anchorEl={aspectButtonRef.current}
                  minWidth={96}
                  onRequestClose={() => setAspectMenuOpen(false)}
                >
                  {SKILL_COMPOSER_ASPECT_OPTIONS.map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      className={cn(
                        "block w-full px-3 py-1.5 text-left text-[11px] text-white/80 hover:bg-white/10",
                        opt === composerAspect && "text-violet-300"
                      )}
                      onClick={() => {
                        setComposerAspect(opt);
                        setAspectMenuOpen(false);
                      }}
                    >
                      {opt}
                    </button>
                  ))}
                </FixedAboveMenu>
              </div>
              <div className="relative" data-creation-skill-param>
                <button
                  ref={clarityButtonRef}
                  type="button"
                  title="清晰度"
                  disabled={submitting}
                  aria-expanded={clarityMenuOpen}
                  onClick={() => {
                    if (!ensureLogin()) return;
                    setClarityMenuOpen((v) => !v);
                    closeComposerParamMenus("clarity");
                  }}
                >
                  <span>{composerClarity}</span>
                  <ChevronDown size={12} strokeWidth={2.2} />
                </button>
                <FixedAboveMenu
                  open={clarityMenuOpen}
                  anchorEl={clarityButtonRef.current}
                  minWidth={96}
                  onRequestClose={() => setClarityMenuOpen(false)}
                >
                  {SKILL_COMPOSER_CLARITY_OPTIONS.map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      className={cn(
                        "block w-full px-3 py-1.5 text-left text-[11px] text-white/80 hover:bg-white/10",
                        opt === composerClarity && "text-violet-300"
                      )}
                      onClick={() => {
                        setComposerClarity(opt);
                        setClarityMenuOpen(false);
                      }}
                    >
                      {opt}
                    </button>
                  ))}
                </FixedAboveMenu>
              </div>
            </>
          ) : null}

          {/* 控制器模型由后台配置，用户侧不展示 */}

          <button
            type="button"
            title="选择视觉风格"
            onClick={() => {
              if (!ensureLogin()) return;
              setStyleDialogOpen(true);
            }}
          >
            <Palette size={14} strokeWidth={1.8} />
            <span className="max-w-[5.5rem] truncate">{styleLabel}</span>
          </button>
          <button
            type="button"
            title="从素材库添加参考"
            onClick={() => {
              if (!ensureLogin()) return;
              setLibraryDialogOpen(true);
            }}
          >
            <LayoutGrid size={14} strokeWidth={1.8} />
            <span>{refCount ? `素材库·${refCount}` : "素材库"}</span>
          </button>
        </div>
        <div className="flex items-center gap-2">
          {user && needsOrchestration && orchCost > 0 ? (
            <span
              className="hidden max-w-[11rem] truncate text-[11px] text-white/45 sm:inline"
              title="一次对话为轨 S；镜头生图/生视频按模型另计轨 G"
            >
              对话约 {formatCreditLabel(orchCost, orchEnabled)}
            </span>
          ) : null}
          <button
            type="button"
            className="create-btn"
            aria-label="开始创作"
            title={creditHint}
            onClick={() => void handleSubmit()}
            disabled={submitting}
          >
            {submitting ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <ArrowUp size={16} strokeWidth={2.4} />
            )}
          </button>
        </div>
      </div>
      <VisualStylePickerDialog
        open={styleDialogOpen}
        onOpenChange={setStyleDialogOpen}
        styles={visualStyles}
        value={styleId}
        onSelect={setStyleId}
      />
      <CreationMaterialLibraryDialog
        open={libraryDialogOpen}
        onClose={() => setLibraryDialogOpen(false)}
        onSelect={handleLibrarySelect}
      />
      {/* 技能菜单挂到 body：躲开 creation-tools overflow 与下方推荐条叠层 */}
      {portalReady &&
      skillMenuOpen &&
      skillMenuPos
        ? createPortal(
            <div
              ref={skillDropdownRef}
              role="listbox"
              className="fixed z-[210] max-h-64 w-64 overflow-auto rounded-xl border border-white/10 bg-[#1a1a28] p-1 shadow-2xl"
              style={{
                left: skillMenuPos.left,
                top: skillMenuPos.top,
                transform: "translateY(-100%)",
              }}
            >
              <div className="flex items-center justify-between px-3 py-1.5">
                <span className="text-[10px] font-medium tracking-wide text-white/40">
                  智能体技能
                </span>
                <button
                  type="button"
                  className="text-[10px] text-white/40 underline decoration-white/15 hover:text-white/70"
                  onClick={() => {
                    setSkillMenuOpen(false);
                    router.push("/skills");
                  }}
                >
                  全部
                </button>
              </div>
              <button
                type="button"
                className="block w-full rounded-lg px-3 py-2 text-left text-sm text-white/85 hover:bg-white/10"
                onClick={() => pickSkill(null)}
              >
                不使用技能
              </button>
              {skillOptions.map((s) => (
                <button
                  key={s.slug}
                  type="button"
                  className={`block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10 ${
                    selectedSkill?.slug === s.slug
                      ? "bg-white/10 text-white"
                      : "text-white/85"
                  }`}
                  onClick={() => pickSkill(s)}
                >
                  <div className="font-medium">
                    {s.title}
                    {s.visibility === "private" ? (
                      <span className="ml-1 text-[10px] text-white/40">我的</span>
                    ) : null}
                  </div>
                  <div className="line-clamp-2 text-xs text-white/40">
                    {s.pipelineSummary || s.description}
                  </div>
                </button>
              ))}
              {!skillOptions.length ? (
                <div className="px-3 py-2 text-xs text-white/40">
                  {skillsQuery.isLoading ? "加载中…" : "暂无智能体技能"}
                </div>
              ) : null}
            </div>,
            document.body
          )
        : null}
    </div>
  );
}

/** 供技能卡：聚焦创作框并触发预选 Skill 芯片同步 */
export function focusCreationPromptBox() {
  const el = document.querySelector<HTMLTextAreaElement>(
    ".creation-box textarea, .skills-hero textarea"
  );
  el?.focus();
  document
    .querySelector(".skills-hero, .hero")
    ?.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    window.dispatchEvent(new Event("jumeng:preselect-skill"));
  } catch {
    /* ignore */
  }
}
