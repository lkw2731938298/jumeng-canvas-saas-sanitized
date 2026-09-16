"use client";

/**
 * 创建 Skill：AI 创建为主入口；生成技能包（SKILL.md + references）后可编辑流程再保存。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  Code2,
  Eye,
  FileUp,
  FolderOpen,
  ImagePlus,
  Loader2,
  Plus,
  Sparkles,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

import {
  createSkillFromForm,
  draftSkillFromPrompt,
  importSkillPackage,
  uploadSkillCover,
  type SkillExecutionMode,
  type SkillFlowStep,
  type SkillItem,
  type SkillNodeRecipe,
  type SkillPackageFiles,
} from "@/lib/api/skills";
import { ApiError } from "@/lib/api/client";
import { validateImageAspectRatio } from "@/lib/validateImageAspectRatio";
import { formatCreditLabel, getAgentSkillPricing } from "@/lib/api/credits";
import { cn } from "@/lib/utils";

type Props = {
  open: boolean;
  onClose: () => void;
  onCreated?: (skill: SkillItem) => void;
  initialPrompt?: string;
};

type ContentTab = "preview" | "code" | "upload";
type MediaKind = "image" | "video" | "text" | "audio";

/** 技能包能力对齐：可编辑的参考文档行（保存时序列化为 {path: content} 字典） */
type PackageFileRow = { path: string; content: string };

const PKG_FLOW = "references/flow.md";
const PKG_LAYOUT = "references/node-layout.md";
const PKG_GENERATION = "references/generation.md";
const PKG_TOOLS = "references/canvas-tools.md";
const PKG_DEFAULT_PATHS = [PKG_FLOW, PKG_LAYOUT, PKG_GENERATION, PKG_TOOLS] as const;

function packageFilesToDict(rows: PackageFileRow[]): SkillPackageFiles | undefined {
  const out: SkillPackageFiles = {};
  for (const row of rows) {
    const path = row.path.trim();
    const content = row.content.trim();
    if (path && content) out[path] = content;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function packageFilesToRows(files: SkillPackageFiles | undefined | null): PackageFileRow[] {
  if (!files) return [];
  return Object.entries(files).map(([path, content]) => ({ path, content: String(content) }));
}

function recipeSummaryText(recipe: SkillNodeRecipe | null): string {
  if (!recipe?.nodes?.length) return "";
  return recipe.nodes.map((n) => n.label || n.key).join(" → ");
}

/** 按产出类型给出默认可编辑流程（与后端 default_flow_steps_for_media 对齐） */
function defaultFlowSteps(kind: MediaKind): SkillFlowStep[] {
  if (kind === "image") {
    return [
      { title: "读画布与素材", note: "get_canvas_state；复用已有节点；处理附件/准星" },
      { title: "出方案并确认", note: "用 ask_user 确认画面内容/风格后再改画布" },
      { title: "布点连线填词", note: "按 nodeRecipe add_node + connect_nodes；写入 prompt/content" },
      { title: "确认后出图", note: "用户说生成后再 generate_node；默认一张图；禁止未确认出图" },
    ];
  }
  if (kind === "text") {
    return [
      { title: "读画布与需求", note: "读最新画布与用户原文" },
      { title: "出文案方案", note: "ask_user 确认结构/语气" },
      { title: "写入文本节点", note: "add/update text_input；不扣媒体生成算力" },
      { title: "按需再生成", note: "仅当用户明确要求润色/重写时再改" },
    ];
  }
  if (kind === "audio") {
    return [
      { title: "读画布与情绪", note: "锁定情绪、场景、时长" },
      { title: "确认方案", note: "ask_user 确认 BGM/音效方向" },
      { title: "布音频节点", note: "add audio_input 并写 prompt" },
      { title: "确认后生成", note: "用户确认后再 generate_node" },
    ];
  }
  return [
    { title: "先分析需求", note: "对话讲清卖点/时长/画幅；有过长参考片再按关键点切 5–12 秒整数段" },
    { title: "出故事板并确认", note: "storyboard；展示产物；ask_user 确认后再出视频" },
    { title: "按板搭镜头", note: "读板建 video_input；连故事板与产品图；写批次镜号与秒数" },
    { title: "确认后同批出片", note: "用户确认后同批 generate_node；系统合并成一条；禁止一条条续跑" },
  ];
}

function kindLabelCn(kind: MediaKind): string {
    return kind === "image"
    ? "生图"
    : kind === "text"
      ? "文本"
      : kind === "audio"
        ? "音频"
        : "短视频";
}

function buildFlowMarkdown(
  title: string,
  kind: MediaKind,
  steps: SkillFlowStep[]
): string {
  const lines = [
    `# 流程设置 · ${title.trim() || "我的 Skill"}`,
    "",
    `> 产出类型：**${kindLabelCn(kind)}**（\`${kind}\`）。助手按下列步骤选用工具；可跳步，须先读画布。`,
    "",
  ];
  steps.forEach((step, i) => {
    lines.push(`## ${i + 1}. ${step.title.trim() || `步骤${i + 1}`}`);
    lines.push("");
    lines.push((step.note || "按画布现状执行").trim() || "按画布现状执行");
    lines.push("");
  });
  return lines.join("\n");
}

function buildDefaultPackageRows(input: {
  title: string;
  description: string;
  mediaKind: MediaKind;
  outputContent: string;
  nodeRecipe: SkillNodeRecipe | null;
  flowSteps: SkillFlowStep[];
  existing?: PackageFileRow[];
}): PackageFileRow[] {
  const name = input.title.trim() || "我的 Skill";
  const kind = input.mediaKind;
  const nodes = input.nodeRecipe?.nodes || [];
  const edges = input.nodeRecipe?.edges || [];
  const nodeLines =
    nodes.length > 0
      ? nodes.map((n) => {
          const ntype =
            n.kind === "image"
              ? "image_input"
              : n.kind === "video"
                ? "video_input"
                : n.kind === "audio"
                  ? "audio_input"
                  : "text_input";
          return `| ${n.label || n.key} | \`${ntype}\` | \`${n.key}\` | ${n.hint || "按方案填写"} |`;
        })
      : ["| （按类型默认） | — | — | 保存后由默认配方补齐 |"];
  const edgeLines =
    edges.length > 0
      ? edges.map((e) => `- \`${e.from}\` → \`${e.to}\`（目标 \`ref_in\`）`)
      : ["- （按类型默认连线）"];
  const genRule =
    kind === "image"
      ? "用户明确说生成/出图后，对图片节点同批 `generate_node`。"
      : kind === "text"
        ? "文本以写入节点为主；仅用户要求重写时再改 content。"
        : kind === "audio"
          ? "用户确认后再对音频节点 `generate_node`。"
          : "用户说确认生成/出片后，对空视频节点同批 `generate_node`；禁止一条条续跑。";

  const defaults: Record<string, string> = {
    [PKG_FLOW]: buildFlowMarkdown(name, kind, input.flowSteps),
    [PKG_LAYOUT]: [
      `# 节点布置 · ${name}`,
      "",
      input.description.trim() || "按节点表布点。",
      "",
      "## 节点表",
      "",
      "| 名称 | type | key | 填写要点 |",
      "|------|------|-----|----------|",
      ...nodeLines,
      "",
      "## 连线",
      "",
      ...edgeLines,
      "",
      "## 布局建议",
      "",
      "- 相关节点从左到右、从上到下排列，间距约 220，避免重叠；可用 `layout_hint`。",
      "- 能复用已有节点就改参/连线，不要无故新建重复节点。",
      "",
    ].join("\n"),
    [PKG_GENERATION]: [
      `# 生成需求 · ${name}`,
      "",
      `**产出**：${kindLabelCn(kind)}`,
      "",
      input.outputContent.trim() || "按节点 hint 与方案产出。",
      "",
      "## 何时生成",
      "",
      "1. 先布点、填词、连线；未确认不要扣媒体生成算力。",
      `2. ${genRule}`,
      "3. 本轮已成功提交生成后收束；用户再说「再生成/重做」才继续。",
      "4. 准星 +「生成视频」= 短出片（只出一条）；「确认生成」走完整流程。",
      "",
    ].join("\n"),
    [PKG_TOOLS]: [
      `# 工具对照 · ${name}`,
      "",
      "| 意图 | 工具 |",
      "|------|------|",
      "| 读画布 | `get_canvas_state` / `inspect_node` |",
      "| 新建节点 | `add_node`（text/image/video/audio_input） |",
      "| 改参数 | `update_node_params` |",
      "| 连参考 | `connect_nodes` → `ref_in` |",
      "| 画布工具 | `run_canvas_tool`（须官方全名） |",
      "| 出片/出图 | `generate_node`（须用户确认） |",
      "| 确认/缺信息 | `ask_user` |",
      "",
      "编排顺序见 `flow.md`；节点细节见 `node-layout.md`。",
      "",
    ].join("\n"),
  };

  const byPath = new Map<string, string>();
  for (const row of input.existing || []) {
    if (row.path.trim() && row.content.trim()) byPath.set(row.path.trim(), row.content);
  }
  // 流程设置权威：始终用当前步骤重写 flow.md
  byPath.set(PKG_FLOW, defaults[PKG_FLOW]);
  for (const path of PKG_DEFAULT_PATHS) {
    if (!byPath.get(path)?.trim()) byPath.set(path, defaults[path]);
  }
  const ordered: PackageFileRow[] = PKG_DEFAULT_PATHS.map((path) => ({
    path,
    content: byPath.get(path) || defaults[path],
  }));
  for (const [path, content] of byPath) {
    if (!(PKG_DEFAULT_PATHS as readonly string[]).includes(path)) {
      ordered.push({ path, content });
    }
  }
  return ordered;
}

function defaultSpecsMd(
  kind: MediaKind,
  specs?: { aspectRatio?: string; durationSec?: string; clarity?: string }
): string {
  const ratio = specs?.aspectRatio || "9:16";
  const clarity = specs?.clarity || "1080p";
  const dur = specs?.durationSec || "30";
  if (kind === "image") {
    return [
      "- **pageCount**：1（默认单张；用户要多张再增加）",
      `- **aspectRatio**：${ratio}`,
      `- **clarity**：${clarity}`,
      "- **styleHint**：主体清晰、构图完整；按用户描述出一张图",
    ].join("\n");
  }
  if (kind === "text") {
    return [
      "- **sections**：大纲、正文、角色设定",
      "- **toneHint**：开跑时与用户确认语气与受众",
    ].join("\n");
  }
  if (kind === "audio") {
    return [
      "- **tracks**：BGM、SFX",
      "- **moodHint**：先锁定情绪与场景再写提示词",
    ].join("\n");
  }
  return [
    "- **clipCount**：4",
    `- **aspectRatio**：${ratio}`,
    `- **clarity**：${clarity}`,
    `- **durationSec**：${dur}`,
  ].join("\n");
}

function assembleMarkdown(input: {
  title: string;
  description: string;
  useCases: string;
  howToUse: string;
  outputContent: string;
  mediaKind: MediaKind;
  nodeRecipe?: SkillNodeRecipe | null;
  aspectRatio?: string;
  durationSec?: string;
  clarity?: string;
}): string {
  const name = input.title.trim() || "我的 Skill";
  const desc = input.description.trim() || name;
  const kind = input.mediaKind;
  const label = kindLabelCn(kind);
  const nodes = input.nodeRecipe?.nodes || [];
  const edges = input.nodeRecipe?.edges || [];
  const nodesMd =
    nodes
      .map((n, i) => {
        const kindCn =
          n.kind === "image"
            ? "图片"
            : n.kind === "video"
              ? "视频"
              : n.kind === "audio"
                ? "音频"
                : "文本";
        return `${i + 1}. **${n.label || n.key}**（${kindCn}，key=\`${n.key}\`）${n.hint ? `：${n.hint}` : ""}`;
      })
      .join("\n") || `- （保存时按「${label}」生成默认节点配方）`;
  const edgesMd =
    edges.length > 0
      ? edges.map((e) => `- \`${e.from}\` → \`${e.to}\``).join("\n")
      : "- （按类型默认连线）";
  const order =
    input.nodeRecipe?.orderNotes?.trim() ||
    "AI 先出方案请用户确认 → 同意后建节点连线并填内容 → 再问是否生成 → 用户说生成相关词后扣费生成。";
  const howDefault = `1. 选择本 Skill，填写内容（可加附件）后发送
2. 进入画布：AI 给出创作方案并询问你的建议
3. 你同意后：AI 新建节点、连线并填入各节点内容
4. AI 询问是否生成；你回复「生成/出图/全部生成」等后开始扣费生成`;
  return `---
name: ${name}
slug: draft
title: ${name}
description: ${desc.replace(/\n/g, " ")}
execution: agent_recipe
entryKind: mine
mediaKind: ${kind}
---

# ${name}

> **对助手**：可选配方，不是向导。每轮先读画布。  
> **必读配套**：\`references/flow.md\` · \`references/node-layout.md\` · \`references/generation.md\` · \`references/canvas-tools.md\`

${desc}

## Agent 必读

用中文说你要做什么，然后调工具。禁止向用户解释 tempId / canvasOps / 节点类型内部名。

## 使用场景

${input.useCases.trim() || `当用户需要「${name}」类能力（类型：${label}）时选用。`}

## 如何使用

${input.howToUse.trim() || howDefault}

## 输出内容

${input.outputContent.trim() || "（待填写：请说明页数/镜头、风格、尺寸与各节点产物）"}

## 创作规格

${defaultSpecsMd(kind, {
  aspectRatio: input.aspectRatio,
  durationSec: input.durationSec,
  clarity: input.clarity,
})}

## 画布节点与连线

${nodesMd}

### 连线

${edgesMd}

## 编排顺序

${order}

## AI 操控流程（强制）

1. 读 Skill 与技能包 references
2. 出方案：结合用户输入给出可执行方案，用选项请用户确认
3. 用户同意后：创建/更新节点、连线、填入可执行 content/prompt
4. 询问是否开始生成
5. 用户明确同意生成后再提交生成任务

## 对助手的约束

- 产出类型固定为 **${label}**（mediaKind=\`${kind}\`）
- 严格按节点清单与 flow.md 执行；用户确认方案前不要批量改画布
- 节点编排完成后必须再问是否生成；仅当用户表达生成意愿时才扣费生成
`;
}

function FieldLabel({
  children,
  required,
}: {
  children: React.ReactNode;
  required?: boolean;
}) {
  return (
    <span className="csd-label">
      {children}
      {required ? <i aria-hidden>*</i> : null}
    </span>
  );
}

export function CreateSkillFromPromptDialog({
  open,
  onClose,
  onCreated,
  initialPrompt = "",
}: Props) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [useCases, setUseCases] = useState("");
  const [howToUse, setHowToUse] = useState("");
  const [outputContent, setOutputContent] = useState("");
  const [mediaKind, setMediaKind] = useState<MediaKind>("image");
  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [durationSec, setDurationSec] = useState("30");
  const [clarity, setClarity] = useState("1080p");
  const [nodeRecipe, setNodeRecipe] = useState<SkillNodeRecipe | null>(null);
  const [docMarkdown, setDocMarkdown] = useState("");
  const [codeDirty, setCodeDirty] = useState(false);
  const [coverUrl, setCoverUrl] = useState("");
  const [contentTab, setContentTab] = useState<ContentTab>("preview");
  const [aiHint, setAiHint] = useState("");
  /** 平常默认折叠；有 initialPrompt 时自动展开 */
  const [aiExpanded, setAiExpanded] = useState(false);
  /** AI 生成成功后高亮「可继续编辑」分区 */
  const [aiFilled, setAiFilled] = useState(false);
  // 我的技能默认技能包 + 精细画布操控
  const [executionMode, setExecutionMode] =
    useState<SkillExecutionMode>("canvas_manual");
  const [canvasRulesMarkdown, setCanvasRulesMarkdown] = useState("");
  const [packageFileRows, setPackageFileRows] = useState<PackageFileRow[]>([]);
  const [flowSteps, setFlowSteps] = useState<SkillFlowStep[]>(() =>
    defaultFlowSteps("image")
  );
  const [activePkgPath, setActivePkgPath] = useState<string>("SKILL.md");
  const [detailsExpanded, setDetailsExpanded] = useState(false);
  const [flowExpanded, setFlowExpanded] = useState(false);
  const [pkgExpanded, setPkgExpanded] = useState(false);
  const [advExpanded, setAdvExpanded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [uploadingCover, setUploadingCover] = useState(false);
  const [importingZip, setImportingZip] = useState(false);
  const coverInputRef = useRef<HTMLInputElement>(null);
  const mdInputRef = useRef<HTMLInputElement>(null);
  const zipInputRef = useRef<HTMLInputElement>(null);
  const aiInputRef = useRef<HTMLTextAreaElement>(null);
  const basicsRef = useRef<HTMLElement | null>(null);
  const queryClient = useQueryClient();

  const { data: agentPricing } = useQuery({
    queryKey: ["credits", "agent-skill-pricing", "skill-ai-fill"],
    queryFn: () => getAgentSkillPricing(),
    enabled: open,
    staleTime: 30_000,
  });
  const skillAiFillCost = Math.max(
    0,
    Number(agentPricing?.skillAiFillQuote?.total ?? agentPricing?.skillAiFill ?? 0) || 0
  );
  const creditsEnabled = agentPricing?.creditsEnabled !== false;
  const showFillCredit = creditsEnabled && skillAiFillCost > 0;
  const fillCreditLabel = showFillCredit
    ? formatCreditLabel(skillAiFillCost, creditsEnabled)
    : null;

  useEffect(() => {
    if (!open) return;
    setTitle("");
    setDescription("");
    setUseCases("");
    setHowToUse("");
    setOutputContent("");
    setMediaKind("image");
    setAspectRatio("9:16");
    setDurationSec("30");
    setClarity("1080p");
    setNodeRecipe(null);
    setDocMarkdown("");
    setCodeDirty(false);
    setCoverUrl("");
    setContentTab("preview");
    setExecutionMode("canvas_manual");
    setCanvasRulesMarkdown("");
    setFlowSteps(defaultFlowSteps("image"));
    setPackageFileRows(
      buildDefaultPackageRows({
        title: "",
        description: "",
        mediaKind: "image",
        outputContent: "",
        nodeRecipe: null,
        flowSteps: defaultFlowSteps("image"),
      })
    );
    setActivePkgPath("SKILL.md");
    setDetailsExpanded(false);
    setFlowExpanded(false);
    setPkgExpanded(false);
    setAdvExpanded(false);
    setAiFilled(false);
    const hint = (initialPrompt || "").trim().slice(0, 500);
    setAiHint(hint);
    setAiExpanded(hint.length > 0);
  }, [open, initialPrompt]);

  useEffect(() => {
    if (!open || !aiExpanded) return;
    const t = window.setTimeout(() => aiInputRef.current?.focus(), 80);
    return () => window.clearTimeout(t);
  }, [open, aiExpanded]);

  const liveMarkdown = useMemo(() => {
    if (codeDirty && docMarkdown.trim()) return docMarkdown;
    return assembleMarkdown({
      title,
      description,
      useCases,
      howToUse,
      outputContent,
      mediaKind,
      nodeRecipe,
      aspectRatio,
      durationSec,
      clarity,
    });
  }, [
    codeDirty,
    docMarkdown,
    title,
    description,
    useCases,
    howToUse,
    outputContent,
    mediaKind,
    nodeRecipe,
    aspectRatio,
    durationSec,
    clarity,
  ]);

  const activePkgContent = useMemo(() => {
    if (activePkgPath === "SKILL.md") return liveMarkdown;
    return packageFileRows.find((r) => r.path === activePkgPath)?.content || "";
  }, [activePkgPath, liveMarkdown, packageFileRows]);

  if (!open) return null;

  const refreshPackageFromFields = (next?: {
    kind?: MediaKind;
    steps?: SkillFlowStep[];
    recipe?: SkillNodeRecipe | null;
  }) => {
    const kind = next?.kind ?? mediaKind;
    const steps = next?.steps ?? flowSteps;
    const recipe = next?.recipe === undefined ? nodeRecipe : next.recipe;
    setPackageFileRows(
      buildDefaultPackageRows({
        title,
        description,
        mediaKind: kind,
        outputContent,
        nodeRecipe: recipe,
        flowSteps: steps,
        existing: packageFileRows,
      })
    );
  };

  const syncCodeFromFields = () => {
    setDocMarkdown(
      assembleMarkdown({
        title,
        description,
        useCases,
        howToUse,
        outputContent,
        mediaKind,
        nodeRecipe,
        aspectRatio,
        durationSec,
        clarity,
      })
    );
    setCodeDirty(false);
  };

  const handleAiFill = async () => {
    const hint = aiHint.trim() || description.trim() || title.trim();
    if (hint.length < 2) {
      toast.error("请先用一句话描述你想要的技能");
      aiInputRef.current?.focus();
      return;
    }
    setDrafting(true);
    try {
      const draft = await draftSkillFromPrompt(hint, mediaKind);
      setTitle(draft.title || "");
      setDescription(draft.description || "");
      setUseCases(draft.useCases || "");
      setHowToUse(draft.howToUse || "");
      setOutputContent(draft.outputContent || "");
      const kindRaw = String(draft.mediaKind || mediaKind || "").toLowerCase();
      const kind: MediaKind =
        kindRaw === "video" ||
        kindRaw === "text" ||
        kindRaw === "audio" ||
        kindRaw === "image"
          ? kindRaw
          : mediaKind;
      setMediaKind(kind);
      if (draft.aspectRatio === "9:16" || draft.aspectRatio === "16:9" || draft.aspectRatio === "1:1") {
        setAspectRatio(draft.aspectRatio);
      }
      const dur = String(draft.durationSec || "").trim();
      if (dur && ["15", "30", "45", "60"].includes(dur)) setDurationSec(dur);
      const clar = String(draft.clarity || "").trim();
      if (clar === "720p" || clar === "1080p") setClarity(clar);
      setNodeRecipe(draft.nodeRecipe || null);
      setDocMarkdown(draft.docMarkdown || "");
      setCodeDirty(Boolean(draft.docMarkdown));
      const draftMode =
        (draft.executionMode as SkillExecutionMode) || "canvas_manual";
      const draftRules = draft.canvasRulesMarkdown || "";
      const steps =
        draft.flowSteps && draft.flowSteps.length > 0
          ? draft.flowSteps.map((s) => ({
              title: String(s.title || "").trim(),
              note: String(s.note || "").trim(),
            }))
          : defaultFlowSteps(kind);
      setFlowSteps(steps);
      const draftFileRows = packageFilesToRows(draft.packageFiles);
      setPackageFileRows(
        buildDefaultPackageRows({
          title: draft.title || "",
          description: draft.description || "",
          mediaKind: kind,
          outputContent: draft.outputContent || "",
          nodeRecipe: draft.nodeRecipe || null,
          flowSteps: steps,
          existing: draftFileRows,
        })
      );
      setExecutionMode(draftMode);
      setCanvasRulesMarkdown(draftRules);
      setActivePkgPath("SKILL.md");
      setAiFilled(true);
      setDetailsExpanded(true);
      setFlowExpanded(true);
      setPkgExpanded(true);
      if (draftRules.trim()) setAdvExpanded(true);
      toast.success("技能包已生成，可继续改流程后保存");
      void queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
      window.setTimeout(() => {
        basicsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 60);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "AI 创建失败");
    } finally {
      setDrafting(false);
    }
  };

  const handleCover = async (file: File | null) => {
    if (!file) return;
    /* 校验封面必须为 9:16 竖版比例（允许 ±5% 偏差） */
    const ok = await validateImageAspectRatio(file, 9, 16).catch(() => false);
    if (!ok) {
      toast.error("封面须为 9:16 竖版比例（宽:高 = 9:16），请重新选择");
      return;
    }
    setUploadingCover(true);
    try {
      const res = await uploadSkillCover(file);
      setCoverUrl(res.imageUrl);
      toast.success("封面已上传");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "封面上传失败");
    } finally {
      setUploadingCover(false);
    }
  };

  const handleMdUpload = async (file: File | null) => {
    if (!file) return;
    try {
      const text = await file.text();
      if (!text.trim()) {
        toast.error("MD 文件为空");
        return;
      }
      if (activePkgPath === "SKILL.md") {
        setDocMarkdown(text);
        setCodeDirty(true);
      } else {
        setPackageFileRows((rows) =>
          rows.map((r) =>
            r.path === activePkgPath ? { ...r, content: text } : r
          )
        );
      }
      setContentTab("code");
      toast.success(`已载入 ${file.name} → ${activePkgPath}`);
    } catch {
      toast.error("读取 MD 失败");
    }
  };

  /** Codex / Agent Skills zip：只入库 markdown，scripts 记入 unsupportedFiles */
  const handleZipImport = async (file: File | null) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) {
      toast.error("请选择 .zip 技能包");
      return;
    }
    setImportingZip(true);
    try {
      const res = await importSkillPackage(file);
      const unsupported = res.unsupportedFiles?.length
        ? `；已忽略 ${res.unsupportedFiles.length} 个脚本/非 md`
        : "";
      toast.success(`已导入「${res.title || res.slug}」${unsupported}`);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      onCreated?.(res);
      onClose();
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.message : "导入失败，请检查 zip 内是否有 SKILL.md";
      toast.error(msg);
    } finally {
      setImportingZip(false);
      if (zipInputRef.current) zipInputRef.current.value = "";
    }
  };

  const handleSave = async () => {
    if (!title.trim()) {
      toast.error("请填写 Skill 名称");
      return;
    }
    if (description.trim().length < 2) {
      toast.error("请填写一句话介绍");
      return;
    }
    if (useCases.trim().length < 2) {
      setDetailsExpanded(true);
      toast.error("请填写使用场景");
      return;
    }
    if (howToUse.trim().length < 2) {
      setDetailsExpanded(true);
      toast.error("请填写如何使用");
      return;
    }
    if (outputContent.trim().length < 2) {
      setDetailsExpanded(true);
      toast.error("请填写输出内容");
      return;
    }
    const steps = flowSteps
      .map((s) => ({
        title: s.title.trim(),
        note: (s.note || "").trim(),
      }))
      .filter((s) => s.title);
    // 流程选填：未写或删空时用类型默认步骤写入技能包，不拦截保存
    const stepsForPackage = steps.length > 0 ? steps : defaultFlowSteps(mediaKind);
    setSubmitting(true);
    try {
      const pkgRows = buildDefaultPackageRows({
        title: title.trim(),
        description: description.trim(),
        mediaKind,
        outputContent: outputContent.trim(),
        nodeRecipe,
        flowSteps: stepsForPackage,
        existing: packageFileRows,
      });
      const skill = await createSkillFromForm({
        title: title.trim(),
        description: description.trim(),
        useCases: useCases.trim(),
        howToUse: howToUse.trim(),
        outputContent: outputContent.trim(),
        mediaKind,
        docMarkdown: liveMarkdown,
        coverUrl: coverUrl || undefined,
        idea: useCases.trim().slice(0, 500),
        aspectRatio:
          mediaKind === "image" || mediaKind === "video" ? aspectRatio : undefined,
        durationSec: mediaKind === "video" ? durationSec : undefined,
        clarity:
          mediaKind === "image" || mediaKind === "video" ? clarity : undefined,
        nodeRecipe: nodeRecipe || undefined,
        executionMode: executionMode || "canvas_manual",
        canvasRulesMarkdown: canvasRulesMarkdown.trim() || undefined,
        packageFiles: packageFilesToDict(pkgRows),
        flowSteps: steps.length > 0 ? steps : undefined,
      });
      toast.success(`已保存技能包「${skill.title}」`);
      onCreated?.(skill);
      onClose();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "保存失败");
    } finally {
      setSubmitting(false);
    }
  };

  const updateFlowStep = (idx: number, patch: Partial<SkillFlowStep>) => {
    const next = flowSteps.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    setFlowSteps(next);
    refreshPackageFromFields({ steps: next });
  };

  const applyMediaKind = (id: MediaKind) => {
    const steps = defaultFlowSteps(id);
    setMediaKind(id);
    setNodeRecipe(null);
    setCodeDirty(false);
    setFlowSteps(steps);
    setPackageFileRows(
      buildDefaultPackageRows({
        title,
        description,
        mediaKind: id,
        outputContent,
        nodeRecipe: null,
        flowSteps: steps,
      })
    );
  };

  return (
    <div
      className="create-skill-dialog-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-skill-form-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div className="create-skill-dialog">
        <div className="csd-glow" aria-hidden />

        <header className="csd-header">
          <div className="csd-header-copy">
            <p className="csd-kicker">Skill Package</p>
            <h2 id="create-skill-form-title">创建技能包</h2>
            <p className="csd-sub">
              用一句话生成流程与参考文件；开跑后助手按包在画布上黑盒执行，产物进资产
            </p>
          </div>
          <div className="csd-header-actions">
            <button
              type="button"
              className="csd-btn-save"
              disabled={submitting || drafting}
              onClick={() => void handleSave()}
            >
              {submitting ? <Loader2 size={15} className="animate-spin" /> : null}
              保存技能包
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
          {/* AI 创建 · 平常折叠；展开后：选类型 → 一句话 → 生成 */}
          <section
            className={cn("csd-ai-hero", aiExpanded && "is-open")}
            aria-label="AI 创建"
          >
            <button
              type="button"
              className="csd-ai-hero-toggle"
              aria-expanded={aiExpanded}
              disabled={submitting || drafting}
              onClick={() => setAiExpanded((v) => !v)}
            >
              <span className="csd-ai-hero-toggle-main">
                <Sparkles size={14} strokeWidth={2} aria-hidden />
                <strong>可选 · AI 创建</strong>
                {showFillCredit ? (
                  <span className="csd-ai-credit-chip" title={fillCreditLabel ?? undefined}>
                    <Zap size={11} strokeWidth={2.2} fill="currentColor" aria-hidden />
                    <span className="tabular-nums">{skillAiFillCost}</span>
                  </span>
                ) : null}
              </span>
              <span className="csd-ai-hero-toggle-meta">
                <span className="csd-ai-hero-toggle-hint">
                  {aiExpanded ? "收起" : "展开后选类型并用一句话生成技能包"}
                </span>
                <ChevronDown
                  size={16}
                  className={cn("csd-ai-chevron", aiExpanded && "is-open")}
                  aria-hidden
                />
              </span>
            </button>

            {aiExpanded ? (
              <div className="csd-ai-hero-body">
                <p className="csd-ai-hero-desc">
                  先选产出类型，再用一句话描述目标；将生成名称、流程步骤与 SKILL.md / references
                </p>

                <div className="csd-ai-hero-types" role="group" aria-label="产出类型">
                  {(
                    [
                      { id: "image" as const, label: "生图", hint: "单张出图" },
                      { id: "video" as const, label: "短视频", hint: "成片编排" },
                      { id: "text" as const, label: "文本", hint: "文案 / 剧本" },
                      { id: "audio" as const, label: "音频", hint: "配乐 / 音效" },
                    ] as const
                  ).map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      className={cn("csd-type-chip", mediaKind === opt.id && "active")}
                      disabled={submitting || drafting}
                      onClick={() => applyMediaKind(opt.id)}
                    >
                      <strong>{opt.label}</strong>
                      <span>{opt.hint}</span>
                    </button>
                  ))}
                </div>

                <label className="csd-ai-hero-prompt">
                  <span className="csd-label">一句话描述</span>
                  <textarea
                    ref={aiInputRef}
                    className="csd-textarea csd-ai-prompt"
                    value={aiHint}
                    maxLength={500}
                    rows={3}
                    placeholder={
                      mediaKind === "video"
                        ? "例如：产品电影级竖屏宣传片，15 秒，先故事板再出片"
                        : mediaKind === "image"
                          ? "例如：日系插画风角色立绘，竖屏一张"
                          : mediaKind === "text"
                            ? "例如：短视频口播脚本，带钩子与 CTA"
                            : "例如：轻松 BGM + 轻点音效，配竖屏短片"
                    }
                    disabled={submitting || drafting}
                    onChange={(e) => setAiHint(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        void handleAiFill();
                      }
                    }}
                  />
                </label>

                <div className="csd-ai-hero-actions">
                  <button
                    type="button"
                    className="csd-btn-ai csd-btn-ai-primary"
                    disabled={submitting || drafting}
                    onClick={() => void handleAiFill()}
                  >
                    {drafting ? (
                      <Loader2 size={15} className="animate-spin" />
                    ) : (
                      <Sparkles size={14} />
                    )}
                    <span className="csd-btn-ai-label">
                      {drafting ? "生成中…" : aiFilled ? "重新生成" : "生成技能包"}
                    </span>
                    {showFillCredit ? (
                      <span className="csd-btn-ai-cost" title={fillCreditLabel ?? undefined}>
                        <Zap size={12} strokeWidth={2.2} fill="currentColor" aria-hidden />
                        <span className="tabular-nums">{skillAiFillCost}</span>
                      </span>
                    ) : null}
                  </button>
                  <span className="csd-ai-hero-hint">
                    {showFillCredit
                      ? `每次生成消耗 ${skillAiFillCost} 算力 · Ctrl/⌘+Enter`
                      : "也可跳过，直接在下方手动填写 · Ctrl/⌘+Enter"}
                  </span>
                </div>

                {aiFilled ? (
                  <p className="csd-ai-filled-banner" role="status">
                    已生成草稿：请核对名称与流程，确认无误后点「保存技能包」
                  </p>
                ) : null}
              </div>
            ) : null}
          </section>

          <section
            ref={basicsRef}
            className={cn("csd-section", aiFilled && "csd-section-ready")}
          >
            <div className="csd-section-label-row">
              <span className="csd-section-label">基础信息</span>
            </div>
            {!aiExpanded ? (
              <div className="csd-field" style={{ marginBottom: 14 }}>
                <FieldLabel required>选择类型</FieldLabel>
                <p className="csd-hint" style={{ marginTop: 4 }}>
                  决定默认流程与开跑时建哪些节点
                </p>
                <div className="csd-type-row">
                  {(
                    [
                      { id: "image" as const, label: "生图", hint: "单张出图" },
                      { id: "video" as const, label: "短视频", hint: "成片编排" },
                      { id: "text" as const, label: "文本", hint: "文案 / 剧本" },
                      { id: "audio" as const, label: "音频", hint: "配乐 / 音效" },
                    ] as const
                  ).map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      className={cn("csd-type-chip", mediaKind === opt.id && "active")}
                      disabled={submitting || drafting}
                      onClick={() => applyMediaKind(opt.id)}
                    >
                      <strong>{opt.label}</strong>
                      <span>{opt.hint}</span>
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="csd-grid-2">
              <label className="csd-field">
                <FieldLabel required>Skill 名称</FieldLabel>
                <input
                  className="csd-input"
                  value={title}
                  maxLength={64}
                  placeholder="例如：文本一键转漫画"
                  disabled={submitting}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label className="csd-field">
                <FieldLabel required>一句话介绍</FieldLabel>
                <input
                  className="csd-input"
                  value={description}
                  maxLength={200}
                  placeholder="一句话说明这个 Skill 做什么"
                  disabled={submitting}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
            </div>
          </section>

          <section className={cn("csd-fold csd-flow-section", flowExpanded && "is-open")}>
            <button
              type="button"
              className="csd-fold-toggle"
              aria-expanded={flowExpanded}
              disabled={submitting}
              onClick={() => setFlowExpanded((v) => !v)}
            >
              <span className="csd-fold-toggle-main">
                <span>流程设置</span>
                <span className="csd-fold-opt">选填</span>
              </span>
              <span className="csd-fold-meta">
                <span className="csd-fold-hint">
                  {flowExpanded
                    ? "收起"
                    : flowSteps.some((s) => s.title.trim())
                      ? `${flowSteps.filter((s) => s.title.trim()).length} 步 · 可不写`
                      : "可不写；未写则保存时用类型默认流程"}
                </span>
                <ChevronDown
                  size={16}
                  className={cn("csd-ai-chevron", flowExpanded && "is-open")}
                  aria-hidden
                />
              </span>
            </button>
            {flowExpanded ? (
              <div className="csd-fold-panel">
                <div className="csd-section-head" style={{ marginBottom: 8 }}>
                  <p className="csd-hint" style={{ margin: 0 }}>
                    可自定义助手步骤并写入 <code>references/flow.md</code>；清空则保存时按类型补默认
                  </p>
                  <button
                    type="button"
                    className="csd-btn-ghost csd-flow-add"
                    disabled={submitting || flowSteps.length >= 12}
                    onClick={() => {
                      const next = [
                        ...flowSteps,
                        { title: `步骤 ${flowSteps.length + 1}`, note: "" },
                      ];
                      setFlowSteps(next);
                      refreshPackageFromFields({ steps: next });
                    }}
                  >
                    <Plus size={13} strokeWidth={2} />
                    添加步骤
                  </button>
                </div>
                {flowSteps.length === 0 ? (
                  <p className="csd-flow-empty">暂无步骤。可添加，或不写直接保存。</p>
                ) : (
                  <ol className="csd-flow-list">
                    {flowSteps.map((step, idx) => (
                      <li key={idx} className="csd-flow-item">
                        <span className="csd-flow-idx" aria-hidden>
                          {idx + 1}
                        </span>
                        <div className="csd-flow-fields">
                          <input
                            className="csd-input"
                            value={step.title}
                            maxLength={64}
                            placeholder="步骤标题，如：出方案并确认"
                            disabled={submitting}
                            onChange={(e) => updateFlowStep(idx, { title: e.target.value })}
                          />
                          <input
                            className="csd-input"
                            value={step.note || ""}
                            maxLength={500}
                            placeholder="要点 / 工具提示，如：ask_user 确认后再改画布"
                            disabled={submitting}
                            onChange={(e) => updateFlowStep(idx, { note: e.target.value })}
                          />
                        </div>
                        <button
                          type="button"
                          className="csd-btn-icon csd-flow-remove"
                          disabled={submitting}
                          aria-label="删除步骤"
                          onClick={() => {
                            const next = flowSteps.filter((_, i) => i !== idx);
                            setFlowSteps(next);
                            refreshPackageFromFields({ steps: next });
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                      </li>
                    ))}
                  </ol>
                )}
                {flowSteps.length > 0 ? (
                  <div className="csd-flow-footer">
                    <button
                      type="button"
                      className="csd-btn-ghost"
                      disabled={submitting}
                      onClick={() => {
                        setFlowSteps([]);
                        refreshPackageFromFields({ steps: [] });
                      }}
                    >
                      清空步骤
                    </button>
                    <button
                      type="button"
                      className="csd-btn-ghost"
                      disabled={submitting}
                      onClick={() => {
                        const steps = defaultFlowSteps(mediaKind);
                        setFlowSteps(steps);
                        refreshPackageFromFields({ steps });
                      }}
                    >
                      恢复类型默认
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>

          <section className={cn("csd-fold", detailsExpanded && "is-open")}>
            <button
              type="button"
              className="csd-fold-toggle"
              aria-expanded={detailsExpanded}
              disabled={submitting}
              onClick={() => setDetailsExpanded((v) => !v)}
            >
              <span className="csd-fold-toggle-main">
                <span>完善说明</span>
                <span className="csd-fold-req">必填</span>
              </span>
              <span className="csd-fold-meta">
                <span className="csd-fold-hint">
                  {detailsExpanded ? "收起" : "使用场景 · 如何使用 · 输出内容"}
                </span>
                <ChevronDown
                  size={16}
                  className={cn("csd-ai-chevron", detailsExpanded && "is-open")}
                  aria-hidden
                />
              </span>
            </button>
            {detailsExpanded ? (
              <div className="csd-fold-panel">
                <div className="csd-stack">
                  <label className="csd-field">
                    <FieldLabel required>使用场景</FieldLabel>
                    <textarea
                      className="csd-textarea"
                      value={useCases}
                      maxLength={2000}
                      placeholder="什么时候应该触发本 Skill…"
                      disabled={submitting}
                      onChange={(e) => setUseCases(e.target.value)}
                    />
                  </label>
                  <label className="csd-field">
                    <FieldLabel required>如何使用</FieldLabel>
                    <textarea
                      className="csd-textarea"
                      value={howToUse}
                      maxLength={2000}
                      placeholder="逐步说明用户如何使用…"
                      disabled={submitting}
                      onChange={(e) => setHowToUse(e.target.value)}
                    />
                  </label>
                  <label className="csd-field">
                    <FieldLabel required>输出内容</FieldLabel>
                    <textarea
                      className="csd-textarea"
                      value={outputContent}
                      maxLength={2000}
                      placeholder="最终产出什么结果…"
                      disabled={submitting}
                      onChange={(e) => setOutputContent(e.target.value)}
                    />
                  </label>
                </div>
              </div>
            ) : null}
          </section>

          <section className={cn("csd-fold", pkgExpanded && "is-open")}>
            <button
              type="button"
              className="csd-fold-toggle"
              aria-expanded={pkgExpanded}
              disabled={submitting}
              onClick={() => setPkgExpanded((v) => !v)}
            >
              <span className="csd-fold-toggle-main">
                <FolderOpen size={14} strokeWidth={2} />
                <span>技能包文件</span>
              </span>
              <span className="csd-fold-meta">
                <span className="csd-fold-hint">
                  {pkgExpanded ? "收起" : "SKILL.md · references/*"}
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
                <div className="csd-section-head" style={{ marginBottom: 10 }}>
                  <FieldLabel>预览 / 编辑</FieldLabel>
                  <div className="csd-tabs" role="tablist" aria-label="Skill 内容视图">
                    {(
                      [
                        { id: "preview" as const, label: "预览", icon: Eye },
                        { id: "code" as const, label: "代码", icon: Code2 },
                        { id: "upload" as const, label: "上传", icon: FileUp },
                      ] as const
                    ).map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        role="tab"
                        aria-selected={contentTab === tab.id}
                        className={cn("csd-tab", contentTab === tab.id && "active")}
                        onClick={() => {
                          if (tab.id === "code" && activePkgPath === "SKILL.md" && !codeDirty) {
                            syncCodeFromFields();
                          }
                          setContentTab(tab.id);
                        }}
                      >
                        <tab.icon size={13} strokeWidth={2} />
                        {tab.label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="csd-md-shell">
                  <aside className="csd-tree">
                    <div className="csd-tree-caption">
                      <FolderOpen size={12} strokeWidth={2} />
                      技能包
                    </div>
                    <button
                      type="button"
                      className={cn(
                        "csd-tree-file",
                        activePkgPath === "SKILL.md" && "is-active"
                      )}
                      onClick={() => setActivePkgPath("SKILL.md")}
                    >
                      <span className="csd-tree-dot" />
                      SKILL.md
                    </button>
                    <div className="csd-tree-folder">references/</div>
                    {packageFileRows.map((row) => (
                      <button
                        key={row.path}
                        type="button"
                        className={cn(
                          "csd-tree-file csd-tree-file-nested",
                          activePkgPath === row.path && "is-active"
                        )}
                        onClick={() => setActivePkgPath(row.path)}
                        title={row.path}
                      >
                        <span className="csd-tree-dot" />
                        {row.path.replace(/^references\//, "")}
                      </button>
                    ))}
                  </aside>
                  <div className="csd-md-panel">
                    {contentTab === "preview" ? (
                      <pre className="csd-md-preview">{activePkgContent}</pre>
                    ) : null}
                    {contentTab === "code" ? (
                      <textarea
                        className="csd-md-code"
                        value={
                          activePkgPath === "SKILL.md"
                            ? codeDirty
                              ? docMarkdown
                              : liveMarkdown
                            : activePkgContent
                        }
                        disabled={submitting}
                        spellCheck={false}
                        onChange={(e) => {
                          const val = e.target.value;
                          if (activePkgPath === "SKILL.md") {
                            setDocMarkdown(val);
                            setCodeDirty(true);
                          } else {
                            setPackageFileRows((rows) =>
                              rows.map((r) =>
                                r.path === activePkgPath
                                  ? { ...r, content: val }
                                  : r
                              )
                            );
                          }
                        }}
                      />
                    ) : null}
                    {contentTab === "upload" ? (
                      <div className="csd-md-upload">
                        <div className="csd-md-upload-icon">
                          <FileUp size={22} strokeWidth={1.6} />
                        </div>
                        <p>上传本地 MD，覆盖当前选中文件（{activePkgPath}）</p>
                        <input
                          ref={mdInputRef}
                          type="file"
                          accept=".md,text/markdown,text/plain"
                          className="hidden"
                          onChange={(e) =>
                            void handleMdUpload(e.target.files?.[0] ?? null)
                          }
                        />
                        <button
                          type="button"
                          className="csd-btn-ghost"
                          onClick={() => mdInputRef.current?.click()}
                        >
                          选择 MD 文件
                        </button>
                        <p className="mt-3 text-[11px] text-muted-foreground">
                          或导入 Codex / Agent Skills 风格 zip（SKILL.md + references；不执行 scripts）
                        </p>
                        <input
                          ref={zipInputRef}
                          type="file"
                          accept=".zip,application/zip"
                          className="hidden"
                          onChange={(e) =>
                            void handleZipImport(e.target.files?.[0] ?? null)
                          }
                        />
                        <button
                          type="button"
                          className="csd-btn-ghost"
                          disabled={importingZip || submitting}
                          onClick={() => zipInputRef.current?.click()}
                        >
                          {importingZip ? "导入中…" : "导入技能包 zip"}
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            ) : null}
          </section>

          {nodeRecipe?.nodes?.length ? (
            <section className="csd-section">
              <FieldLabel>推荐节点 / 编排顺序</FieldLabel>
              <div className="csd-recipe-box">
                <p className="csd-recipe-summary">{recipeSummaryText(nodeRecipe)}</p>
                <ol className="csd-recipe-list">
                  {nodeRecipe.nodes.map((n) => (
                    <li key={n.key}>
                      <strong>{n.label || n.key}</strong>
                      <span className="csd-recipe-kind">{n.kind}</span>
                      {n.hint ? <em>{n.hint}</em> : null}
                    </li>
                  ))}
                </ol>
                {nodeRecipe.orderNotes ? (
                  <p className="csd-recipe-notes">{nodeRecipe.orderNotes}</p>
                ) : null}
              </div>
            </section>
          ) : null}

          <section className={cn("csd-fold", advExpanded && "is-open")}>
            <button
              type="button"
              className="csd-fold-toggle"
              aria-expanded={advExpanded}
              disabled={submitting}
              onClick={() => setAdvExpanded((v) => !v)}
            >
              <span className="csd-fold-toggle-main">
                <span>高级与封面</span>
              </span>
              <span className="csd-fold-meta">
                <span className="csd-fold-hint">
                  {advExpanded ? "收起" : "执行模式 · 画布规则 · 封面"}
                </span>
                <ChevronDown
                  size={16}
                  className={cn("csd-ai-chevron", advExpanded && "is-open")}
                  aria-hidden
                />
              </span>
            </button>
            {advExpanded ? (
              <div className="csd-fold-panel">
                <p className="csd-ai-tip">
                  默认「精细画布操控」按技能包 references 执行（对齐平台技能包）。
                </p>
                {(mediaKind === "image" || mediaKind === "video") ? (
                  <div className="csd-grid-2" style={{ marginBottom: 10 }}>
                    <label className="csd-field">
                      <FieldLabel>默认画幅</FieldLabel>
                      <select
                        className="csd-input"
                        value={aspectRatio}
                        disabled={submitting}
                        onChange={(e) => setAspectRatio(e.target.value)}
                      >
                        <option value="9:16">9:16 竖屏</option>
                        <option value="16:9">16:9 横屏</option>
                        <option value="1:1">1:1 方形</option>
                      </select>
                    </label>
                    <label className="csd-field">
                      <FieldLabel>默认清晰度</FieldLabel>
                      <select
                        className="csd-input"
                        value={clarity}
                        disabled={submitting}
                        onChange={(e) => setClarity(e.target.value)}
                      >
                        <option value="1080p">1080p</option>
                        <option value="720p">720p</option>
                      </select>
                    </label>
                    {mediaKind === "video" ? (
                      <label className="csd-field">
                        <FieldLabel>成片参考总时长</FieldLabel>
                        <select
                          className="csd-input"
                          value={durationSec}
                          disabled={submitting}
                          onChange={(e) => setDurationSec(e.target.value)}
                        >
                          <option value="15">约 15 秒</option>
                          <option value="30">约 30 秒</option>
                          <option value="45">约 45 秒</option>
                          <option value="60">约 60 秒</option>
                        </select>
                      </label>
                    ) : null}
                  </div>
                ) : null}
                <label className="csd-field">
                  <FieldLabel>执行模式</FieldLabel>
                  <select
                    className="csd-input"
                    value={executionMode}
                    disabled={submitting}
                    onChange={(e) =>
                      setExecutionMode(e.target.value as SkillExecutionMode)
                    }
                  >
                    <option value="canvas_manual">精细画布操控（技能包，推荐）</option>
                    <option value="team">智能编排（按节点配方投影）</option>
                  </select>
                </label>
                <label className="csd-field" style={{ marginTop: 10 }}>
                  <FieldLabel>画布规则覆盖</FieldLabel>
                  <textarea
                    className="csd-textarea"
                    value={canvasRulesMarkdown}
                    maxLength={8000}
                    placeholder="该技能对 AI 操控画布的专属规则（选填）"
                    disabled={submitting}
                    onChange={(e) => setCanvasRulesMarkdown(e.target.value)}
                  />
                </label>
                <div className="csd-field" style={{ marginTop: 14 }}>
                  <FieldLabel>上传封面（选填）</FieldLabel>
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
                      <input
                        ref={coverInputRef}
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => void handleCover(e.target.files?.[0] ?? null)}
                      />
                      <button
                        type="button"
                        className="csd-btn-ghost"
                        disabled={submitting || uploadingCover}
                        onClick={() => coverInputRef.current?.click()}
                      >
                        {uploadingCover ? "上传中…" : coverUrl ? "更换封面" : "选择图片"}
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
