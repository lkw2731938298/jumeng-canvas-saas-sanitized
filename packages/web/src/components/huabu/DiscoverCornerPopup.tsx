"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import type { DiscoverCornerPopup } from "@/types/admin";

function defaultPopup(): DiscoverCornerPopup {
  return {
    enabled: false,
    text: "",
    imageUrl: "",
    imageFill: false,
    aspectRatio: "16:9",
    showWhenLoggedIn: true,
    showWhenLoggedOut: true,
  };
}

export function normalizeDiscoverCornerPopup(
  raw: DiscoverCornerPopup | null | undefined
): DiscoverCornerPopup {
  const base = defaultPopup();
  if (!raw || typeof raw !== "object") return base;
  const ratio = raw.aspectRatio === "9:16" ? "9:16" : "16:9";
  return {
    enabled: Boolean(raw.enabled),
    text: String(raw.text || "").trim(),
    imageUrl: String(raw.imageUrl || "").trim(),
    imageFill: Boolean(raw.imageFill),
    aspectRatio: ratio,
    showWhenLoggedIn: raw.showWhenLoggedIn !== false,
    showWhenLoggedOut: raw.showWhenLoggedOut !== false,
  };
}

/**
 * 发现页右上角运营弹窗：无遮罩，不挡顶栏；关闭后离开再进入发现页会再出现。
 */
export function DiscoverCornerPopupCard({
  config,
  loggedIn,
}: {
  config?: DiscoverCornerPopup | null;
  loggedIn: boolean;
}) {
  const popup = normalizeDiscoverCornerPopup(config);
  const [closed, setClosed] = useState(false);

  useEffect(() => {
    // 每次进入发现页（本组件挂载）重新展示
    setClosed(false);
  }, [popup.enabled, popup.text, popup.imageUrl, popup.aspectRatio, popup.imageFill]);

  const audienceOk = loggedIn ? popup.showWhenLoggedIn : popup.showWhenLoggedOut;
  const hasBody = Boolean(popup.text || popup.imageUrl);
  if (closed || !popup.enabled || !audienceOk || !hasBody) return null;

  const ratioClass =
    popup.aspectRatio === "9:16"
      ? "discover-corner-popup-916"
      : "discover-corner-popup-169";
  const fillClass = popup.imageFill ? "is-fill" : "is-fit";

  return (
    <aside
      className={`discover-corner-popup ${ratioClass} ${fillClass}`}
      role="dialog"
      aria-label="发现页公告"
    >
      <button
        type="button"
        className="discover-corner-popup-close"
        aria-label="关闭"
        onClick={() => setClosed(true)}
      >
        <X size={14} strokeWidth={2.2} />
      </button>
      {popup.imageUrl ? (
        // 运营图可能来自 OSS/外链，不能走 Next Image 域名白名单
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className="discover-corner-popup-image"
          src={popup.imageUrl}
          alt=""
        />
      ) : null}
      {popup.text ? <p className="discover-corner-popup-text">{popup.text}</p> : null}
    </aside>
  );
}
