"use client";

/**
 * 画布右下角 AI 圆形按钮：点击展开对话；控制器模型由后台配置（用户不可见）；续聊操控画布。
 */

import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Crosshair,
  Globe2,
  History,
  Layers,
  Loader2,
  MessageSquarePlus,
  Paperclip,
  Send,
  Square,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import {
  bindAgentSessionReferenceAssets,
  createAgentSession,
  getAgentSession,
  listControllerModels,
  listProjectAgentSessions,
  postAgentSessionMessage,
  steerAgentSession,
  stopAgentSession,
  subscribeAgentSessionEvents,
  type AgentMessage,
  type AgentRuntimeSseEvent,
  type AskChoicesArtifact,
  type AskedSummaryArtifact,
  type ControllerModelOption,
  type MediaAssetItem,
  type MediaAssetsArtifact,
} from "@/lib/api/agentSessions";
import {
  applySkillRecipe,
  listMySkills,
  listSkills,
  type SkillItem,
} from "@/lib/api/skills";
import { ApiError } from "@/lib/api/client";
import { uploadAsset, fetchAssetById } from "@/lib/api/assets";
import { listModels } from "@/lib/api/models";
import { formatCreditLabel, getAgentSkillPricing } from "@/lib/api/credits";
import { ensureHttpsOssUrl } from "@/lib/signedUrl";
import { inferAssetCategory } from "@/lib/canvas/fileDrop";
import { buildModelOptions } from "@/lib/canvas/nodeModelRouting";
import {
  getAgentCanvasBusy,
  setAgentCanvasBusy,
  clearAgentCanvasBusy,
  requestAgentCanvasStop,
  subscribeAgentCanvasBusy,
} from "@/lib/canvas/agentCanvasBusy";
import { projectAgentCanvasOps } from "@/lib/canvas/projectAgentCanvasOps";
import {
  setAgentProjectGraphSnapshot,
  useAgentTimelineSummary,
} from "@/lib/canvas/agentProjectGraphCache";
import {
  getAgentCanvasMediaModels,
  hydrateAgentCanvasMediaModels,
  subscribeAgentCanvasMediaModels,
} from "@/lib/canvas/agentCanvasMediaModels";
import {
  AgentCanvasMediaModelMenu,
  AgentCanvasMediaModelTrigger,
} from "@/components/canvas/AgentCanvasMediaModelMenu";
import { AgentNodeRefChipRow } from "@/components/canvas/AgentNodeRefChips";
import { FixedAboveMenu } from "@/components/huabu/FixedAboveMenu";
import {
  buildAgentNodeRef,
  buildMessageWithNodeRefs,
  type AgentNodeRef,
} from "@/lib/canvas/agentNodeRefs";
import { takeFreshCanvasSnapshot } from "@/lib/canvas/buildCanvasSnapshot";
import { getNodeWorldPosition } from "@/lib/canvas/canvasOverlayTransform";
import { useProjectAssetManifest } from "@/lib/canvas/useProjectAssets";
import {
  OVERSEAS_MARKETS,
  getOverseasMarket,
  type OverseasMarketId,
} from "@/lib/canvas/overseasMarkets";
import {
  loadOverseasSession,
  OVERSEAS_LOCALIZE_SKILL,
} from "@/lib/canvas/overseasSession";
import { ensureOverseasLocalizeNode } from "@/lib/canvas/overseasLocalizeNode";
import {
  loadViralRemakeSession,
  VIRAL_REMAKE_SESSION_EVENT,
  VIRAL_REMAKE_SKILL,
} from "@/lib/canvas/viralRemakeSession";
import { withBasePath } from "@/lib/basePath";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/stores/authStore";
import { useCanvasStore } from "@/stores/canvasStore";

/** 是否为爆款拉片复刻 Skill */
function isViralRemakeSkill(skill: SkillItem | null | undefined): boolean {
  const kind = skill?.entryKind || skill?.slug;
  return kind === VIRAL_REMAKE_SKILL || kind === "viral_remake";
}

/** 是否为一键出海 Skill */
function isOverseasSkill(skill: SkillItem | null | undefined): boolean {
  const kind = skill?.entryKind || skill?.slug;
  return kind === OVERSEAS_LOCALIZE_SKILL || kind === "overseas";
}

/** 爆款或出海向导类 Skill（允许附件视频） */
function isWizardSkill(skill: SkillItem | null | undefined): boolean {
  return isViralRemakeSkill(skill) || isOverseasSkill(skill);
}

type ViralAspectRatio = "9:16" | "16:9" | "1:1";
type ViralClarity = "1080p" | "720p";

const VIRAL_ASPECT_OPTIONS: ViralAspectRatio[] = ["9:16", "16:9", "1:1"];
const VIRAL_CLARITY_OPTIONS: ViralClarity[] = ["1080p", "720p"];

type Props = {
  projectId: string;
};

type FabPos = { x: number; y: number };

const FAB_SIZE = 56;
const FAB_MARGIN = 16;
/** 默认停在缩放工具条上方 */
const FAB_DEFAULT_BOTTOM = 76;
const FAB_POS_KEY = "jumeng:agent_fab_pos";
const DRAG_THRESHOLD = 5;

const ROLE_LABEL: Record<string, string> = {
  orchestrator: "助手",
  art_director: "导演",
  scriptwriter: "剧本",
  character_designer: "角色",
  scene_creator: "场景",
  animator: "动画",
  editor: "剪辑",
  sound_engineer: "音效",
};

const DEFAULT_CONTROLLER_MODEL_ID = "doubao_seed_evolving";

const DEFAULT_CONTROLLER_MODELS: ControllerModelOption[] = [
  { id: "doubao_seed_evolving", label: "豆包 Seed Evolving", supportsFiles: true },
  { id: "doubao_pro", label: "豆包 Pro", supportsFiles: true },
  { id: "deepseek_v4_flash", label: "DeepSeek V4 Flash", supportsFiles: true },
  { id: "doubao_seed_21_pro", label: "豆包 Seed 2.1 Pro", supportsFiles: true },
  { id: "deepseek_v3", label: "DeepSeek V3", supportsFiles: false },
  { id: "deepseek_v4_pro", label: "DeepSeek V4 Pro", supportsFiles: true },
  { id: "deepseek_r1", label: "DeepSeek R1", supportsFiles: false },
  { id: "rh_gpt_56_sol", label: "GPT-5.6 Sol", supportsFiles: true },
  { id: "rh_gpt_56_terra", label: "GPT-5.6 Terra", supportsFiles: true },
  { id: "rh_gpt_55", label: "GPT-5.5", supportsFiles: true },
  { id: "rh_gemini_35_flash", label: "Gemini 3.5 Flash", supportsFiles: true },
  { id: "rh_gemini_31_flash_lite", label: "Gemini 3.1 Flash Lite", supportsFiles: true },
  { id: "rh_claude_fable_5", label: "Claude Fable 5", supportsFiles: true },
  { id: "rh_claude_opus_48", label: "Claude Opus 4.8", supportsFiles: true },
];

const GEN_MODES = [
  { id: "smart" as const, label: "智能编排", hint: "读画布后调用工具落地（多角度/打光/九宫格/故事板等）" },
  { id: "canvas" as const, label: "画布操控", hint: "直接改节点、连线、跑画布工具" },
  { id: "chat" as const, label: "仅对话", hint: "不改画布，纯聊天" },
];

type GenModeId = (typeof GEN_MODES)[number]["id"];

const MODEL_STORAGE_KEY = "jumeng:agent_controller_model";
const GEN_MODE_KEY = "jumeng:agent_gen_mode";
const OVERSEAS_MARKET_KEY = "jumeng:overseas_target_market";
const FAB_ICON = withBasePath("/branding/ai-assistant-fab.png");

/** 爆款/出海会话是否仍缺参考视频（已拉片/成片后不应再提示上传） */
function wizardStillNeedsUpload(projectId: string, messages: AgentMessage[]): boolean {
  const viralStored = loadViralRemakeSession(projectId);
  const overseasStored = loadOverseasSession(projectId);
  if (viralStored?.videoAssetId || overseasStored?.videoAssetId) return false;
  const phase = String(
    viralStored?.progress?.phase || overseasStored?.progress?.phase || ""
  );
  if (phase && !["idle", "bootstrap", "error"].includes(phase)) {
    return false;
  }
  if (
    viralStored?.progress?.gridNodeId ||
    overseasStored?.progress?.gridNodeId ||
    (viralStored?.progress?.shotCount ?? 0) > 0 ||
    (overseasStored?.progress?.shotCount ?? 0) > 0
  ) {
    return false;
  }

  const nodes = useCanvasStore.getState().nodes;
  if (nodes.some((n) => n.type === "finished_clips_grid")) return false;
  const grid = nodes.find((n) => n.type === "storyboard_grid");
  const shots = (grid?.data as { params?: { shots?: unknown } } | undefined)?.params?.shots;
  if (Array.isArray(shots) && shots.length > 0) return false;
  const meta = (grid?.data as { params?: { viralRemakeMeta?: { videoAssetId?: string } } } | undefined)
    ?.params?.viralRemakeMeta;
  if (meta?.videoAssetId) return false;

  const blob = messages.map((m) => String(m.content || "")).join("\n");
  if (
    /成片完成|拉片完成|出海拉片完成|已收到参考视频|正在按 Skill pipeline|主体图已|开始批量生成同款|切镜拉片|确认生成|本地化拉片/.test(
      blob
    )
  ) {
    return false;
  }
  return true;
}

function readStoredOverseasMarket(): OverseasMarketId {
  try {
    const raw = localStorage.getItem(OVERSEAS_MARKET_KEY);
    if (raw) return getOverseasMarket(raw).id;
  } catch {
    /* ignore */
  }
  return "US";
}

type PendingAttach = {
  localId: string;
  name: string;
  /** image | video | audio；Windows 上 MIME 常为空，须按扩展名推断后上传 */
  category?: "image" | "video" | "audio";
  assetId?: string;
  previewUrl?: string;
  uploading?: boolean;
};

function getAskedSummary(messages: AgentMessage[]): Array<{ q: string; a: string }> {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const arts = messages[i].artifacts || [];
    for (const a of arts) {
      if (a && typeof a === "object" && (a as AskedSummaryArtifact).kind === "asked_summary") {
        const items = (a as AskedSummaryArtifact).items;
        if (Array.isArray(items) && items.length) return items;
      }
    }
  }
  return [];
}

function getPendingAsk(messages: AgentMessage[]): {
  messageId: string;
  artifact: AskChoicesArtifact;
} | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const arts = messages[i].artifacts || [];
    for (const a of arts) {
      if (
        a &&
        typeof a === "object" &&
        (a as AskChoicesArtifact).kind === "ask_choices" &&
        (a as AskChoicesArtifact).status === "pending"
      ) {
        // 这条询问之后已有用户回复，视为已答（兼容旧数据未改 status）
        const answeredLater = messages.slice(i + 1).some((m) => m.role === "user");
        if (answeredLater) continue;
        return { messageId: messages[i].id, artifact: a as AskChoicesArtifact };
      }
    }
  }
  return null;
}

/** 需要补充说明的选项：点选后仍留输入框；确认类选项点选即提交并收起。 */
function askChoiceNeedsExtraText(choiceId: string | null | undefined): boolean {
  const id = String(choiceId || "").trim().toLowerCase();
  return id === "other" || id === "tweak" || id === "rewrite";
}

function readStoredModel(): string {
  try {
    return localStorage.getItem(MODEL_STORAGE_KEY) || DEFAULT_CONTROLLER_MODEL_ID;
  } catch {
    return DEFAULT_CONTROLLER_MODEL_ID;
  }
}

function readStoredGenMode(): GenModeId {
  try {
    const raw = localStorage.getItem(GEN_MODE_KEY);
    if (raw === "smart" || raw === "canvas" || raw === "chat") return raw;
  } catch {
    /* ignore */
  }
  // AI 面板首次打开默认「画布操控」
  return "canvas";
}

function defaultFabPos(): FabPos {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  return {
    x: Math.max(FAB_MARGIN, window.innerWidth - FAB_MARGIN - FAB_SIZE),
    y: Math.max(FAB_MARGIN, window.innerHeight - FAB_DEFAULT_BOTTOM - FAB_SIZE),
  };
}

function clampFabPos(pos: FabPos): FabPos {
  if (typeof window === "undefined") return pos;
  const maxX = Math.max(FAB_MARGIN, window.innerWidth - FAB_SIZE - FAB_MARGIN);
  const maxY = Math.max(FAB_MARGIN, window.innerHeight - FAB_SIZE - FAB_MARGIN);
  return {
    x: Math.min(maxX, Math.max(FAB_MARGIN, pos.x)),
    y: Math.min(maxY, Math.max(FAB_MARGIN, pos.y)),
  };
}

function readStoredFabPos(): FabPos {
  try {
    const raw = localStorage.getItem(FAB_POS_KEY);
    if (!raw) return defaultFabPos();
    const parsed = JSON.parse(raw) as Partial<FabPos>;
    if (typeof parsed?.x !== "number" || typeof parsed?.y !== "number") return defaultFabPos();
    return clampFabPos({ x: parsed.x, y: parsed.y });
  } catch {
    return defaultFabPos();
  }
}

function persistFabPos(pos: FabPos): void {
  try {
    localStorage.setItem(FAB_POS_KEY, JSON.stringify(pos));
  } catch {
    /* ignore */
  }
}

/** 聊天气泡内展示 AI 生成的图片/视频/音频 */
function AgentChatMediaGrid(props: { projectId: string; items: MediaAssetItem[] }) {
  const { projectId, items } = props;
  const itemsKey = items.map((i) => `${i.assetId || ""}:${i.url || ""}:${i.nodeId || ""}`).join("|");
  const [resolved, setResolved] = useState<
    Array<MediaAssetItem & { displayUrl?: string }>
  >(items);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const next = await Promise.all(
        items.map(async (it) => {
          if (it.url) {
            return { ...it, displayUrl: ensureHttpsOssUrl(it.url) || it.url };
          }
          if (!it.assetId || !projectId) return { ...it };
          try {
            const asset = await fetchAssetById(projectId, it.assetId);
            const url = asset?.fileUrl ? ensureHttpsOssUrl(asset.fileUrl) || asset.fileUrl : "";
            return {
              ...it,
              displayUrl: url || undefined,
              category:
                it.category ||
                (asset?.category === "video" || asset?.category === "audio" || asset?.category === "image"
                  ? asset.category
                  : "image"),
              title: it.title || asset?.title || undefined,
            };
          } catch {
            return { ...it };
          }
        })
      );
      if (!cancelled) setResolved(next);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- itemsKey 已覆盖 items 内容变化
  }, [projectId, itemsKey]);

  if (!items.length) return null;

  return (
    <div className="mt-2 grid grid-cols-2 gap-1.5">
      {resolved.map((it, idx) => {
        const key = it.assetId || it.url || it.nodeId || String(idx);
        const label = it.title || it.nodeName || "素材";
        const cat = it.category || "image";
        return (
          <div
            key={key}
            className="overflow-hidden rounded-lg border border-border-default/50 bg-black/5 dark:border-white/10 dark:bg-white/5"
          >
            {it.displayUrl && cat === "video" ? (
              <video
                src={it.displayUrl}
                className="aspect-video w-full bg-black object-contain"
                controls
                playsInline
                preload="metadata"
              />
            ) : it.displayUrl && cat === "audio" ? (
              <div className="px-2 py-2">
                <audio src={it.displayUrl} controls className="w-full" preload="metadata" />
              </div>
            ) : it.displayUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={it.displayUrl}
                alt={label}
                className="aspect-square w-full object-cover"
                loading="lazy"
              />
            ) : (
              <div className="flex aspect-square items-center justify-center text-[10px] text-muted-foreground">
                加载中…
              </div>
            )}
            <div className="truncate px-1.5 py-1 text-[10px] text-muted-foreground" title={label}>
              {label}
              {it.nodeId ? ` · ${it.nodeId.slice(0, 8)}` : ""}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** LibTV 风格输入卡片：节点引用整块 / 附件 / Skill / 生成模式 / 模型 / 出海市场 / 画幅·清晰度 / 发送 */
function ComposerBox(props: {
  draft: string;
  setDraft: (v: string) => void;
  placeholder: string;
  disabled?: boolean;
  pendingAttaches: PendingAttach[];
  onRemoveAttach: (id: string) => void;
  /** 已引用的画布节点整块（缩略图 + 名称）；由画布点选加入 */
  nodeRefs: AgentNodeRef[];
  onRemoveNodeRef: (nodeId: string) => void;
  onFocusNodeRef: (nodeId: string) => void;
  modelSupportsFiles: boolean;
  onPickFiles: () => void;
  skillLabel: string;
  skillMenuOpen: boolean;
  setSkillMenuOpen: (fn: (v: boolean) => boolean) => void;
  skillOptions: SkillItem[];
  onSelectSkill: (s: SkillItem | null) => void;
  /** 当前选中 Skill slug，用于菜单高亮 */
  selectedSkillSlug?: string | null;
  genMode: GenModeId;
  genModeLabel: string;
  genMenuOpen: boolean;
  setGenMenuOpen: (fn: (v: boolean) => boolean) => void;
  onSelectGenMode: (m: GenModeId) => void;
  /** 画布操控：生图/生视频模型菜单 */
  mediaModelMenuOpen?: boolean;
  setMediaModelMenuOpen?: (fn: (v: boolean) => boolean) => void;
  mediaModelTab?: "image" | "video";
  setMediaModelTab?: (tab: "image" | "video") => void;
  mediaImageLabel?: string;
  mediaVideoLabel?: string;
  modelLabel: string;
  modelMenuOpen: boolean;
  setModelMenuOpen: (fn: (v: boolean) => boolean) => void;
  modelOptions: ControllerModelOption[];
  controllerModel: string;
  onSelectModel: (id: string, label: string) => void;
  /** 一键出海：在模型旁显示目标市场下拉 */
  showOverseasMarket?: boolean;
  overseasMarketId?: OverseasMarketId;
  overseasMarketLabel?: string;
  marketMenuOpen?: boolean;
  setMarketMenuOpen?: (fn: (v: boolean) => boolean) => void;
  onSelectMarket?: (id: OverseasMarketId) => void;
  /** 爆款 / 出海：模型行旁画幅与清晰度 */
  showVideoOptions?: boolean;
  aspectRatio?: ViralAspectRatio;
  clarity?: ViralClarity;
  aspectMenuOpen?: boolean;
  setAspectMenuOpen?: (fn: (v: boolean) => boolean) => void;
  clarityMenuOpen?: boolean;
  setClarityMenuOpen?: (fn: (v: boolean) => boolean) => void;
  onSelectAspect?: (v: ViralAspectRatio) => void;
  onSelectClarity?: (v: ViralClarity) => void;
  sending: boolean;
  canSend: boolean;
  /** 处理中时发送钮变为停止 */
  canStop?: boolean;
  stopping?: boolean;
  onStop?: () => void;
  onSend: () => void;
  onIgnore?: () => void;
  showIgnore?: boolean;
  onKeySubmit?: () => void;
  /** 一次对话算力展示（轨 S） */
  turnCreditLabel?: string | null;
}) {
  // 工具图标按钮（附件 / Skill / 模式）
  const toolBtn =
    "flex size-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-black/5 hover:text-foreground disabled:opacity-40 dark:hover:bg-white/10";
  // 参数胶囊：模型 / 市场 / 画幅 / 清晰度，独立一行避免挤成一团
  const chipBtn =
    "inline-flex max-w-full items-center gap-1 rounded-full border border-border-default/70 bg-bg-surface/90 px-2.5 py-1 text-[11px] font-medium text-foreground/90 shadow-sm transition-colors hover:border-violet-500/35 hover:bg-violet-500/10 disabled:opacity-50 dark:border-white/12 dark:bg-white/[0.06]";

  const closeOtherMenus = (
    keep: "skill" | "gen" | "model" | "market" | "aspect" | "clarity" | "media"
  ) => {
    if (keep !== "skill") props.setSkillMenuOpen(() => false);
    if (keep !== "gen") props.setGenMenuOpen(() => false);
    if (keep !== "model") props.setModelMenuOpen(() => false);
    if (keep !== "market") props.setMarketMenuOpen?.(() => false);
    if (keep !== "aspect") props.setAspectMenuOpen?.(() => false);
    if (keep !== "clarity") props.setClarityMenuOpen?.(() => false);
    if (keep !== "media") props.setMediaModelMenuOpen?.(() => false);
  };

  const showSkillHint = props.skillLabel !== "Skill";
  const showModeHint = props.genMode !== "smart";
  const showCanvasMediaModel = props.genMode === "canvas";
  const mediaAnchorRef = useRef<HTMLDivElement>(null);
  const marketButtonRef = useRef<HTMLButtonElement>(null);
  const aspectButtonRef = useRef<HTMLButtonElement>(null);
  const clarityButtonRef = useRef<HTMLButtonElement>(null);

  return (
    <div className="rounded-2xl border border-border-default/80 bg-black/[0.03] px-3 pt-2.5 pb-2.5 dark:border-white/10 dark:bg-white/[0.04]">
      {props.pendingAttaches.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {props.pendingAttaches.map((a) => (
            <span
              key={a.localId}
              className="inline-flex max-w-[160px] items-center gap-1 rounded-lg border border-border-default/60 bg-bg-surface/60 px-1.5 py-0.5 text-[10px]"
            >
              {a.previewUrl ? (
                a.category === "video" ? (
                  <video
                    src={a.previewUrl}
                    className="size-4 rounded object-cover"
                    muted
                    playsInline
                    preload="metadata"
                  />
                ) : a.category === "audio" ? (
                  <span className="flex size-4 items-center justify-center text-[9px] opacity-70">
                    ♪
                  </span>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={a.previewUrl} alt="" className="size-4 rounded object-cover" />
                )
              ) : null}
              <span className="truncate">
                {a.uploading
                  ? "上传中…"
                  : a.category === "video"
                    ? `🎬 ${a.name}`
                    : a.category === "audio"
                      ? `♪ ${a.name}`
                      : a.name}
              </span>
              <button
                type="button"
                className="opacity-50 hover:opacity-100"
                onClick={() => props.onRemoveAttach(a.localId)}
              >
                <X size={10} />
              </button>
            </span>
          ))}
        </div>
      ) : null}

      {/* LibTV 风格：画布点选加入的节点引用整块 + 正文 */}
      <div className="min-h-[4rem]">
        <AgentNodeRefChipRow
          refs={props.nodeRefs}
          onRemove={props.onRemoveNodeRef}
          onFocus={props.onFocusNodeRef}
        />
        <textarea
          value={props.draft}
          onChange={(e) => props.setDraft(e.target.value)}
          placeholder={
            props.nodeRefs.length
              ? "继续描述要对已引用节点做的操作…"
              : props.placeholder
          }
          rows={3}
          disabled={props.disabled}
          className="min-h-[64px] w-full resize-none bg-transparent px-0.5 py-1 text-[12px] leading-relaxed outline-none placeholder:text-muted-foreground/70 disabled:opacity-50"
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              props.onKeySubmit?.();
            }
          }}
        />
      </div>

      {/* 参数行：市场 · 画幅 · 清晰度（菜单 portal，避免父级 overflow 裁切） */}
      {props.showOverseasMarket || props.showVideoOptions ? (
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {props.showOverseasMarket ? (
          <div className="relative shrink-0" data-creation-skill-param>
            <button
              ref={marketButtonRef}
              type="button"
              className={chipBtn}
              title="选择出海国家版本"
              disabled={props.disabled}
              aria-expanded={Boolean(props.marketMenuOpen)}
              onClick={() => {
                props.setMarketMenuOpen?.((v) => !v);
                closeOtherMenus("market");
              }}
            >
              <Globe2 size={12} className="shrink-0 opacity-70" />
              <span className="max-w-[5.5rem] truncate">{props.overseasMarketLabel || "美国"}</span>
              <ChevronDown size={11} className="shrink-0 opacity-50" />
            </button>
            <FixedAboveMenu
              open={Boolean(props.marketMenuOpen)}
              anchorEl={marketButtonRef.current}
              minWidth={168}
              onRequestClose={() => props.setMarketMenuOpen?.(() => false)}
            >
              {OVERSEAS_MARKETS.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={cn(
                    "block w-full px-3 py-1.5 text-left text-[11px] text-white/80 hover:bg-white/10",
                    m.id === props.overseasMarketId && "text-violet-300"
                  )}
                  onClick={() => props.onSelectMarket?.(m.id)}
                >
                  <div className="font-medium">{m.label}</div>
                  <div className="text-[10px] text-white/40">{m.language}</div>
                </button>
              ))}
            </FixedAboveMenu>
          </div>
        ) : null}

        {props.showVideoOptions ? (
          <>
            <div className="relative shrink-0" data-creation-skill-param>
              <button
                ref={aspectButtonRef}
                type="button"
                className={chipBtn}
                title="视频比例"
                disabled={props.disabled}
                aria-expanded={Boolean(props.aspectMenuOpen)}
                onClick={() => {
                  props.setAspectMenuOpen?.((v) => !v);
                  closeOtherMenus("aspect");
                }}
              >
                <span>{props.aspectRatio || "9:16"}</span>
                <ChevronDown size={11} className="shrink-0 opacity-50" />
              </button>
              <FixedAboveMenu
                open={Boolean(props.aspectMenuOpen)}
                anchorEl={aspectButtonRef.current}
                minWidth={96}
                onRequestClose={() => props.setAspectMenuOpen?.(() => false)}
              >
                {VIRAL_ASPECT_OPTIONS.map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    className={cn(
                      "block w-full px-3 py-1.5 text-left text-[11px] text-white/80 hover:bg-white/10",
                      opt === props.aspectRatio && "text-violet-300"
                    )}
                    onClick={() => props.onSelectAspect?.(opt)}
                  >
                    {opt}
                  </button>
                ))}
              </FixedAboveMenu>
            </div>
            <div className="relative shrink-0" data-creation-skill-param>
              <button
                ref={clarityButtonRef}
                type="button"
                className={chipBtn}
                title="清晰度"
                disabled={props.disabled}
                aria-expanded={Boolean(props.clarityMenuOpen)}
                onClick={() => {
                  props.setClarityMenuOpen?.((v) => !v);
                  closeOtherMenus("clarity");
                }}
              >
                <span>{props.clarity || "1080p"}</span>
                <ChevronDown size={11} className="shrink-0 opacity-50" />
              </button>
              <FixedAboveMenu
                open={Boolean(props.clarityMenuOpen)}
                anchorEl={clarityButtonRef.current}
                minWidth={96}
                onRequestClose={() => props.setClarityMenuOpen?.(() => false)}
              >
                {VIRAL_CLARITY_OPTIONS.map((opt) => (
                  <button
                    key={opt}
                    type="button"
                    className={cn(
                      "block w-full px-3 py-1.5 text-left text-[11px] text-white/80 hover:bg-white/10",
                      opt === props.clarity && "text-violet-300"
                    )}
                    onClick={() => props.onSelectClarity?.(opt)}
                  >
                    {opt}
                  </button>
                ))}
              </FixedAboveMenu>
            </div>
          </>
        ) : null}
      </div>
      ) : null}

      {/* 操作行：附件 / Skill / 模式 ····· 忽略 / 发送 */}
      <div className="mt-2 flex items-center gap-0.5 border-t border-border-default/50 pt-2 dark:border-white/10">
        {props.modelSupportsFiles ? (
          <button
            type="button"
            className={toolBtn}
            aria-label="添加图片/视频/音频"
            title="添加图片、视频或音频"
            disabled={props.disabled}
            onClick={props.onPickFiles}
          >
            <Paperclip size={15} />
          </button>
        ) : null}

        <div className="relative">
          <button
            type="button"
            className={cn(toolBtn, showSkillHint && "text-violet-600 dark:text-violet-300")}
            aria-label="Skill"
            title={showSkillHint ? `Skill：${props.skillLabel}` : "选择 Skill"}
            disabled={props.disabled}
            onClick={() => {
              props.setSkillMenuOpen((v) => !v);
              closeOtherMenus("skill");
            }}
          >
            <Zap size={15} />
          </button>
          {props.skillMenuOpen ? (
            <div className="absolute bottom-full left-0 z-50 mb-1.5 max-h-56 w-60 overflow-auto rounded-xl border border-border-default bg-bg-surface py-1 shadow-lg dark:border-[#363636] dark:bg-[#222]">
              <button
                type="button"
                className={cn(
                  "block w-full px-3 py-1.5 text-left text-[11px] hover:bg-black/5 dark:hover:bg-white/10",
                  !props.selectedSkillSlug && "text-violet-600 dark:text-violet-300"
                )}
                onClick={() => props.onSelectSkill(null)}
              >
                不使用 Skill
              </button>
              {props.skillOptions.map((s) => (
                <button
                  key={s.slug}
                  type="button"
                  className={cn(
                    "block w-full px-3 py-1.5 text-left text-[11px] hover:bg-black/5 dark:hover:bg-white/10",
                    s.slug === props.selectedSkillSlug && "text-violet-600 dark:text-violet-300"
                  )}
                  onClick={() => props.onSelectSkill(s)}
                >
                  <div className="font-medium">{s.title}</div>
                  <div className="line-clamp-2 text-[10px] text-muted-foreground">{s.description}</div>
                </button>
              ))}
              {!props.skillOptions.length ? (
                <div className="px-3 py-2 text-[10px] text-muted-foreground">暂无 Skill</div>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="relative">
          <button
            type="button"
            className={cn(toolBtn, showModeHint && "text-violet-600 dark:text-violet-300")}
            aria-label="生成模式"
            title={`生成模式：${props.genModeLabel}`}
            disabled={props.disabled}
            onClick={() => {
              props.setGenMenuOpen((v) => !v);
              closeOtherMenus("gen");
            }}
          >
            <Layers size={15} />
          </button>
          {props.genMenuOpen ? (
            <div className="absolute bottom-full left-0 z-50 mb-1.5 max-h-56 min-w-[188px] overflow-y-auto rounded-xl border border-border-default bg-bg-surface py-1 shadow-lg dark:border-[#363636] dark:bg-[#222]">
              {GEN_MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={cn(
                    "block w-full px-3 py-1.5 text-left text-[11px] hover:bg-black/5 dark:hover:bg-white/10",
                    m.id === props.genMode && "text-violet-600 dark:text-violet-300"
                  )}
                  onClick={() => props.onSelectGenMode(m.id)}
                >
                  <div className="font-medium">{m.label}</div>
                  <div className="text-[10px] text-muted-foreground">{m.hint}</div>
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {(showSkillHint || showModeHint || showCanvasMediaModel) && (
          <div className="relative ml-1 flex min-w-0 flex-1 items-center gap-1">
            <div className="min-w-0 truncate text-[10px] text-muted-foreground">
              {showSkillHint ? props.skillLabel : null}
              {showSkillHint && showModeHint ? " · " : null}
              {showModeHint ? props.genModeLabel : null}
            </div>
            {showCanvasMediaModel ? (
              <>
                <AgentCanvasMediaModelTrigger
                  open={Boolean(props.mediaModelMenuOpen)}
                  activeTab={props.mediaModelTab || "image"}
                  disabled={props.disabled}
                  imageLabel={props.mediaImageLabel || "生图"}
                  videoLabel={props.mediaVideoLabel || "生视频"}
                  triggerRef={mediaAnchorRef}
                  onOpenTab={(tab) => {
                    const already =
                      props.mediaModelMenuOpen && (props.mediaModelTab || "image") === tab;
                    props.setMediaModelTab?.(tab);
                    if (already) {
                      props.setMediaModelMenuOpen?.(() => false);
                    } else {
                      props.setMediaModelMenuOpen?.(() => true);
                      closeOtherMenus("media");
                    }
                  }}
                />
                <AgentCanvasMediaModelMenu
                  open={Boolean(props.mediaModelMenuOpen)}
                  disabled={props.disabled}
                  initialTab={props.mediaModelTab || "image"}
                  anchorRef={mediaAnchorRef}
                  onOpenChange={(open) => props.setMediaModelMenuOpen?.(() => open)}
                  onTabChange={(tab) => props.setMediaModelTab?.(tab)}
                />
              </>
            ) : null}
          </div>
        )}
        {!showSkillHint && !showModeHint && !showCanvasMediaModel ? <div className="flex-1" /> : null}

        {props.turnCreditLabel ? (
          <span
            className="mr-1 shrink-0 text-[10px] tabular-nums text-muted-foreground"
            title="发送一条对话将按此算力预扣（生图/生视频另计）"
          >
            {props.turnCreditLabel}
          </span>
        ) : null}

        {props.showIgnore ? (
          <button
            type="button"
            className="shrink-0 rounded-lg px-2.5 py-1.5 text-[11px] text-muted-foreground hover:bg-black/5 dark:hover:bg-white/10"
            disabled={props.sending}
            onClick={props.onIgnore}
          >
            忽略
          </button>
        ) : null}
        <button
          type="button"
          className={cn(
            "flex size-9 shrink-0 items-center justify-center rounded-xl hover:opacity-90 disabled:opacity-40",
            props.canStop && props.onStop
              ? "bg-red-600 text-white hover:bg-red-500"
              : "bg-foreground text-background"
          )}
          disabled={
            props.canStop && props.onStop
              ? Boolean(props.stopping)
              : props.sending || !props.canSend
          }
          aria-label={props.canStop && props.onStop ? "停止" : "发送"}
          title={props.canStop && props.onStop ? "停止助手" : "发送"}
          onClick={() => {
            if (props.canStop && props.onStop) {
              props.onStop();
              return;
            }
            props.onSend();
          }}
        >
          {props.stopping ? (
            <Loader2 size={15} className="animate-spin" />
          ) : props.canStop && props.onStop ? (
            <Square size={14} fill="currentColor" />
          ) : props.sending ? (
            <Loader2 size={15} className="animate-spin" />
          ) : (
            <Send size={15} />
          )}
        </button>
      </div>
    </div>
  );
}

export function AgentSessionPanel({ projectId }: Props) {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const sessionId = searchParams.get("session") || "";
  const queryClient = useQueryClient();

  // 有 session 时默认展开；无 session 时仅显示 FAB
  const [open, setOpen] = useState(Boolean(sessionId));
  const [applyingRecipe, setApplyingRecipe] = useState(false);
  const [recipeForceHint, setRecipeForceHint] = useState(false);
  const [askedOpen, setAskedOpen] = useState(true);
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  // 用户已作答时立刻藏询问条，不等接口把 pending 改成 answered
  const [dismissedAskMessageId, setDismissedAskMessageId] = useState<string | null>(null);
  // 爆款等待上传：画幅 / 清晰度（默认竖屏 9:16 / 1080p）
  const [viralAspect, setViralAspect] = useState<ViralAspectRatio>("9:16");
  const [viralClarity, setViralClarity] = useState<ViralClarity>("1080p");
  const [overseasMarketId, setOverseasMarketId] = useState<OverseasMarketId>(readStoredOverseasMarket);
  const [marketMenuOpen, setMarketMenuOpen] = useState(false);
  const [aspectMenuOpen, setAspectMenuOpen] = useState(false);
  const [clarityMenuOpen, setClarityMenuOpen] = useState(false);
  const [controllerModel, setControllerModel] = useState(readStoredModel);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const [genMode, setGenMode] = useState<GenModeId>(readStoredGenMode);
  const [genMenuOpen, setGenMenuOpen] = useState(false);
  const [mediaModelMenuOpen, setMediaModelMenuOpen] = useState(false);
  const [mediaModelTab, setMediaModelTab] = useState<"image" | "video">("image");
  const [mediaPrefs, setMediaPrefs] = useState(() => hydrateAgentCanvasMediaModels());
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<SkillItem | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [pendingAttaches, setPendingAttaches] = useState<PendingAttach[]>([]);
  const [fabPos, setFabPos] = useState<FabPos>(defaultFabPos);
  const [dragging, setDragging] = useState(false);
  /** 已引用的画布节点整块（缩略图 + 名称）；仅通过画布点选加入 */
  const [nodeRefs, setNodeRefs] = useState<AgentNodeRef[]>([]);
  /** 准星：点选画布节点后加入引用整块（不提供列表选择） */
  const [pickNodeMode, setPickNodeMode] = useState(false);
  const pickBaselineRef = useRef<string | null>(null);
  const projectingRef = useRef(false);
  const canvasKickRef = useRef<string | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const user = useAuthStore((s) => s.user);
  const selectedNodeId = useCanvasStore((s) => s.selectedNodeId);
  const { lookupMap } = useProjectAssetManifest(projectId);
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    moved: boolean;
  } | null>(null);

  // 客户端恢复上次拖动位置
  useEffect(() => {
    setFabPos(readStoredFabPos());
    setMediaPrefs(hydrateAgentCanvasMediaModels());
  }, []);

  useEffect(() => subscribeAgentCanvasMediaModels(() => setMediaPrefs(getAgentCanvasMediaModels())), []);

  // 窗口变化时把按钮夹回可视区
  useEffect(() => {
    const onResize = () => setFabPos((p) => clampFabPos(p));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const mediaImageModelsQuery = useQuery({
    queryKey: ["models", "agent-canvas-labels", "image"],
    queryFn: () => listModels({ category: "image" }),
    staleTime: 60_000,
    enabled: open && genMode === "canvas",
  });
  const mediaVideoModelsQuery = useQuery({
    queryKey: ["models", "agent-canvas-labels", "video"],
    queryFn: () => listModels({ category: "video" }),
    staleTime: 60_000,
    enabled: open && genMode === "canvas",
  });
  const mediaImageLabel = useMemo(() => {
    const opts = buildModelOptions(mediaImageModelsQuery.data || [], ["image"], []);
    return opts.find((o) => o.value === mediaPrefs.imageModel)?.label || mediaPrefs.imageModel || "生图";
  }, [mediaImageModelsQuery.data, mediaPrefs.imageModel]);
  const mediaVideoLabel = useMemo(() => {
    const opts = buildModelOptions(mediaVideoModelsQuery.data || [], ["video"], []);
    return opts.find((o) => o.value === mediaPrefs.videoModel)?.label || mediaPrefs.videoModel || "生视频";
  }, [mediaVideoModelsQuery.data, mediaPrefs.videoModel]);

  const query = useQuery({
    queryKey: ["agent-session", sessionId],
    queryFn: () => getAgentSession(sessionId),
    enabled: Boolean(sessionId),
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      const ops = q.state.data?.graph?.canvasOps;
      if (Array.isArray(ops) && ops.length > 0) return 3000;
      if (status === "awaiting_user") return 4000;
      if (status === "completed" || status === "failed" || status === "cancelled") return false;
      return 3000;
    },
  });

  // SSE：思考中拉 runtimeEvents，加速状态条；失败则仍靠轮询
  const [sseHint, setSseHint] = useState<AgentRuntimeSseEvent | null>(null);
  useEffect(() => {
    if (!sessionId || !open) {
      setSseHint(null);
      return;
    }
    const status = query.data?.status;
    if (
      status === "awaiting_user" ||
      status === "completed" ||
      status === "failed" ||
      status === "cancelled"
    ) {
      return;
    }
    // 无 status 时也尝试订阅（刚创建会话）
    let lastId = 0;
    const events = query.data?.runtimeEvents;
    if (Array.isArray(events) && events.length) {
      lastId = Math.max(0, ...events.map((e) => Number(e.id || 0)));
    }
    return subscribeAgentSessionEvents(
      sessionId,
      {
        onEvent: (ev) => {
          setSseHint(ev);
          void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
        },
        onDone: () => {
          void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
        },
      },
      { afterId: lastId }
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, open, query.data?.status]);

  const skillsQuery = useQuery({
    queryKey: ["skills", "catalog"],
    queryFn: () => listSkills(),
    staleTime: 60_000,
    enabled: open,
  });
  const mineQuery = useQuery({
    queryKey: ["skills", "mine"],
    queryFn: listMySkills,
    staleTime: 30_000,
    enabled: open && Boolean(user),
  });
  const historyQuery = useQuery({
    queryKey: ["agent-sessions", projectId],
    queryFn: () => listProjectAgentSessions(projectId),
    enabled: open && historyOpen && Boolean(projectId),
    staleTime: 15_000,
  });
  // 无会话时也拉全量控制器模型（GPT / Gemini / Claude 等）
  const modelsQuery = useQuery({
    queryKey: ["agent-controller-models"],
    queryFn: listControllerModels,
    staleTime: 60_000,
    enabled: open || Boolean(sessionId),
  });
  // 一次对话算力（后台「AI 操控设置」）
  const agentPricingQuery = useQuery({
    queryKey: ["credits", "agent-skill-pricing", selectedSkill?.slug ?? ""],
    queryFn: () => getAgentSkillPricing(selectedSkill?.slug),
    staleTime: 30_000,
    enabled: open && Boolean(user),
  });
  const turnCreditLabel = useMemo(() => {
    const q = agentPricingQuery.data;
    if (!q?.creditsEnabled) return null;
    const total = q.quote?.total ?? q.conversationTurn ?? q.sessionStart ?? 0;
    if (total <= 0) return null;
    return formatCreditLabel(total, true);
  }, [agentPricingQuery.data]);

  const skillOptions = useMemo(() => {
    const pub = skillsQuery.data?.items ?? [];
    const mine = mineQuery.data ?? [];
    const seen = new Set(pub.map((s) => s.slug));
    return [...pub, ...mine.filter((s) => !seen.has(s.slug))];
  }, [skillsQuery.data?.items, mineQuery.data]);

  /** 每个会话只从 referenceAssetIds / 出海·爆款 session 回填一次附件芯片 */
  const hydratedAttachesRef = useRef<string | null>(null);
  /** 投影/忙碌时短排队（最多 2 条），ack 后自动发出 */
  type QueuedAgentMsg = {
    message: string;
    ignore?: boolean;
    choiceId?: string;
  };
  const sendQueueRef = useRef<QueuedAgentMsg[]>([]);
  const [queueLen, setQueueLen] = useState(0);
  const flushingQueueRef = useRef(false);

  useEffect(() => {
    if (sessionId) setOpen(true);
    setSelectedChoice(null);
    setDraft("");
    setNodeRefs([]);
    setPendingAttaches([]);
    setPickNodeMode(false);
    pickBaselineRef.current = null;
    hydratedAttachesRef.current = null;
  }, [sessionId]);

  // 发现页创作框文案 → Agent Composer 输入框（一次）
  useEffect(() => {
    if (!sessionId) return;
    try {
      const pre = sessionStorage.getItem("jumeng:prefill_agent_draft");
      if (!pre) return;
      sessionStorage.removeItem("jumeng:prefill_agent_draft");
      const text = pre.trim();
      if (text) {
        setDraft(text);
        setOpen(true);
      }
    } catch {
      /* ignore */
    }
  }, [sessionId]);

  useEffect(() => {
    const mid = query.data?.controllerModel;
    if (mid) setControllerModel(mid);
  }, [query.data?.controllerModel]);

  // 创作框进画布：把已绑定参考（含视频）回显到 Agent Composer，样式同本地上传芯片
  useEffect(() => {
    if (!sessionId || !projectId || !query.isSuccess) return;
    if (hydratedAttachesRef.current === sessionId) return;

    const fromApi = Array.isArray(query.data?.referenceAssetIds)
      ? query.data!.referenceAssetIds!.map(String).filter(Boolean)
      : [];
    const overseas = loadOverseasSession(projectId);
    const viral = loadViralRemakeSession(projectId);
    const ids: string[] = [];
    const seen = new Set<string>();
    for (const aid of [
      ...fromApi,
      overseas?.videoAssetId,
      viral?.videoAssetId,
      ...(viral?.replaceImageAssetIds || []),
    ]) {
      const id = String(aid || "").trim();
      if (!id || seen.has(id)) continue;
      seen.add(id);
      ids.push(id);
      if (ids.length >= 8) break;
    }

    hydratedAttachesRef.current = sessionId;
    if (!ids.length) return;

    let cancelled = false;
    void (async () => {
      const next: PendingAttach[] = [];
      for (const aid of ids) {
        try {
          const asset = await fetchAssetById(projectId, aid);
          if (!asset?.id || cancelled) continue;
          const cat =
            asset.category === "video"
              ? "video"
              : asset.category === "audio"
                ? "audio"
                : asset.category === "image"
                  ? "image"
                  : undefined;
          if (!cat) continue;
          const url =
            ensureHttpsOssUrl(asset.thumbnailUrl || asset.fileUrl) ||
            asset.fileUrl;
          next.push({
            localId: `seed_${aid}`,
            name:
              asset.title ||
              (cat === "video" ? "参考视频" : cat === "audio" ? "参考音频" : "参考图"),
            category: cat,
            assetId: aid,
            previewUrl: url || undefined,
          });
        } catch {
          /* 单条失败跳过 */
        }
      }
      if (!cancelled && next.length) {
        setPendingAttaches((prev) => (prev.length ? prev : next));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [
    sessionId,
    projectId,
    query.isSuccess,
    query.data?.referenceAssetIds,
  ]);

  // 画布 init 完成后再投影：避免 isLoading 时跳过导致 canvasOps 永久挂起、发送被拦
  const canvasIsLoading = useCanvasStore((s) => s.isLoading);

  useEffect(() => {
    const graph = query.data?.graph;
    const ops = graph?.canvasOps;
    const revision = graph?.revision;
    if (!projectId || !ops?.length || !revision) return;
    if (String(query.data?.projectId) !== String(projectId)) return;
    if (canvasIsLoading) return;
    if (projectingRef.current) return;
    projectingRef.current = true;
    void projectAgentCanvasOps({
      projectId,
      revision,
      ops,
      sessionId: sessionId || undefined,
      queryClient,
    })
      .then((ok) => {
        if (ok && sessionId) {
          void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
        }
      })
      .catch(() => {})
      .finally(() => {
        projectingRef.current = false;
      });
  }, [
    projectId,
    sessionId,
    canvasIsLoading,
    query.data?.graph,
    query.data?.projectId,
    queryClient,
  ]);

  // 首页建会话时没有画布快照：进画布后带最新状态 kick 思考
  useEffect(() => {
    if (!sessionId || canvasIsLoading) return;
    if (!query.data?.needsCanvasKick) return;
    if (canvasKickRef.current === sessionId) return;
    canvasKickRef.current = sessionId;
    void postAgentSessionMessage(sessionId, {
      message: "",
      action: "resume",
      canvasSnapshot: takeFreshCanvasSnapshot(),
    })
      .then(() => {
        void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
      })
      .catch(() => {
        canvasKickRef.current = null;
      });
  }, [
    sessionId,
    canvasIsLoading,
    query.data?.needsCanvasKick,
    queryClient,
  ]);

  // 自由创作 Agent Team 路径：会话每次返回最新 graph 时都同步进组件外缓存，
  // 供 applyMediaJobResult.ts 的单镜 reflow 钩子在组件树之外也能读到 shots[]。
  useEffect(() => {
    const graph = query.data?.graph;
    if (!projectId || !graph) return;
    if (String(query.data?.projectId) !== String(projectId)) return;
    setAgentProjectGraphSnapshot(projectId, graph);
  }, [projectId, query.data?.graph, query.data?.projectId]);

  // 时间线完成度（N/M 镜）：随缓存更新自动刷新，对齐分镜表路径的时长提示
  const agentTimelineSummary = useAgentTimelineSummary(projectId);

  // 旁白/素材消息写入后立刻刷新聊天列表
  useEffect(() => {
    if (!sessionId) return;
    const onBump = (ev: Event) => {
      const detail = (ev as CustomEvent<{ sessionId?: string }>).detail;
      if (detail?.sessionId && detail.sessionId !== sessionId) return;
      void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
    };
    window.addEventListener("agent-session-invalidate", onBump);
    return () => window.removeEventListener("agent-session-invalidate", onBump);
  }, [sessionId, queryClient]);

  // 准星模式：用户新点选画布节点后，加入 LibTV 风格引用整块（缩略图 + 名称）
  useEffect(() => {
    if (!pickNodeMode || !selectedNodeId) return;
    if (selectedNodeId === pickBaselineRef.current) return;
    const store = useCanvasStore.getState();
    const node = store.nodes.find((n) => n.id === selectedNodeId);
    if (!node) return;
    const ref = buildAgentNodeRef(node, (id) => {
      const a = lookupMap?.get(id);
      if (!a) return undefined;
      return { fileUrl: a.fileUrl, thumbnailUrl: a.thumbnailUrl };
    });
    setNodeRefs((prev) => {
      if (prev.some((r) => r.nodeId === ref.nodeId)) return prev;
      return [...prev, ref];
    });
    // 保持准星开启，便于连续点选多个节点；baseline 更新避免同节点重复触发
    pickBaselineRef.current = selectedNodeId;
    toast.message(`已引用「${ref.label}」`);
  }, [lookupMap, pickNodeMode, selectedNodeId]);

  /** 点击引用芯片：选中并定位到画布节点 */
  const focusCanvasNode = (nodeId: string) => {
    const store = useCanvasStore.getState();
    store.setCanvasMode("pointer");
    store.selectNode(nodeId);
    const node = store.nodes.find((n) => n.id === nodeId);
    if (!node || !store.centerFlowView) return;
    const world = getNodeWorldPosition(node, store.nodes);
    const cx = world.x + (node.width || 240) / 2;
    const cy = world.y + (node.height || 160) / 2;
    store.centerFlowView(cx, cy);
  };

  /** 开启画布点选引用（强制指针模式，清空选中以便再次点同一节点） */
  const startPickNode = () => {
    const store = useCanvasStore.getState();
    store.setCanvasMode("pointer");
    pickBaselineRef.current = null;
    store.selectNode(null);
    setPickNodeMode(true);
    toast.message("请点击画布上要引用的节点");
  };

  const messages: AgentMessage[] = useMemo(() => query.data?.messages ?? [], [query.data?.messages]);
  const askedItems = useMemo(() => getAskedSummary(messages), [messages]);
  const pendingAsk = useMemo(() => getPendingAsk(messages), [messages]);
  // 已点选/发送后立刻收起询问条；新的 pending 问句（不同 messageId）仍会显示
  const showPendingAsk = Boolean(
    pendingAsk && pendingAsk.messageId !== dismissedAskMessageId
  );
  const modelOptions = query.data?.controllerModels?.length
    ? query.data.controllerModels
    : modelsQuery.data?.items?.length
      ? modelsQuery.data.items
      : DEFAULT_CONTROLLER_MODELS;
  const modelLabel =
    modelOptions.find((m) => m.id === controllerModel)?.label || controllerModel;
  const modelSupportsFiles = Boolean(
    (modelOptions.find((m) => m.id === DEFAULT_CONTROLLER_MODEL_ID) as ControllerModelOption | undefined)
      ?.supportsFiles ??
      DEFAULT_CONTROLLER_MODELS.find((m) => m.id === DEFAULT_CONTROLLER_MODEL_ID)?.supportsFiles
  );
  // 爆款复刻 / 一键出海：不依赖控制器模型 supportsFiles，始终允许上传参考视频
  const sessionIsViral =
    query.data?.skillSlug === VIRAL_REMAKE_SKILL || selectedSkill?.slug === VIRAL_REMAKE_SKILL;
  const sessionIsOverseas =
    query.data?.skillSlug === OVERSEAS_LOCALIZE_SKILL ||
    selectedSkill?.slug === OVERSEAS_LOCALIZE_SKILL ||
    isOverseasSkill(selectedSkill);
  const showOverseasMarket = sessionIsOverseas || isOverseasSkill(selectedSkill);
  const overseasMarketLabel = getOverseasMarket(overseasMarketId).label;
  const allowFileAttach =
    modelSupportsFiles || isWizardSkill(selectedSkill) || sessionIsViral || sessionIsOverseas;
  const genModeLabel = GEN_MODES.find((m) => m.id === genMode)?.label || "智能编排";

  // 会话绑定 Skill 时同步标签（仅随 sessionId / skillSlug 变化，避免挡住用户主动切换）
  useEffect(() => {
    if (!sessionId) return;
    const slug = query.data?.skillSlug;
    if (!slug) return;
    const hit = skillOptions.find((s) => s.slug === slug);
    if (hit) setSelectedSkill(hit);
  }, [sessionId, query.data?.skillSlug, skillOptions]);

  useEffect(() => {
    setDismissedAskMessageId(null);
    setSelectedChoice(null);
  }, [sessionId]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, pendingAsk?.messageId, open]);

  const bindSessionToUrl = (sid: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("session", sid);
    router.replace(`${pathname}?${params.toString()}`);
  };

  const clearSessionFromUrl = () => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("session");
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname);
  };

  const persistModel = (mid: string) => {
    setControllerModel(mid);
    try {
      localStorage.setItem(MODEL_STORAGE_KEY, mid);
    } catch {
      /* ignore */
    }
    const supports =
      DEFAULT_CONTROLLER_MODELS.find((m) => m.id === mid)?.supportsFiles ??
      (modelOptions.find((m) => m.id === mid) as ControllerModelOption | undefined)?.supportsFiles;
    // 爆款复刻 / 一键出海会话保留视频附件
    if (!supports && !sessionIsViral && !sessionIsOverseas && !isWizardSkill(selectedSkill)) {
      setPendingAttaches([]);
    }
  };

  const persistGenMode = (mode: GenModeId) => {
    setGenMode(mode);
    try {
      localStorage.setItem(GEN_MODE_KEY, mode);
    } catch {
      /* ignore */
    }
  };

  const persistOverseasMarket = (id: OverseasMarketId) => {
    setOverseasMarketId(id);
    try {
      localStorage.setItem(OVERSEAS_MARKET_KEY, id);
    } catch {
      /* ignore */
    }
  };

  const persistViralAspect = (v: ViralAspectRatio) => {
    setViralAspect(v);
    setAspectMenuOpen(false);
  };
  const persistViralClarity = (v: ViralClarity) => {
    setViralClarity(v);
    setClarityMenuOpen(false);
  };

  const closeMenus = () => {
    setModelMenuOpen(false);
    setGenMenuOpen(false);
    setMediaModelMenuOpen(false);
    setSkillMenuOpen(false);
    setMarketMenuOpen(false);
    setAspectMenuOpen(false);
    setClarityMenuOpen(false);
    setHistoryOpen(false);
  };

  const handleNewChat = () => {
    requestAgentCanvasStop();
    clearAgentCanvasBusy();
    sendQueueRef.current = [];
    setQueueLen(0);
    clearSessionFromUrl();
    setDraft("");
    setSelectedChoice(null);
    setPendingAttaches([]);
    setNodeRefs([]);
    setPickNodeMode(false);
    pickBaselineRef.current = null;
    setSelectedSkill(null);
    setOpen(true);
    closeMenus();
    toast.message("已新建对话");
  };

  const handleSelectSkill = async (s: SkillItem | null) => {
    setSkillMenuOpen(false);
    const nextSlug = s?.slug || "";
    const sessionSlug = query.data?.skillSlug || "";

    // 一键出海：清理遗留 Skill 卡，操作走对话（不落画布节点）
    if (s && isOverseasSkill(s)) {
      ensureOverseasLocalizeNode({
        targetMarketId: overseasMarketId,
        markSkillQuery: true,
      });
    }

    // 有会话且换 Skill：脱离旧会话再开新一轮
    const switching = Boolean(sessionId) && sessionSlug !== nextSlug;
    if (switching) {
      clearSessionFromUrl();
      setDraft("");
      setSelectedChoice(null);
      setPendingAttaches([]);
    }

    setSelectedSkill(s);

    // 已在同一 Skill 会话中：无需重建
    if (sessionId && !switching && sessionSlug === nextSlug) {
      return;
    }

    // 爆款 / 出海：新建会话
    if (s && isWizardSkill(s)) {
      setSending(true);
      try {
        const created = await createAgentSession({
          message: `启动 Skill：${s.title}`,
          projectId,
          skillSlug: s.slug,
          controllerModel: DEFAULT_CONTROLLER_MODEL_ID,
          genMode,
          aspectRatio: viralAspect,
          clarity: viralClarity,
          targetMarketId: isOverseasSkill(s) ? overseasMarketId : undefined,
        });
        bindSessionToUrl(created.sessionId);
        void queryClient.invalidateQueries({ queryKey: ["agent-sessions", projectId] });
        toast.message(
          isOverseasSkill(s)
            ? `已切换「一键出海」· ${getOverseasMarket(overseasMarketId).label}，可上传参考视频`
            : switching
              ? "已切换「爆款拉片复刻」，请上传参考视频"
              : "已加载爆款复刻 Skill，请上传参考视频"
        );
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "无法加载 Skill");
      } finally {
        setSending(false);
      }
      return;
    }

    if (s) {
      toast.message(`已选择 Skill「${s.title}」`);
    } else if (switching) {
      toast.message("已取消 Skill");
    }
  };

  const handlePickFiles = async (files: FileList | null) => {
    if (!files?.length || !allowFileAttach) return;
    const list = Array.from(files).slice(0, 4);
    for (const file of list) {
      const localId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      // Windows 上 mp4/mov 常出现 file.type 为空或 application/octet-stream，须按扩展名回退
      const inferred = inferAssetCategory(file);
      if (inferred !== "image" && inferred !== "video" && inferred !== "audio") {
        toast.error(`暂不支持：${file.name}（请上传图片、视频或音频）`);
        continue;
      }
      const category = inferred;
      const previewUrl = URL.createObjectURL(file);
      setPendingAttaches((prev) => [
        ...prev,
        { localId, name: file.name, category, previewUrl, uploading: true },
      ]);
      try {
        const asset = await uploadAsset({
          file,
          projectId,
          category,
          title: file.name.replace(/\.[^.]+$/, "") || "附件",
        });
        const resolvedCat: PendingAttach["category"] =
          asset.category === "video"
            ? "video"
            : asset.category === "audio"
              ? "audio"
              : "image";
        setPendingAttaches((prev) =>
          prev.map((p) =>
            p.localId === localId
              ? {
                  ...p,
                  assetId: asset.id,
                  uploading: false,
                  name: asset.title || file.name,
                  category: resolvedCat,
                }
              : p
          )
        );
      } catch (err) {
        setPendingAttaches((prev) => prev.filter((p) => p.localId !== localId));
        URL.revokeObjectURL(previewUrl);
        toast.error(err instanceof Error ? err.message : "上传失败");
      }
    }
  };

  const buildMessageWithAttaches = (text: string) => {
    const ready = pendingAttaches.filter((a) => a.assetId);
    // 节点引用行在上（或紧随附件），正文在下
    const withNodes = buildMessageWithNodeRefs(text, nodeRefs);
    if (!ready.length) return withNodes;
    const lines = ready.map((a) => `[附件:${a.name}|assetId=${a.assetId}]`);
    return `${lines.join("\n")}\n${withNodes}`.trim();
  };

  const submitReply = async (opts?: {
    ignore?: boolean;
    choiceId?: string;
    fromQueue?: boolean;
    queuedMessage?: string;
    queuedChoiceId?: string;
    queuedIgnore?: boolean;
  }) => {
    if (sending && !opts?.fromQueue) return;
    if (stopping) return;
    const ignore = Boolean(opts?.ignore);
    const pickedChoice = ignore
      ? undefined
      : opts?.fromQueue
        ? opts.queuedChoiceId
        : opts?.choiceId || selectedChoice || undefined;
    if (
      !ignore &&
      !opts?.fromQueue &&
      !pickedChoice &&
      !draft.trim() &&
      !nodeRefs.length &&
      !pendingAttaches.some((a) => a.assetId)
    ) {
      toast.error("请选择选项或输入内容");
      return;
    }
    if (pendingAttaches.some((a) => a.uploading)) {
      toast.error("附件上传中，请稍候");
      return;
    }

    const enqueueInstead = (reason: "projecting" | "busy") => {
      if (!sessionId) {
        toast.message(
          reason === "projecting"
            ? "画布正在应用上一轮节点，请稍候再发首条消息"
            : "AI 正在操控画布，请稍候再开新对话"
        );
        return;
      }
      if (sendQueueRef.current.length >= 2) {
        toast.message("最多排队 2 条，请等画布应用完成后再发");
        return;
      }
      const text = buildMessageWithAttaches(draft.trim());
      if (!text && !ignore && !pickedChoice) {
        toast.error("请输入内容");
        return;
      }
      sendQueueRef.current = [
        ...sendQueueRef.current,
        {
          message: text || "继续",
          ignore,
          choiceId: ignore ? undefined : pickedChoice,
        },
      ];
      setQueueLen(sendQueueRef.current.length);
      setDraft("");
      setSelectedChoice(null);
      setPendingAttaches([]);
      setNodeRefs([]);
      if (pendingAsk?.messageId) setDismissedAskMessageId(pendingAsk.messageId);
      toast.message("已排队：画布应用完成后将自动发送");
    };

    // 上一轮 canvasOps 尚未投影完：先尝试补投影；失败则入队而非硬拒
    const pendingOps = query.data?.graph?.canvasOps;
    if (
      !opts?.fromQueue &&
      Array.isArray(pendingOps) &&
      pendingOps.length > 0 &&
      sessionId
    ) {
      const rev = Number(query.data?.graph?.revision || 0);
      if (projectId && rev > 0 && !useCanvasStore.getState().isLoading) {
        try {
          const ok = await projectAgentCanvasOps({
            projectId,
            revision: rev,
            ops: pendingOps,
            sessionId,
            queryClient,
          });
          if (ok) {
            void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
          } else {
            enqueueInstead("projecting");
            return;
          }
        } catch {
          enqueueInstead("projecting");
          return;
        }
      } else {
        enqueueInstead("projecting");
        return;
      }
    }
    // 投影/工具忙碌：入队；会话 executing（running）用 steer 追加飞行指令，避免双扣对话算力
    if (!opts?.fromQueue && getAgentCanvasBusy().busy) {
      const phase = getAgentCanvasBusy().phase;
      if (phase === "orchestrating" && sessionId) {
        const steerText = buildMessageWithAttaches(draft.trim());
        if (steerText) {
          try {
            await steerAgentSession(sessionId, steerText);
            setDraft("");
            setSelectedChoice(null);
            setPendingAttaches([]);
            setNodeRefs([]);
            toast.message("已追加飞行指令，将在思考中并入");
            void queryClient.invalidateQueries({
              queryKey: ["agent-session", sessionId],
            });
            return;
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "飞行指令发送失败");
            return;
          }
        }
        toast.message("AI 正在处理上一句，请稍候再发（避免重复扣对话算力）");
        return;
      }
      enqueueInstead("busy");
      return;
    }

    setSending(true);
    if (pendingAsk?.messageId) setDismissedAskMessageId(pendingAsk.messageId);
    try {
      const text = opts?.fromQueue
        ? opts.queuedMessage || ""
        : buildMessageWithAttaches(draft.trim());
      const choiceId = ignore ? undefined : pickedChoice;
      // 无会话：在当前项目上新建 Agent Session
      if (!sessionId) {
        if (!text && !ignore) {
          toast.error("请输入内容");
          return;
        }
        const created = await createAgentSession({
          message: text || "开始创作",
          projectId,
          controllerModel: DEFAULT_CONTROLLER_MODEL_ID,
          skillSlug: selectedSkill?.slug,
          genMode,
          canvasSnapshot: takeFreshCanvasSnapshot({
            priorityNodeIds: nodeRefs.map((r) => r.nodeId),
          }),
          aspectRatio: isWizardSkill(selectedSkill) ? viralAspect : undefined,
          clarity: isWizardSkill(selectedSkill) ? viralClarity : undefined,
          targetMarketId: isOverseasSkill(selectedSkill) ? overseasMarketId : undefined,
        });
        const assetIds = pendingAttaches
          .map((a) => a.assetId)
          .filter((id): id is string => Boolean(id));
        if (assetIds.length > 0) {
          try {
            await bindAgentSessionReferenceAssets(created.sessionId, assetIds);
          } catch {
            /* 绑定失败不阻断会话 */
          }
        }
        setDraft("");
        setSelectedChoice(null);
        setPendingAttaches([]);
        setNodeRefs([]);
        bindSessionToUrl(created.sessionId);
        void queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
        void queryClient.invalidateQueries({ queryKey: ["agent-sessions", projectId] });
        toast.success("已开始 AI 对话");
        return;
      }

      // 先把附件写入会话 brief，再发消息（防短 id 漏抽时仍可从 referenceAssetIds 识别）
      if (!opts?.fromQueue) {
        const moreIds = pendingAttaches
          .map((a) => a.assetId)
          .filter((id): id is string => Boolean(id));
        if (moreIds.length > 0) {
          try {
            await bindAgentSessionReferenceAssets(sessionId, moreIds);
          } catch {
            /* ignore */
          }
        }
      }
      await postAgentSessionMessage(sessionId, {
        message: text,
        choiceId,
        action: ignore || opts?.queuedIgnore ? "ignore" : "submit",
        controllerModel: DEFAULT_CONTROLLER_MODEL_ID,
        genMode,
        canvasSnapshot: takeFreshCanvasSnapshot({
          priorityNodeIds: nodeRefs.map((r) => r.nodeId),
        }),
        aspectRatio: sessionIsViral || sessionIsOverseas ? viralAspect : undefined,
        clarity: sessionIsViral || sessionIsOverseas ? viralClarity : undefined,
        targetMarketId: sessionIsOverseas ? overseasMarketId : undefined,
      });
      if (!opts?.fromQueue) {
        setDraft("");
        setSelectedChoice(null);
        setPendingAttaches([]);
        setNodeRefs([]);
      }
      void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
      void queryClient.invalidateQueries({ queryKey: ["agent-sessions", projectId] });
    } catch (err) {
      setDismissedAskMessageId(null);
      toast.error(err instanceof Error ? err.message : "发送失败");
    } finally {
      setSending(false);
    }
  };

  if (query.data && sessionId && String(query.data.projectId) !== String(projectId)) {
    return null;
  }

  const onFabPointerDown = (e: ReactPointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startY: e.clientY,
      originX: fabPos.x,
      originY: fabPos.y,
      moved: false,
    };
    setDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onFabPointerMove = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) >= DRAG_THRESHOLD) {
      drag.moved = true;
    }
    if (!drag.moved) return;
    setFabPos(clampFabPos({ x: drag.originX + dx, y: drag.originY + dy }));
  };

  const onFabPointerUp = (e: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    if (drag.moved) {
      setFabPos((p) => {
        const next = clampFabPos(p);
        persistFabPos(next);
        return next;
      });
      return;
    }
    // 未拖动：视为点击，展开/收起
    setOpen((v) => !v);
    setModelMenuOpen(false);
  };

  const status = query.data?.status;
  // 配方待确认时后端应为 awaiting_user；若仍为 active 也不要把 UI 当成「处理中」
  const running = status === "active" && !query.data?.recipePending;
  // 仅自由创作反问（有选项）算「询问中」；爆款等上传不算
  const asking = showPendingAsk;
  // 订阅 sessionStorage 进度，避免成片后仍卡在「请上传参考视频」
  const [viralSessionTick, setViralSessionTick] = useState(0);
  useEffect(() => {
    const onViral = (ev: Event) => {
      const detail = (ev as CustomEvent<{ projectId?: string }>).detail;
      if (detail?.projectId && detail.projectId !== projectId) return;
      setViralSessionTick((n) => n + 1);
    };
    window.addEventListener(VIRAL_REMAKE_SESSION_EVENT, onViral);
    return () => window.removeEventListener(VIRAL_REMAKE_SESSION_EVENT, onViral);
  }, [projectId]);
  const storyboardShotCount = useCanvasStore((s) => {
    const grid = s.nodes.find((n) => n.type === "storyboard_grid");
    const shots = (grid?.data as { params?: { shots?: unknown[] } } | undefined)?.params?.shots;
    return Array.isArray(shots) ? shots.length : 0;
  });
  const hasFinishedClips = useCanvasStore((s) =>
    s.nodes.some((n) => n.type === "finished_clips_grid")
  );
  const waitingWizardUpload = useMemo(
    () =>
      status === "awaiting_user" &&
      (sessionIsViral || sessionIsOverseas) &&
      !pendingAsk &&
      wizardStillNeedsUpload(projectId, messages),
    [
      status,
      sessionIsViral,
      sessionIsOverseas,
      pendingAsk,
      projectId,
      messages,
      viralSessionTick,
      storyboardShotCount,
      hasFinishedClips,
    ]
  );

  // 爆款 / 出海：工具栏模型行展示画幅·清晰度（与市场同一行）
  const showVideoOptions =
    showOverseasMarket || sessionIsViral || isViralRemakeSkill(selectedSkill);
  const canChat =
    !sessionId ||
    status === "completed" ||
    status === "active" ||
    status === "awaiting_user" ||
    status === undefined;
  const title = query.data?.title || selectedSkill?.title || "AI 助手";

  // 投影/工具/生成忙碌态（画布横幅）
  const canvasBusy = useSyncExternalStore(
    subscribeAgentCanvasBusy,
    getAgentCanvasBusy,
    getAgentCanvasBusy
  );
  const canStopAssistant = Boolean(sessionId && (running || sending || canvasBusy.busy || stopping));

  const handleStopAssistant = async () => {
    if (!sessionId || stopping) return;
    requestAgentCanvasStop();
    sendQueueRef.current = [];
    setQueueLen(0);
    clearAgentCanvasBusy();
    setStopping(true);
    setSending(false);
    try {
      await stopAgentSession(sessionId);
      void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
      toast.message("已停止助手");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "停止失败，请再试一次");
    } finally {
      setStopping(false);
    }
  };
  // 会话 active 时提示「AI 正在处理」（勿在每帧新建 snapshot，见 agentCanvasBusy）
  useEffect(() => {
    if (running) {
      const cur = getAgentCanvasBusy();
      // 已在投影/工具/生成中则不要盖成 orchestrating
      if (!cur.busy) {
        setAgentCanvasBusy("orchestrating", "AI 正在处理你的指令，请稍候…");
      }
      return;
    }
    if (getAgentCanvasBusy().phase === "orchestrating") {
      clearAgentCanvasBusy();
    }
  }, [running]);

  // 投影结束后自动冲刷短队列（最多 2 条）
  useEffect(() => {
    if (!sessionId || flushingQueueRef.current || sending || stopping) return;
    if (running) return;
    if (getAgentCanvasBusy().busy) return;
    const pendingOps = query.data?.graph?.canvasOps;
    if (Array.isArray(pendingOps) && pendingOps.length > 0) return;
    if (sendQueueRef.current.length === 0) return;

    flushingQueueRef.current = true;
    const next = sendQueueRef.current[0];
    sendQueueRef.current = sendQueueRef.current.slice(1);
    setQueueLen(sendQueueRef.current.length);
    void (async () => {
      try {
        await submitReply({
          fromQueue: true,
          queuedMessage: next.message,
          queuedChoiceId: next.choiceId,
          queuedIgnore: next.ignore,
          ignore: next.ignore,
        });
      } finally {
        flushingQueueRef.current = false;
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅在忙碌态/ops 变化时冲刷
  }, [
    sessionId,
    sending,
    running,
    canvasBusy.busy,
    canvasBusy.phase,
    query.data?.graph?.canvasOps,
    query.data?.graph?.revision,
  ]);

  const showControlHint = running || asking || waitingWizardUpload || canvasBusy.busy || queueLen > 0;
  const controlHintText = asking
    ? "AI 正在询问你，请在对话里选择或回复"
    : waitingWizardUpload
      ? sessionIsOverseas
        ? `请选择出海国家版本（当前 ${overseasMarketLabel}），并可选画幅/清晰度；上传参考视频后发送`
        : "请上传参考视频，并说明替换内容；可选画幅/清晰度"
      : queueLen > 0
        ? `排队中 ${queueLen} 条：画布应用完成后将自动发送`
      : canvasBusy.busy && canvasBusy.message
        ? canvasBusy.message
        : running
          ? "AI 正在操控画布，请稍候再操作或发送"
          : "";

  // 靠近顶/左边缘时翻转聊天框锚点，避免裁切
  const chatAbove = fabPos.y > 220;
  // 面板加宽后，靠右对齐阈值略提高，避免贴边裁切
  const chatAlignEnd = fabPos.x + FAB_SIZE > 260;

  return (
    <>
      {/* 画布顶部提醒：AI 编排 / 投影 / 生成中 */}
      {showControlHint && controlHintText ? (
        <div className="pointer-events-none fixed left-1/2 top-16 z-50 -translate-x-1/2 px-3">
          <div
            className={cn(
              "pointer-events-auto flex max-w-[min(92vw,420px)] items-center gap-2 rounded-full border px-3.5 py-2 text-[12px] shadow-lg backdrop-blur-md",
              asking
                ? "border-amber-500/30 bg-amber-500/15 text-amber-800 dark:text-amber-200"
                : "border-violet-500/35 bg-violet-600/90 text-white"
            )}
            role="status"
            aria-live="polite"
          >
            <Loader2 size={14} className="shrink-0 animate-spin opacity-90" />
            <span className="min-w-0 flex-1 leading-snug">{controlHintText}</span>
            {sessionId && !asking ? (
              <button
                type="button"
                className="shrink-0 rounded-full bg-white/20 px-2 py-0.5 text-[10px] hover:bg-white/30"
                onClick={() => void handleStopAssistant()}
              >
                停止
              </button>
            ) : null}
            {!open ? (
              <button
                type="button"
                className="shrink-0 rounded-full bg-white/20 px-2 py-0.5 text-[10px] hover:bg-white/30"
                onClick={() => setOpen(true)}
              >
                查看
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

    <div
      className="pointer-events-none fixed z-40"
      style={{
        left: fabPos.x,
        top: fabPos.y,
        width: FAB_SIZE,
        height: FAB_SIZE,
      }}
    >
      {/* 聊天框锚定在按钮旁，随按钮一起移动 */}
      {open ? (
        <div
          className={cn(
            "pointer-events-auto absolute flex w-[440px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-border-default bg-bg-surface/95 shadow-xl backdrop-blur-md dark:border-[#363636] dark:bg-[#1a1a1a]/95",
            "max-h-[min(80vh,720px)] animate-in fade-in zoom-in-95 duration-200",
            chatAbove ? "bottom-full mb-3" : "top-full mt-3",
            chatAlignEnd ? "right-0" : "left-0"
          )}
        >
          <div className="flex items-center gap-1 border-b border-border-default px-2.5 py-2 dark:border-[#363636]">
            <img
              src={FAB_ICON}
              alt=""
              className="size-7 shrink-0 rounded-full object-cover"
              draggable={false}
            />
            <div className="min-w-0 flex-1 truncate px-1 text-sm font-medium">{title}</div>
            {asking ? (
              <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] text-amber-600 dark:text-amber-300">
                询问中
              </span>
            ) : null}
            {running || canvasBusy.busy ? (
              <span className="shrink-0 rounded-full bg-violet-500/15 px-2 py-0.5 text-[10px] text-violet-700 dark:text-violet-300">
                {canvasBusy.phase === "generating"
                  ? "生成中"
                  : canvasBusy.phase === "tool"
                    ? "工具中"
                    : canvasBusy.phase === "projecting"
                      ? "操控中"
                      : "处理中"}
              </span>
            ) : null}
            <button
              type="button"
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-lg opacity-70 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10",
                pickNodeMode && "bg-violet-500/20 text-violet-600 opacity-100 dark:text-violet-300"
              )}
              aria-label="引用节点"
              title={pickNodeMode ? "点击画布节点以引用（再点此结束）" : "引用画布节点（点击画布选择）"}
              onClick={() => {
                if (pickNodeMode) {
                  setPickNodeMode(false);
                  pickBaselineRef.current = null;
                  return;
                }
                startPickNode();
              }}
            >
              <Crosshair size={14} />
            </button>
            <button
              type="button"
              className="flex size-7 shrink-0 items-center justify-center rounded-lg opacity-70 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
              aria-label="新建对话"
              title="新建对话"
              onClick={handleNewChat}
            >
              <MessageSquarePlus size={14} />
            </button>
            <div className="relative">
              <button
                type="button"
                className="flex size-7 shrink-0 items-center justify-center rounded-lg opacity-70 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
                aria-label="历史对话"
                title="历史对话"
                onClick={() => {
                  setHistoryOpen((v) => !v);
                  setModelMenuOpen(false);
                  setSkillMenuOpen(false);
                  setGenMenuOpen(false);
                }}
              >
                <History size={14} />
              </button>
              {historyOpen ? (
                <div className="absolute right-0 top-full z-50 mt-1 max-h-56 w-56 overflow-auto rounded-xl border border-border-default bg-bg-surface py-1 shadow-lg dark:border-[#363636] dark:bg-[#222]">
                  {historyQuery.isLoading ? (
                    <div className="px-3 py-2 text-[11px] text-muted-foreground">加载中…</div>
                  ) : (historyQuery.data?.items || []).length === 0 ? (
                    <div className="px-3 py-2 text-[11px] text-muted-foreground">暂无历史</div>
                  ) : (
                    (historyQuery.data?.items || []).map((it) => (
                      <button
                        key={it.sessionId}
                        type="button"
                        className={cn(
                          "block w-full px-3 py-2 text-left hover:bg-black/5 dark:hover:bg-white/10",
                          it.sessionId === sessionId && "bg-violet-500/10"
                        )}
                        onClick={() => {
                          bindSessionToUrl(it.sessionId);
                          setHistoryOpen(false);
                        }}
                      >
                        <div className="truncate text-[11px] font-medium">{it.title}</div>
                        <div className="text-[10px] text-muted-foreground">{it.status}</div>
                      </button>
                    ))
                  )}
                </div>
              ) : null}
            </div>
            {query.isFetching || running || sending ? (
              <Loader2 size={12} className="mx-0.5 animate-spin opacity-60" />
            ) : null}
            <button
              type="button"
              className="flex size-7 shrink-0 items-center justify-center rounded-lg opacity-60 hover:bg-black/5 hover:opacity-100 dark:hover:bg-white/10"
              aria-label="关闭"
              onClick={() => {
                setOpen(false);
                closeMenus();
              }}
            >
              <X size={14} />
            </button>
          </div>

          {agentTimelineSummary && agentTimelineSummary.totalCount > 0 ? (
            <div className="flex items-center gap-1.5 border-b border-border-default/50 px-3 py-1.5 text-[10.5px] text-muted-foreground dark:border-white/10">
              <span className="inline-block size-1.5 rounded-full bg-violet-500/70" />
              <span>
                时间线 {agentTimelineSummary.readyCount}/{agentTimelineSummary.totalCount} 镜已完成
              </span>
            </div>
          ) : null}

          {query.data?.runtimePlan?.steps?.length ? (
            <div className="border-b border-border-default/50 px-3 py-1.5 dark:border-white/10">
              <div className="flex flex-wrap items-center gap-1.5 text-[10.5px] text-muted-foreground">
                <span className="shrink-0 font-medium text-foreground/80">计划</span>
                {query.data.runtimePlan.steps.map((s, i) => {
                  const st = String(s.status || "pending");
                  const label = String(s.step || `步骤${i + 1}`);
                  const tone =
                    st === "completed"
                      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
                      : st === "in_progress"
                        ? "bg-amber-500/15 text-amber-800 dark:text-amber-200"
                        : "bg-black/5 text-muted-foreground dark:bg-white/5";
                  return (
                    <span
                      key={`${i}-${label}`}
                      className={cn("rounded px-1.5 py-0.5", tone)}
                      title={st}
                    >
                      {label}
                    </span>
                  );
                })}
              </div>
              {query.data.runtimePlan.explanation ? (
                <p className="mt-0.5 truncate text-[10px] text-muted-foreground/80">
                  {query.data.runtimePlan.explanation}
                </p>
              ) : null}
            </div>
          ) : null}

          {(running || sseHint) && (sseHint || query.data?.runtimeEvents?.length) ? (
            <div className="border-b border-border-default/40 px-3 py-1 text-[10px] text-muted-foreground dark:border-white/10">
              {(() => {
                const last =
                  sseHint ||
                  (query.data?.runtimeEvents?.length
                    ? query.data.runtimeEvents[query.data.runtimeEvents.length - 1]
                    : null);
                const kind = String(last?.kind || "");
                const msg = String(last?.message || "").trim();
                if (!msg) return null;
                return (
                  <span>
                    {kind === "tool_started"
                      ? "工具 "
                      : kind === "plan_updated"
                        ? "计划 "
                        : kind === "ask_user"
                          ? "询问 "
                          : kind === "hard_gate"
                            ? "硬闸 "
                            : kind === "steer_applied"
                              ? "飞行 "
                              : ""}
                    {msg}
                  </span>
                );
              })()}
            </div>
          ) : null}

          {sessionId &&
          query.data?.recipePending &&
          query.data?.mySkillDefaults?.nodeRecipe?.nodes?.length ? (
            <div className="mx-3 mt-2 rounded-xl border border-violet-500/30 bg-violet-500/10 px-3 py-2.5 text-[11px] leading-relaxed">
              <p className="font-medium text-foreground">确认节点配方后铺到画布</p>
              <p className="mt-1 text-muted-foreground">
                {(query.data.mySkillDefaults.nodeRecipe.nodes || [])
                  .map((n) => n.label || n.key)
                  .filter(Boolean)
                  .join(" → ")}
              </p>
              {query.data.mySkillDefaults.nodeRecipe.orderNotes ? (
                <p className="mt-1 text-muted-foreground/90">
                  {query.data.mySkillDefaults.nodeRecipe.orderNotes}
                </p>
              ) : null}
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-2.5 py-1.5 text-[11px] font-medium text-white hover:bg-violet-500 disabled:opacity-60"
                  disabled={applyingRecipe || !query.data.skillSlug}
                  onClick={() => {
                    void (async () => {
                      if (!query.data?.skillSlug || !projectId || !sessionId) return;
                      setApplyingRecipe(true);
                      try {
                        await applySkillRecipe({
                          slug: query.data.skillSlug,
                          projectId: String(projectId),
                          sessionId,
                          force: recipeForceHint,
                        });
                        toast.success("已按配方铺到画布，请填写后手动生成");
                        setRecipeForceHint(false);
                        void queryClient.invalidateQueries({
                          queryKey: ["agent-session", sessionId],
                        });
                      } catch (err) {
                        if (err instanceof ApiError && err.code === "CONFLICT") {
                          setRecipeForceHint(true);
                          toast.error("该项目已铺过配方，再点一次将强制追加");
                        } else {
                          toast.error(
                            err instanceof Error ? err.message : "铺节点失败"
                          );
                        }
                      } finally {
                        setApplyingRecipe(false);
                      }
                    })();
                  }}
                >
                  {applyingRecipe ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Layers size={12} />
                  )}
                  {recipeForceHint ? "确认再次铺节点" : "按配方铺到画布"}
                </button>
              </div>
            </div>
          ) : null}

          <div ref={listRef} className="min-h-0 flex-1 space-y-2 overflow-y-auto px-3 py-2 text-xs leading-relaxed">
            {!sessionId ? (
              <div className="space-y-2.5 py-2 text-muted-foreground">
                <p className="text-foreground/90">你好，我是画布 AI 助手。</p>
                {isViralRemakeSkill(selectedSkill) ? (
                  <p>已选「爆款拉片复刻」：请上传参考视频，或直接描述创作想法。确认后才会批量出视频。</p>
                ) : isOverseasSkill(selectedSkill) ? (
                  <p>已选「一键出海」：请上传参考视频，并说明目标市场。确认后才会出片。</p>
                ) : (
                  <>
                    <p>
                      要用画布工具时，请说<strong>按钮上的全名</strong>，例如「多角度」「打光」「九宫格」「故事板」。
                      不要只说「侧面」「光不好」这类含糊说法，否则我会先问你要用哪一个。有参考图请点输入框准星引用画布节点，或上传图片。
                    </p>
                    <ul className="list-none space-y-1.5 text-[11px] leading-relaxed">
                      <li>
                        <span className="font-medium text-foreground/85">多角度</span>
                        ：说「多角度」，可再加「看背面」「右侧俯视特写」。
                      </li>
                      <li>
                        <span className="font-medium text-foreground/85">打光</span>
                        ：说「打光」，可再加「左侧轮廓光」「逆光暗一点」。
                      </li>
                      <li>
                        <span className="font-medium text-foreground/85">九宫格</span>
                        ：只说「九宫格」会做 3×3。子功能请说全名，如「角色设定图」「25宫格连贯分镜」。要单格时说「宫格切分」。
                      </li>
                      <li>
                        <span className="font-medium text-foreground/85">故事板</span>
                        ：说「故事板」出一张多格合成图。调度请说「调度故事板」。和「分镜表」不是一回事。
                      </li>
                      <li>
                        <span className="font-medium text-foreground/85">其它全名</span>
                        ：抠图、扩图、高清、人像调节、情绪调节、全景等，用按钮上的名字。
                      </li>
                    </ul>
                    <p>
                      图片类能力说了就会做（扣生图算力）。视频出片前我会先问你确认。
                    </p>
                  </>
                )}
              </div>
            ) : query.isLoading ? (
              <p className="text-muted-foreground">加载中…</p>
            ) : messages.length === 0 ? (
              <p className="text-muted-foreground">暂无消息</p>
            ) : (
              messages.map((m) => {
                const isUser = m.role === "user";
                const roleTag = m.agentRole ? ROLE_LABEL[m.agentRole] || m.agentRole : m.role;
                const arts = m.artifacts || [];
                const summary = arts.find(
                  (a) => a && typeof a === "object" && (a as AskedSummaryArtifact).kind === "asked_summary"
                ) as AskedSummaryArtifact | undefined;
                const ask = arts.find(
                  (a) => a && typeof a === "object" && (a as AskChoicesArtifact).kind === "ask_choices"
                ) as AskChoicesArtifact | undefined;
                const mediaArt = arts.find(
                  (a) => a && typeof a === "object" && (a as MediaAssetsArtifact).kind === "media_assets"
                ) as MediaAssetsArtifact | undefined;
                const mediaItems = Array.isArray(mediaArt?.items) ? mediaArt.items : [];

                return (
                  <div key={m.id} className={cn("flex", isUser ? "justify-end" : "justify-start")}>
                    <div
                      className={cn(
                        "max-w-[92%] rounded-2xl px-3 py-2",
                        isUser
                          ? "bg-violet-600 text-white"
                          : "bg-black/[0.04] text-foreground dark:bg-white/[0.06]"
                      )}
                    >
                      {!isUser ? (
                        <div className="mb-1 text-[10px] uppercase tracking-wide opacity-45">{roleTag}</div>
                      ) : null}
                      {m.content ? (
                        <div className="whitespace-pre-wrap">{m.content}</div>
                      ) : null}

                      {mediaItems.length > 0 ? (
                        <AgentChatMediaGrid projectId={projectId} items={mediaItems} />
                      ) : null}

                      {summary?.items?.length ? (
                        <div className="mt-2 rounded-xl border border-border-default/60 bg-bg-surface/40 dark:border-white/10">
                          <button
                            type="button"
                            className="flex w-full items-center gap-1 px-2.5 py-1.5 text-left text-[11px] font-medium"
                            onClick={() => setAskedOpen((v) => !v)}
                          >
                            {askedOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                            已询问
                          </button>
                          {askedOpen ? (
                            <ul className="space-y-1.5 border-t border-border-default/50 px-2.5 py-2 dark:border-white/10">
                              {summary.items.map((it, idx) => (
                                <li key={`${it.q}-${idx}`}>
                                  <div className="font-medium text-foreground/90">{it.q}</div>
                                  <div className="text-muted-foreground">{it.a}</div>
                                </li>
                              ))}
                            </ul>
                          ) : null}
                        </div>
                      ) : null}

                      {ask && ask.status !== "pending" && ask.options?.length ? (
                        <div className="mt-2 space-y-1 opacity-60">
                          {ask.options.map((opt, idx) => (
                            <div key={opt.id} className="rounded-lg border border-border-default/40 px-2 py-1.5">
                              {idx + 1}. {opt.label}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })
            )}

            {askedItems.length > 0 &&
            !messages.some((m) =>
              (m.artifacts || []).some((a) => (a as AskedSummaryArtifact).kind === "asked_summary")
            ) ? (
              <div className="rounded-xl border border-border-default/60 px-2.5 py-2">
                <div className="mb-1 text-[11px] font-medium">已询问</div>
                <ul className="space-y-1">
                  {askedItems.map((it, idx) => (
                    <li key={`${it.q}-${idx}`}>
                      <span className="font-medium">{it.q}：</span>
                      <span className="text-muted-foreground">{it.a}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {query.isError ? <p className="text-destructive">无法加载会话消息</p> : null}
          </div>

          {showPendingAsk && pendingAsk ? (
            <div className="border-t border-border-default bg-black/[0.02] px-3.5 py-3 dark:border-[#363636] dark:bg-white/[0.03]">
              <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-amber-600 dark:text-amber-300">
                <Loader2 size={11} className="animate-spin" />
                询问中
              </div>
              <div className="mb-2 space-y-1.5">
                {(pendingAsk.artifact.options || []).map((opt, idx) => {
                  const active = selectedChoice === opt.id;
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => {
                        setSelectedChoice(opt.id);
                        // 确认类选项点选即提交并收起询问框；需补充说明的仍留输入框
                        if (!askChoiceNeedsExtraText(opt.id)) {
                          void submitReply({ choiceId: opt.id });
                        }
                      }}
                      className={cn(
                        "block w-full rounded-xl border px-2.5 py-2 text-left text-[11px] leading-snug transition-colors",
                        active
                          ? "border-violet-500/70 bg-violet-500/15"
                          : "border-border-default/70 hover:border-white/25 hover:bg-white/[0.04]"
                      )}
                    >
                      <span className="mr-1 opacity-50">{idx + 1}.</span>
                      {opt.label}
                    </button>
                  );
                })}
              </div>
              <ComposerBox
                draft={draft}
                setDraft={setDraft}
                placeholder={
                  askChoiceNeedsExtraText(selectedChoice)
                    ? "请补充说明…"
                    : "也可直接输入…"
                }
                disabled={sending}
                pendingAttaches={pendingAttaches}
                onRemoveAttach={(id) =>
                  setPendingAttaches((prev) => prev.filter((p) => p.localId !== id))
                }
                nodeRefs={nodeRefs}
                onRemoveNodeRef={(id) => setNodeRefs((prev) => prev.filter((r) => r.nodeId !== id))}
                onFocusNodeRef={focusCanvasNode}
                modelSupportsFiles={allowFileAttach}
                onPickFiles={() => fileInputRef.current?.click()}
                skillLabel={selectedSkill?.title || "Skill"}
                skillMenuOpen={skillMenuOpen}
                setSkillMenuOpen={setSkillMenuOpen}
                skillOptions={skillOptions}
                selectedSkillSlug={selectedSkill?.slug || null}
                onSelectSkill={(s) => {
                  void handleSelectSkill(s);
                }}
                genMode={genMode}
                genModeLabel={genModeLabel}
                genMenuOpen={genMenuOpen}
                setGenMenuOpen={setGenMenuOpen}
                onSelectGenMode={(m) => {
                  persistGenMode(m);
                  setGenMenuOpen(false);
                  setMediaModelMenuOpen(false);
                }}
                mediaModelMenuOpen={mediaModelMenuOpen}
                setMediaModelMenuOpen={setMediaModelMenuOpen}
                mediaModelTab={mediaModelTab}
                setMediaModelTab={setMediaModelTab}
                mediaImageLabel={mediaImageLabel}
                mediaVideoLabel={mediaVideoLabel}
                modelLabel={modelLabel}
                modelMenuOpen={modelMenuOpen}
                setModelMenuOpen={setModelMenuOpen}
                modelOptions={modelOptions}
                controllerModel={controllerModel}
                onSelectModel={(id, label) => {
                  const opt = modelOptions.find((m) => m.id === id) as
                    | ControllerModelOption
                    | undefined;
                  if (opt?.configured === false) {
                    toast.error(`「${label}」未配置密钥，请另选`);
                    return;
                  }
                  persistModel(id);
                  setModelMenuOpen(false);
                  toast.message(`已切换为 ${label}`);
                }}
                showOverseasMarket={showOverseasMarket}
                overseasMarketId={overseasMarketId}
                overseasMarketLabel={overseasMarketLabel}
                marketMenuOpen={marketMenuOpen}
                setMarketMenuOpen={setMarketMenuOpen}
                onSelectMarket={(id) => {
                  persistOverseasMarket(id);
                  setMarketMenuOpen(false);
                  toast.message(`出海版本：${getOverseasMarket(id).label}`);
                }}
                showVideoOptions={showVideoOptions}
                aspectRatio={viralAspect}
                clarity={viralClarity}
                aspectMenuOpen={aspectMenuOpen}
                setAspectMenuOpen={setAspectMenuOpen}
                clarityMenuOpen={clarityMenuOpen}
                setClarityMenuOpen={setClarityMenuOpen}
                onSelectAspect={persistViralAspect}
                onSelectClarity={persistViralClarity}
                sending={sending}
                canSend
                canStop={canStopAssistant}
                stopping={stopping}
                onStop={() => void handleStopAssistant()}
                onIgnore={() => void submitReply({ ignore: true })}
                onSend={() => void submitReply()}
                showIgnore
                turnCreditLabel={turnCreditLabel}
              />
            </div>
          ) : canChat ? (
            <div className="border-t border-border-default px-3.5 py-3 dark:border-[#363636]">
              <ComposerBox
                draft={draft}
                setDraft={setDraft}
                placeholder={
                  !sessionId
                    ? isOverseasSkill(selectedSkill)
                      ? `上传参考视频；已选「${overseasMarketLabel}」，可选画幅/清晰度后发送…`
                      : isViralRemakeSkill(selectedSkill)
                        ? "上传参考视频，说明想替换什么；可选画幅/清晰度后发送…"
                        : "请说工具全名：多角度、打光、九宫格、故事板、抠图… 也可 @ 节点"
                    : waitingWizardUpload
                      ? sessionIsOverseas
                        ? `上传参考视频；出海版本「${overseasMarketLabel}」，可选画幅/清晰度后发送…`
                        : "上传参考视频，说明想替换什么；可选画幅/清晰度后发送…"
                      : status === "completed"
                        ? "继续说：加镜头、改风格、改剧本…"
                        : running
                          ? "助手处理中…"
                          : "继续跟助手说… 或点准星引用节点"
                }
                disabled={running || sending}
                pendingAttaches={pendingAttaches}
                onRemoveAttach={(id) =>
                  setPendingAttaches((prev) => prev.filter((p) => p.localId !== id))
                }
                nodeRefs={nodeRefs}
                onRemoveNodeRef={(id) => setNodeRefs((prev) => prev.filter((r) => r.nodeId !== id))}
                onFocusNodeRef={focusCanvasNode}
                modelSupportsFiles={allowFileAttach}
                onPickFiles={() => fileInputRef.current?.click()}
                skillLabel={selectedSkill?.title || "Skill"}
                skillMenuOpen={skillMenuOpen}
                setSkillMenuOpen={setSkillMenuOpen}
                skillOptions={skillOptions}
                selectedSkillSlug={selectedSkill?.slug || null}
                onSelectSkill={(s) => {
                  void handleSelectSkill(s);
                }}
                genMode={genMode}
                genModeLabel={genModeLabel}
                genMenuOpen={genMenuOpen}
                setGenMenuOpen={setGenMenuOpen}
                onSelectGenMode={(m) => {
                  persistGenMode(m);
                  setGenMenuOpen(false);
                  setMediaModelMenuOpen(false);
                }}
                mediaModelMenuOpen={mediaModelMenuOpen}
                setMediaModelMenuOpen={setMediaModelMenuOpen}
                mediaModelTab={mediaModelTab}
                setMediaModelTab={setMediaModelTab}
                mediaImageLabel={mediaImageLabel}
                mediaVideoLabel={mediaVideoLabel}
                modelLabel={modelLabel}
                modelMenuOpen={modelMenuOpen}
                setModelMenuOpen={setModelMenuOpen}
                modelOptions={modelOptions}
                controllerModel={controllerModel}
                onSelectModel={(id, label) => {
                  const opt = modelOptions.find((m) => m.id === id) as
                    | ControllerModelOption
                    | undefined;
                  if (opt?.configured === false) {
                    toast.error(`「${label}」未配置密钥，请另选`);
                    return;
                  }
                  persistModel(id);
                  setModelMenuOpen(false);
                  toast.message(`已切换为 ${label}`);
                }}
                showOverseasMarket={showOverseasMarket}
                overseasMarketId={overseasMarketId}
                overseasMarketLabel={overseasMarketLabel}
                marketMenuOpen={marketMenuOpen}
                setMarketMenuOpen={setMarketMenuOpen}
                onSelectMarket={(id) => {
                  persistOverseasMarket(id);
                  setMarketMenuOpen(false);
                  toast.message(`出海版本：${getOverseasMarket(id).label}`);
                }}
                showVideoOptions={showVideoOptions}
                aspectRatio={viralAspect}
                clarity={viralClarity}
                aspectMenuOpen={aspectMenuOpen}
                setAspectMenuOpen={setAspectMenuOpen}
                clarityMenuOpen={clarityMenuOpen}
                setClarityMenuOpen={setClarityMenuOpen}
                onSelectAspect={persistViralAspect}
                onSelectClarity={persistViralClarity}
                sending={sending}
                canSend={
                  !running &&
                  Boolean(draft.trim() || nodeRefs.length || pendingAttaches.some((a) => a.assetId))
                }
                canStop={canStopAssistant}
                stopping={stopping}
                onStop={() => void handleStopAssistant()}
                onSend={() => void submitReply()}
                onKeySubmit={() => {
                  if (
                    !running &&
                    (draft.trim() || nodeRefs.length || pendingAttaches.some((a) => a.assetId))
                  ) {
                    void submitReply();
                  }
                }}
                turnCreditLabel={turnCreditLabel}
              />
            </div>
          ) : null}

          <input
            ref={fileInputRef}
            type="file"
            accept="image/*,video/*,audio/*,.mp4,.webm,.mov,.mkv,.avi,.m4v,.jpg,.jpeg,.png,.gif,.webp,.bmp,.svg,.mp3,.wav,.m4a,.aac,.ogg,.flac"
            multiple
            className="hidden"
            onChange={(e) => {
              void handlePickFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </div>
      ) : null}

      {/* 圆形 AI 按钮：可拖动；拖动时聊天框同步移动 */}
      <button
        type="button"
        className={cn(
          "pointer-events-auto absolute inset-0 touch-none rounded-full",
          "bg-white shadow-[0_8px_28px_rgba(37,99,235,0.28)] ring-1 ring-[#2563eb]/25",
          "dark:bg-[#1a1a1a] dark:ring-[#3b82f6]/40",
          open && "ring-2 ring-[#2563eb]/50",
          dragging ? "cursor-grabbing scale-105" : "cursor-grab hover:scale-105",
          !dragging && "transition-transform duration-200"
        )}
        aria-label={open ? "收起 AI 对话" : "打开 AI 对话"}
        title={dragging ? "拖动中…" : open ? "拖动移动 · 点击收起" : "拖动移动 · 点击打开"}
        onPointerDown={onFabPointerDown}
        onPointerMove={onFabPointerMove}
        onPointerUp={onFabPointerUp}
        onPointerCancel={onFabPointerUp}
      >
        <img
          src={FAB_ICON}
          alt="AI"
          className="pointer-events-none size-full rounded-full object-cover p-0.5"
          draggable={false}
        />
        {(running || asking || canvasBusy.busy) && !open ? (
          <span className="absolute -right-0.5 -top-0.5 size-3 rounded-full bg-violet-500 ring-2 ring-white dark:ring-[#1a1a1a]">
            <span className="absolute inset-0 animate-ping rounded-full bg-violet-400 opacity-75" />
          </span>
        ) : null}
      </button>
    </div>
    </>
  );
}
