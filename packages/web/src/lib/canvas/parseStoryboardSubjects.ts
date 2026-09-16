import {
  enrichSubjectItem,
  newSubjectId,
  type StoryboardSubjectItem,
  type StoryboardSubjectsBundle,
} from "@/types/storyboard-subjects";

export interface ParseStoryboardSubjectsResult {
  subjects: StoryboardSubjectsBundle;
  warnings: string[];
}

function stripMarkdownFence(text: string): string {
  let trimmed = text.trim().replace(/^\uFEFF/, "");
  const fenced = trimmed.match(/^```(?:json)?\s*([\s\S]*?)```$/i);
  if (fenced?.[1]) return fenced[1].trim();
  if (trimmed.startsWith("```")) {
    trimmed = trimmed.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/i, "").trim();
  }
  return trimmed;
}

function tryParseJsonValue(text: string): unknown | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.parse(trimmed);
    } catch {
      /* fall through */
    }
  }
  const match = trimmed.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try {
    return JSON.parse(match[0]);
  } catch {
    return null;
  }
}

function tryParseJson(content: string): unknown | null {
  const candidates = new Set<string>();
  const raw = content.trim().replace(/^\uFEFF/, "");
  if (raw) candidates.add(raw);
  const unfenced = stripMarkdownFence(raw);
  if (unfenced) candidates.add(unfenced);
  for (const candidate of candidates) {
    const parsed = tryParseJsonValue(candidate);
    if (parsed != null) return parsed;
  }
  return null;
}

function pickField(row: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const val = row[key];
    if (val != null && String(val).trim()) return String(val).trim();
  }
  return "";
}

function mapSubjectRow(row: Record<string, unknown>, index: number): StoryboardSubjectItem {
  const name =
    pickField(row, ["name", "名称", "角色名", "场景名", "道具名", "title"]) || `主体${index}`;
  const extractPrompt = pickField(row, ["extractPrompt", "提示词", "prompt", "描述"]);
  return enrichSubjectItem({
    id: newSubjectId(),
    name,
    extractPrompt,
  });
}

function parseSubjectList(data: unknown): StoryboardSubjectItem[] {
  if (!Array.isArray(data)) return [];
  return data
    .filter((r) => r && typeof r === "object")
    .map((r, i) => mapSubjectRow(r as Record<string, unknown>, i + 1));
}

export function parseStoryboardSubjectsContent(content: string): ParseStoryboardSubjectsResult {
  const warnings: string[] = [];
  const trimmed = content.trim();
  if (!trimmed) {
    return { subjects: { roles: [], scenes: [], props: [] }, warnings: ["主体提取返回为空"] };
  }

  const json = tryParseJson(trimmed);
  if (!json || typeof json !== "object") {
    return { subjects: { roles: [], scenes: [], props: [] }, warnings: ["未识别到合法主体 JSON"] };
  }

  const obj = json as Record<string, unknown>;
  const subjects: StoryboardSubjectsBundle = {
    roles: parseSubjectList(obj.roles ?? obj.角色 ?? obj.characters),
    scenes: parseSubjectList(obj.scenes ?? obj.场景),
    props: parseSubjectList(obj.props ?? obj.道具 ?? obj.items),
  };

  const total = subjects.roles.length + subjects.scenes.length + subjects.props.length;
  if (total === 0) {
    warnings.push("JSON 中未找到 roles/scenes/props");
  }

  return { subjects, warnings };
}

export function mergeSubjectsPreservingIds(
  prev: StoryboardSubjectsBundle,
  next: StoryboardSubjectsBundle
): StoryboardSubjectsBundle {
  const mergeList = (oldList: StoryboardSubjectItem[], newList: StoryboardSubjectItem[]) => {
    const byName = new Map(oldList.map((item) => [item.name, item]));
    return newList.map((item) => {
      const old = byName.get(item.name);
      if (!old) return item;
      return {
        ...item,
        id: old.id,
        imageAssetId: old.imageAssetId,
        imageStatus: old.imageStatus,
        imageError: old.imageError,
        imageSourceHash: old.imageSourceHash,
        extractPrompt: item.extractPrompt || old.extractPrompt,
      };
    });
  };

  return {
    roles: mergeList(prev.roles, next.roles),
    scenes: mergeList(prev.scenes, next.scenes),
    props: mergeList(prev.props, next.props),
  };
}
