import type { Metadata } from "next";
import { ACCOUNT_SEO } from "@/lib/pageSeo";

/** 个人中心页 SEO（title / keywords / description） */
export const metadata: Metadata = ACCOUNT_SEO;

export default function AccountSeoLayout({ children }: { children: React.ReactNode }) {
  return children;
}
