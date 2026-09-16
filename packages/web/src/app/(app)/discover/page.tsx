"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** 兼容旧地址 /discover：软跳转到首页 `/`。HTTP 层 next.config 亦有同向跳转。 */
export default function DiscoverRedirectPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return (
    <div className="flex min-h-screen items-center justify-center bg-background text-sm text-muted-foreground">
      正在进入首页…
    </div>
  );
}
