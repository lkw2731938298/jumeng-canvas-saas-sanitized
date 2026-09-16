/** Skill 目录 / 收藏 / 我的 Skill API */

import { apiFetch } from "./client";

/** 技能包能力对齐：多文件参考包，{相对路径: markdown 正文}，如 references/xxx.md */
export type SkillPackageFiles = Record<string, string>;

/** 技能执行模式：team=确定性模板投影（默认）；canvas_manual=对齐平台技能包的精细画布操控 */
export type SkillExecutionMode = "team" | "canvas_manual" | string;

/** 画布节点配方：确认后一键铺节点 */
export type SkillNodeRecipe = {
  nodes: Array<{
    key: string;
    kind: "text" | "image" | "video" | "audio" | string;
    label: string;
    hint?: string;
  }>;
  edges: Array<{ from: string; to: string }>;
  orderNotes: string;
};

/** 我的 Skill 另存时的可复用默认（想法 / 风格 / 模型 / 画幅等） */
export type SkillDefaults = {
  idea?: string;
  genMode?: string;
  /** image=生图（默认单张）；video=短片成片 */
  mediaKind?: "image" | "video" | "text" | "audio" | string;
  styleId?: string;
  controllerModel?: string;
  imageModel?: string;
  videoModel?: string;
  aspectRatio?: string;
  clarity?: string;
  durationSec?: string;
  nodeRecipe?: SkillNodeRecipe;
};

export type SkillItem = {
  id: string;
  slug: string;
  title: string;
  description: string;
  category: string;
  visibility: string;
  ownerUserId?: string | null;
  /** 社区 Skill 作者展示名（display_name 或用户尾号） */
  ownerDisplayName?: string | null;
  coverUrl?: string | null;
  entryKind?: string | null;
  inputs?: unknown[];
  pipeline?: unknown[];
  /** 步骤摘要，如「将铺：剧本→角色→分镜×4」 */
  pipelineSummary?: string;
  /** 产出类型：image | video | text | audio */
  mediaKind?: string | null;
  /** 画布节点配方 */
  nodeRecipe?: SkillNodeRecipe | null;
  execution?: string | null;
  /** 技能包能力对齐：执行模式（team|canvas_manual） */
  executionMode?: SkillExecutionMode;
  /** 技能自定义的画布规则覆盖（与通用规则冲突时以此为准） */
  canvasRulesMarkdown?: string;
  /** 多文件参考包：{相对路径: markdown 正文}，与 SKILL.md 主文件合并整包注入 */
  packageFiles?: SkillPackageFiles;
  /** zip 导入时未入库的脚本/非 md 路径 */
  unsupportedFiles?: string[];
  /** 另存默认参数 */
  defaults?: SkillDefaults;
  defaultStyleId?: string | null;
  favorited?: boolean;
  sortOrder?: number;
  /** 另存版本；>1 表示已更新过 */
  version?: number;
  /** 是否存在站内 SKILL.md（skill_docs） */
  hasSkillDoc?: boolean;
  /** MD 摘要（列表用） */
  skillDocSummary?: string;
  /** 社区发布审核：none | pending | approved | rejected */
  reviewStatus?: string;
  reviewNote?: string | null;
};

/** Skill 说明文档（对齐 LibTV SKILL.md） */
export type SkillDoc = {
  slug: string;
  title: string;
  description: string;
  summary: string;
  markdown: string;
  pipeline?: Array<{ agent?: string; action?: string; trigger?: string; clientAction?: string }>;
  execution?: string;
  entryKind?: string;
};

export async function getSkillDoc(slug: string): Promise<SkillDoc> {
  return apiFetch<SkillDoc>(`/api/v1/skills/${encodeURIComponent(slug)}/doc`, {
    skipAuth: false,
  });
}

export type SkillsListResult = {
  items: SkillItem[];
  categories: string[];
};

export async function listSkills(category?: string): Promise<SkillsListResult> {
  const q = category && category !== "推荐" ? `?category=${encodeURIComponent(category)}` : "";
  // 后端 get_current_user_optional：无 token 可匿名；有 token 附带 favorited
  return apiFetch<SkillsListResult>(`/api/v1/skills${q}`, { skipAuth: false });
}

export async function listFavoriteSkills(): Promise<SkillItem[]> {
  const res = await apiFetch<{ items: SkillItem[] }>("/api/v1/skills/favorites");
  return res.items ?? [];
}

export async function listMySkills(): Promise<SkillItem[]> {
  const res = await apiFetch<{ items: SkillItem[] }>("/api/v1/skills/mine");
  return res.items ?? [];
}

export async function saveSkillFromSession(input: {
  sessionId: string;
  title?: string;
  description?: string;
}): Promise<SkillItem> {
  return apiFetch<SkillItem>("/api/v1/skills/from-session", {
    method: "POST",
    body: JSON.stringify({
      sessionId: input.sessionId,
      title: input.title,
      description: input.description,
    }),
  });
}

/** 一句话让 AI 生成「我的 Skill」 */
export async function createSkillFromPrompt(prompt: string): Promise<SkillItem> {
  return apiFetch<SkillItem>("/api/v1/skills/from-prompt", {
    method: "POST",
    body: JSON.stringify({ prompt }),
  });
}

/** AI 填充创建表单（不落库） */
export type SkillFlowStep = { title: string; note?: string };

export type SkillFormDraft = {
  title: string;
  description: string;
  useCases: string;
  howToUse: string;
  outputContent: string;
  mediaKind: string;
  aspectRatio?: string;
  durationSec?: string;
  clarity?: string;
  idea?: string;
  docMarkdown: string;
  pipelineSummary?: string;
  nodeRecipe?: SkillNodeRecipe;
  /** 技能包能力对齐：AI 判断的执行模式/画布规则覆盖/多文件参考包，供高级区预填 */
  executionMode?: SkillExecutionMode;
  canvasRulesMarkdown?: string;
  packageFiles?: SkillPackageFiles;
  /** 流程设置步骤（写入 references/flow.md） */
  flowSteps?: SkillFlowStep[];
};

export async function draftSkillFromPrompt(
  prompt: string,
  mediaKind?: "image" | "video" | "text" | "audio" | string
): Promise<SkillFormDraft> {
  return apiFetch<SkillFormDraft>("/api/v1/skills/draft-from-prompt", {
    method: "POST",
    body: JSON.stringify({
      prompt,
      ...(mediaKind ? { mediaKind } : {}),
    }),
  });
}

/** LibTV 风格表单保存「我的 Skill」 */
export async function createSkillFromForm(input: {
  title: string;
  description: string;
  useCases: string;
  howToUse: string;
  outputContent: string;
  mediaKind: "image" | "video" | "text" | "audio" | string;
  docMarkdown?: string;
  coverUrl?: string;
  idea?: string;
  aspectRatio?: string;
  durationSec?: string;
  clarity?: string;
  nodeRecipe?: SkillNodeRecipe;
  /** 技能包能力对齐：均可选，缺省不影响存量行为 */
  executionMode?: SkillExecutionMode;
  canvasRulesMarkdown?: string;
  packageFiles?: SkillPackageFiles;
  /** 流程设置：写入技能包 references/flow.md */
  flowSteps?: SkillFlowStep[];
}): Promise<SkillItem> {
  return apiFetch<SkillItem>("/api/v1/skills/from-form", {
    method: "POST",
    body: JSON.stringify({
      title: input.title,
      description: input.description,
      useCases: input.useCases,
      howToUse: input.howToUse,
      outputContent: input.outputContent,
      mediaKind: input.mediaKind,
      docMarkdown: input.docMarkdown,
      coverUrl: input.coverUrl,
      idea: input.idea,
      aspectRatio: input.aspectRatio,
      durationSec: input.durationSec,
      clarity: input.clarity,
      nodeRecipe: input.nodeRecipe,
      executionMode: input.executionMode,
      canvasRulesMarkdown: input.canvasRulesMarkdown,
      packageFiles: input.packageFiles,
      flowSteps: input.flowSteps,
    }),
  });
}

/** 确认后按配方铺到画布 */
export async function applySkillRecipe(input: {
  slug: string;
  projectId: string;
  sessionId?: string;
  force?: boolean;
}): Promise<{
  projectId: string;
  revision: number;
  canvasOps?: unknown[];
  nodeRecipe?: SkillNodeRecipe;
  pipelineSummary?: string;
  graph?: { revision?: number; canvasOps?: unknown[] };
}> {
  return apiFetch(`/api/v1/skills/${encodeURIComponent(input.slug)}/apply-recipe`, {
    method: "POST",
    body: JSON.stringify({
      projectId: input.projectId,
      sessionId: input.sessionId,
      force: Boolean(input.force),
    }),
  });
}

/** 上传我的 Skill 封面 */
export async function uploadSkillCover(
  file: File
): Promise<{ id: string; ossKey: string; imageUrl: string }> {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<{ id: string; ossKey: string; imageUrl: string }>(
    "/api/v1/skills/cover",
    {
      method: "POST",
      body: form,
    }
  );
}

/** 导入 Agent Skills / Codex 风格 zip（只吃 markdown；scripts → unsupportedFiles） */
export async function importSkillPackage(
  file: File,
  opts?: { dryRun?: boolean }
): Promise<SkillItem & {
  unsupportedFiles?: string[];
  importWarnings?: string[];
  agentSkillsName?: string;
  dryRun?: boolean;
  preview?: {
    name?: string;
    description?: string;
    unsupportedFiles?: string[];
    warnings?: string[];
  };
}> {
  const form = new FormData();
  form.append("file", file);
  const q = opts?.dryRun ? "?dryRun=true" : "";
  return apiFetch(`/api/v1/skills/import-package${q}`, {
    method: "POST",
    body: form,
  });
}

/** 所有者更新 Skill 封面 / 名称 / 分类（已发布则回待审） */
export async function updateSkillMeta(
  slug: string,
  input: {
    title?: string;
    coverUrl?: string | null;
    category?: string;
    clearCover?: boolean;
    /** 技能包能力对齐：创建后仍可追加编辑 */
    executionMode?: SkillExecutionMode;
    canvasRulesMarkdown?: string;
    packageFiles?: SkillPackageFiles;
    clearPackageFiles?: boolean;
  }
): Promise<SkillItem> {
  return apiFetch<SkillItem>(`/api/v1/skills/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    body: JSON.stringify({
      ...(input.title !== undefined ? { title: input.title } : {}),
      ...(input.coverUrl !== undefined && input.coverUrl !== null
        ? { coverUrl: input.coverUrl }
        : {}),
      ...(input.category !== undefined ? { category: input.category } : {}),
      ...(input.clearCover ? { clearCover: true } : {}),
      ...(input.executionMode !== undefined ? { executionMode: input.executionMode } : {}),
      ...(input.canvasRulesMarkdown !== undefined
        ? { canvasRulesMarkdown: input.canvasRulesMarkdown }
        : {}),
      ...(input.packageFiles !== undefined ? { packageFiles: input.packageFiles } : {}),
      ...(input.clearPackageFiles ? { clearPackageFiles: true } : {}),
    }),
  });
}

export async function archiveMySkill(slug: string): Promise<SkillItem> {
  return apiFetch<SkillItem>(`/api/v1/skills/${encodeURIComponent(slug)}`, {
    method: "DELETE",
  });
}

/** 提交社区发布（管理员审核通过后公开） */
export async function publishMySkill(slug: string): Promise<SkillItem> {
  return apiFetch<SkillItem>(`/api/v1/skills/${encodeURIComponent(slug)}/publish`, {
    method: "POST",
    body: "{}",
  });
}

/** 撤回公开或取消待审 */
export async function unpublishMySkill(slug: string): Promise<SkillItem> {
  return apiFetch<SkillItem>(`/api/v1/skills/${encodeURIComponent(slug)}/unpublish`, {
    method: "POST",
    body: "{}",
  });
}

export async function favoriteSkill(slug: string): Promise<SkillItem> {
  return apiFetch<SkillItem>(`/api/v1/skills/${encodeURIComponent(slug)}/favorite`, {
    method: "POST",
    body: "{}",
  });
}

export async function unfavoriteSkill(slug: string): Promise<SkillItem> {
  return apiFetch<SkillItem>(`/api/v1/skills/${encodeURIComponent(slug)}/favorite`, {
    method: "DELETE",
  });
}
