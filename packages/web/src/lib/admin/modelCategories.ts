/** Admin model tabs — aligned with canvas node categories and upstream providers */

export const NODE_MODEL_CATEGORIES = [
  { id: "text", label: "文本 LLM", description: "文本输入节点 · 豆包 / DeepSeek 等对话模型" },
  { id: "image", label: "图片生成", description: "文生图 / 图生图 · 即梦、万相、RunningHub" },
  { id: "video", label: "视频生成", description: "参考生 / 生图转视频 · Vidu、Seedance、万相、可灵等" },
  { id: "audio", label: "音频 / 语音", description: "语音合成 · CosyVoice（百炼）" },
  {
    id: "tool",
    label: "画布工具",
    description: "多角度、布光等工具节点专用图片模型（与图片节点独立）",
  },
] as const;

export const PROVIDER_GROUP_ORDER = [
  "火山方舟 · 豆包文本",
  "火山方舟 · 即梦",
  "DeepSeek",
  "百炼 · 万相 / HappyHorse / PixVerse / CosyVoice / 可灵",
  "Vidu 企业版",
  "RunningHub · CN 站（MJ 等）",
  "RunningHub 海外版 · 图片 / 视频 / 音乐 / LLM",
  "NodyHub · 图片 / 视频",
  "华狐 AI · Seedance",
  "ComfyUI 本地推理",
  "本地模型 · OpenAI 兼容 / SGLang",
] as const;

export type NodeModelCategoryId = (typeof NODE_MODEL_CATEGORIES)[number]["id"];
