import type { Metadata } from "next";
import { SKILLS_SEO } from "@/lib/pageSeo";

/** 技能页 SEO（title / keywords / description） */
export const metadata: Metadata = SKILLS_SEO;

export default function SkillsSeoLayout({ children }: { children: React.ReactNode }) {
  return children;
}
