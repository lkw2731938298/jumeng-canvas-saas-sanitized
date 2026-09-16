import type { Metadata } from "next";

/** 页面 SEO：title / keywords / description */
function pageSeo(title: string, keywords: string, description: string): Metadata {
  return {
    title,
    description,
    keywords,
    openGraph: { title, description },
  };
}

/** 发现页（首页 `/`） */
export const DISCOVER_SEO = pageSeo(
  "聚梦画布- AI 视频创作平台|漫剧制作|电商设计|爆款复刻",
  "聚梦画布,聚梦 ai 画布,AI 漫剧,AI 漫剧制作,漫剧制作,电商设计,AI 视频创作,AI 视频生成,AI 视频创作平台,爆款复刻",
  "聚梦画布是一款专业 AI 视频创作平台，也是目前唯一一个同时为人类创作者和 Agent 设计的视频创作系统。支持 AI 漫剧、电商设计、爆款复刻、ip 口播、达人探店等视频创作，还支持各种 skill 创作发布。致力于帮助用户更便捷、更高效的创作。"
);

/** 项目页 `/projects` */
export const PROJECTS_SEO = pageSeo(
  "我的项目-聚梦画布",
  "我的项目,项目管理",
  "我的项目为用户提供项目管理服务。"
);

/** 技能页 `/skills` */
export const SKILLS_SEO = pageSeo(
  "AI 漫剧 skill|电商带货视频 skill|IP 口播|商业广告 skill-聚梦画布",
  "AI 漫剧 skill,电商带货视频 skill,IP 口播,商业广告 skill",
  "Skill 聚集了各行各业普遍使用的技能，方便用户快速创作。"
);

/** 个人中心页 `/account` */
export const ACCOUNT_SEO = pageSeo(
  "个人中心-聚梦画布",
  "个人中心",
  "个人中心为用户提供个人资产的管理服务。"
);

/** 画布项目页用的数字编号：纯数字 projectNo，或 URL 上的数字 id */
export function projectSeoNumber(projectNo?: string | null, projectId?: string | null): string {
  const no = (projectNo ?? "").trim();
  if (/^\d+$/.test(no)) return no;
  const stripped = no.replace(/^PRJ-?/i, "");
  if (/^\d+$/.test(stripped)) return stripped;
  const id = (projectId ?? "").trim();
  if (/^\d+$/.test(id)) return id;
  return "";
}

/** 用户侧项目画布：title=`319-聚梦画布`，keywords/description=`319` */
export function projectCanvasSeo(no: string): Metadata {
  const n = no.trim();
  if (!n) {
    return pageSeo("聚梦画布", "聚梦画布", "聚梦画布");
  }
  return pageSeo(`${n}-聚梦画布`, n, n);
}
