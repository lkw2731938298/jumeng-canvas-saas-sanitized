"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GridMotion } from "@/components/auth/GridMotion";
import { prepareAuthGridDisplayUrls } from "@/lib/auth/downscaleAuthGridUrls";
import { buildAuthGridItems } from "@/lib/auth/gridMotionImages";
import { getSiteHomepage } from "@/lib/api/site";
import { resolveVisualStyleImageUrl } from "@/lib/canvas/visualStyleImage";

const SITE_HOMEPAGE_QUERY_KEY = ["site", "homepage"] as const;

export function useSiteHomepage() {
  return useQuery({
    queryKey: SITE_HOMEPAGE_QUERY_KEY,
    queryFn: getSiteHomepage,
    staleTime: 5 * 60 * 1000,
  });
}

/** Login / register page background grid (GridMotion with admin images). */
export function AuthHomepageBackground() {
  const { data } = useSiteHomepage();

  // 管理端配置的原始 URL（后端已带 OSS 缩略参数）
  const sourceUrls = useMemo(() => {
    return (data?.authGridImageUrls ?? [])
      .map((url) => resolveVisualStyleImageUrl(url))
      .filter(Boolean);
  }, [data?.authGridImageUrls]);

  // 先立即用原 URL 铺网格，避免空白；再对唯一图降采样替换
  const fallbackItems = useMemo(() => buildAuthGridItems(sourceUrls), [sourceUrls]);
  const uniqueSeedUrls = useMemo(
    () => Array.from(new Set(fallbackItems)),
    [fallbackItems],
  );
  const [gridItems, setGridItems] = useState<string[]>(fallbackItems);

  useEffect(() => {
    let cancelled = false;
    let revoke: (() => void) | undefined;
    setGridItems(fallbackItems);

    void (async () => {
      // 仅对唯一图 decode/resize 一次，再循环填满 28 格
      const prepared = await prepareAuthGridDisplayUrls(uniqueSeedUrls);
      if (cancelled) {
        prepared.revoke();
        return;
      }
      revoke = prepared.revoke;
      setGridItems(
        buildAuthGridItems(prepared.urls.length > 0 ? prepared.urls : uniqueSeedUrls),
      );
    })();

    return () => {
      cancelled = true;
      revoke?.();
    };
  }, [fallbackItems, uniqueSeedUrls]);

  return (
    <div className="absolute inset-0 z-0 bg-black">
      {gridItems.length > 0 ? (
        <GridMotion items={gridItems} gradientColor="black" className="h-full w-full" />
      ) : null}
      <div className="pointer-events-none absolute inset-0 z-[3] bg-black/55" aria-hidden />
      <div
        className="pointer-events-none absolute inset-0 z-[4] bg-[radial-gradient(circle_at_center,transparent_0%,rgba(0,0,0,0.65)_100%)]"
        aria-hidden
      />
    </div>
  );
}
