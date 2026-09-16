"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ThemePageBackdrop } from "@/components/theme/ThemePageBackdrop";
import { ProjectInvitesPanel } from "@/components/projects/ProjectInvitesPanel";
import { usePendingProjectInviteCount } from "@/lib/canvas/useProjectInvites";
import { withBasePath } from "@/lib/basePath";

export default function ProjectInvitesPage() {
  const router = useRouter();
  const { data: pendingCount = 0 } = usePendingProjectInviteCount();

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <ThemePageBackdrop />

      <div className="fixed left-0 top-0 z-50 flex items-center gap-4 px-6 py-4">
        <button
          type="button"
          onClick={() => router.push("/projects")}
          className="flex items-center gap-2 text-sm text-foreground/70 transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          项目列表
        </button>
        {/* 品牌 Logo：协作邀请页顶栏，高度与项目页一致（20px） */}
        <button
          type="button"
          onClick={() => router.push("/projects")}
          className="flex items-center gap-2 text-lg font-bold text-foreground transition-opacity hover:opacity-80"
        >
          <Image
            src={withBasePath("/brand-logo.png")}
            alt="聚梦画布"
            width={36}
            height={20}
            className="h-5 w-auto object-contain"
            priority
            unoptimized
          />
          <span className="text-primary">聚梦</span>
          <span>画布</span>
        </button>
      </div>

      <main className="relative z-10 mx-auto w-full max-w-2xl px-4 pb-16 pt-28">
        <h1 className="mb-2 text-center text-2xl font-bold text-foreground">
          协作邀请
          {pendingCount > 0 ? (
            <span className="ml-2 text-base font-normal text-muted-foreground">({pendingCount})</span>
          ) : null}
        </h1>
        <p className="mb-8 text-center text-sm text-muted-foreground">
          接受邀请后可编辑项目画布；生成消耗将从项目创建者算力扣除。也可在{" "}
          <Link href="/projects" className="text-foreground/70 underline-offset-2 hover:underline">
            项目列表 → 与我协作
          </Link>{" "}
          查看已加入的项目。
        </p>

        <section
          className="rounded-2xl border border-border bg-card/80 p-5 backdrop-blur-xl"
          style={{ WebkitBackdropFilter: "blur(24px)" }}
        >
          <ProjectInvitesPanel />
        </section>
      </main>
    </div>
  );
}
