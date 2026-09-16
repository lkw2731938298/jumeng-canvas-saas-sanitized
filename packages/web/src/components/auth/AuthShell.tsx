"use client";

import Image from "next/image";
import { AuthHomepageBackground } from "@/components/site/SiteHomepageBackground";
import { withBasePath } from "@/lib/basePath";

export function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      <AuthHomepageBackground />
      <div className="relative z-10 w-full max-w-md">
        <div className="mb-8 text-center">
          {/* 品牌 Logo：与项目页/画布顶栏同高（20px），登录/注册共用 AuthShell */}
          <h1 className="flex items-center justify-center gap-2 text-2xl font-bold text-white/90">
            <Image
              src={withBasePath("/brand-logo.png")}
            alt="聚梦画布"
              width={36}
              height={20}
              className="h-5 w-auto object-contain"
              priority
              unoptimized
            />
            <span>
              <span className="text-primary">聚梦</span> 画布
            </span>
          </h1>
          <p className="mt-2 text-lg text-white/80">{title}</p>
          {subtitle ? <p className="mt-1 text-sm text-white/40">{subtitle}</p> : null}
        </div>
        <div
          className="rounded-2xl border border-white/10 bg-white/5 p-8"
          style={{ backdropFilter: "blur(24px)", WebkitBackdropFilter: "blur(24px)" }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
