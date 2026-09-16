/**
 * 管理端供应商 code → 中文展示名（任务详情、列表等）。
 * 存库 / API 仍用英文 code；此处仅展示。
 */
const PROVIDER_DISPLAY_LABELS: Record<string, string> = {
  doubao: "火山方舟 · 豆包",
  ark: "火山方舟 · 即梦",
  deepseek: "DeepSeek",
  dashscope: "阿里云百炼",
  kling: "百炼 · 可灵",
  vidu: "Vidu 企业版",
  runninghub: "RunningHub · CN 站",
  ltx_runninghub: "RunningHub 海外版",
  nodyhub: "NodyHub",
  huahu: "华狐 AI",
  jumengai: "聚梦 AI 网关",
  comfyui: "ComfyUI 本地",
  openai: "OpenAI",
  qwen: "通义 / 百炼兼容",
  zhipu: "智谱",
  moonshot: "Moonshot",
};

/** 供应商标识转中文；未知 code 原样返回 */
export function providerDisplayLabel(provider?: string | null): string {
  const code = String(provider || "").trim();
  if (!code) return "—";
  return PROVIDER_DISPLAY_LABELS[code.toLowerCase()] || code;
}
