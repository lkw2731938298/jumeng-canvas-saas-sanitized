import type { Metadata } from "next";
import DiscoverHome from "./DiscoverHome";
import { DISCOVER_SEO } from "@/lib/pageSeo";

export const metadata: Metadata = DISCOVER_SEO;

/** 用户站首页：发现页挂在根路径 `/`（勿再使用 app/page.tsx 跳转页，否则会与 /discover 形成死循环）。 */
export default function HomePage() {
  return <DiscoverHome />;
}
