"use client";

import { useRouter, usePathname } from "next/navigation";
import { FolderOpen } from "lucide-react";

export function Sidebar() {
  const router = useRouter();
  const pathname = usePathname();
  const active = pathname.startsWith("/projects");

  return (
    <aside className="fixed left-0 top-14 bottom-0 z-30 flex w-16 flex-col items-center border-r border-border bg-background/60 backdrop-blur-xl py-4">
      <button
        onClick={() => router.push("/projects")}
        title="我的项目"
        className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${
          active ? "bg-primary/20 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground"
        }`}
      >
        <FolderOpen className="h-5 w-5" />
      </button>
    </aside>
  );
}
