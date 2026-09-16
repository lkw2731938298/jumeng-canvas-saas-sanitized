"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDrive, Loader2 } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import { ASSETS_UPDATED_EVENT } from "@/lib/api/assets";
import { formatStorageGb, getStorageQuota } from "@/lib/api/storageQuota";
import { cn } from "@/lib/utils";

export function StorageQuotaPanel({ className }: { className?: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["storage", "quota"],
    queryFn: getStorageQuota,
    staleTime: 30_000,
  });

  useEffect(() => {
    const onAssetsUpdated = () => {
      void queryClient.invalidateQueries({ queryKey: ["storage", "quota"] });
    };
    window.addEventListener(ASSETS_UPDATED_EVENT, onAssetsUpdated);
    return () => window.removeEventListener(ASSETS_UPDATED_EVENT, onAssetsUpdated);
  }, [queryClient]);

  if (isLoading) {
    return (
      <div className={cn("flex items-center gap-2 text-sm text-white/50", className)}>
        <Loader2 className="h-4 w-4 animate-spin" />
        加载存储空间…
      </div>
    );
  }

  if (!data) return null;

  const pct =
    data.quotaBytes > 0 ? Math.min((data.usedBytes / data.quotaBytes) * 100, 100) : 0;
  const overQuota = data.usedBytes > data.quotaBytes;

  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-1.5 text-white/70">
          <HardDrive className="h-4 w-4" />
          云存储
        </span>
        <span className="font-mono text-white/90">
          {formatStorageGb(data.usedGb)} / {formatStorageGb(data.quotaGb)}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/10">
        <div
          className={cn(
            "h-full rounded-full transition-all",
            overQuota ? "bg-red-400" : pct > 85 ? "bg-amber-400" : "bg-primary"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-xs text-white/45">
        通用 {data.generalGb} GiB
        {data.isMemberActive ? ` + 会员 ${data.memberGb} GiB` : ""}
        {overQuota ? (
          <span className="ml-1 text-red-300">
            · 已超出配额，请删除素材
            {!data.isMemberActive ? "或续费会员" : ""}
            后再上传
          </span>
        ) : data.isMemberActive && data.memberExpiresAt ? (
          <span className="ml-1">
            · 会员空间至 {new Date(data.memberExpiresAt).toLocaleDateString("zh-CN")}
          </span>
        ) : null}
      </p>
      {overQuota ? (
        <Link
          href="/account"
          className="text-xs text-white/50 underline-offset-2 hover:text-white/75 hover:underline"
        >
          管理空间与会员 →
        </Link>
      ) : null}
    </div>
  );
}
