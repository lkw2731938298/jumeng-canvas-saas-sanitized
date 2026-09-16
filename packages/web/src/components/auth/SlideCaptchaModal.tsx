"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, ChevronsRight, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { fetchCaptcha, verifyCaptcha, type SmsAuthScene } from "@/lib/api/auth";
import { handleApiError } from "@/lib/errors/handleApiError";

export interface SlideCaptchaModalProps {
  open: boolean;
  phone: string;
  scene: SmsAuthScene | string;
  onClose: () => void;
  /** 校验成功，返回一次性 captchaTicket */
  onSuccess: (ticket: string) => void;
}

/**
 * 高级滑动拼图弹窗：玻璃态卡片 + 主题点缀色 + 成功/失败动效。
 */
export function SlideCaptchaModal({
  open,
  phone,
  scene,
  onClose,
  onSuccess,
}: SlideCaptchaModalProps) {
  const [captchaId, setCaptchaId] = useState("");
  const [backgroundImage, setBackgroundImage] = useState("");
  const [sliderImage, setSliderImage] = useState("");
  const [puzzleY, setPuzzleY] = useState(0);
  const [imageWidth, setImageWidth] = useState(320);
  const [imageHeight, setImageHeight] = useState(168);
  const [sliderSize, setSliderSize] = useState(48);
  const [themeAccent, setThemeAccent] = useState("#60a5fa");
  const [debugAnswer, setDebugAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [verifying, setVerifying] = useState(false);
  /** 逻辑坐标偏移（与后端 imageWidth 像素一致） */
  const [offsetX, setOffsetX] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<"idle" | "ok" | "fail">("idle");
  const [shake, setShake] = useState(false);
  const [renderWidth, setRenderWidth] = useState(320);
  const [imgReady, setImgReady] = useState(false);

  const panelRef = useRef<HTMLDivElement>(null);
  const dragStartX = useRef(0);
  const dragStartOffset = useRef(0);
  const offsetRef = useRef(0);
  const maxOffset = Math.max(0, imageWidth - sliderSize);
  const scale = imageWidth > 0 ? renderWidth / imageWidth : 1;

  const applyOffset = useCallback(
    (v: number) => {
      const next = Math.max(0, Math.min(maxOffset, v));
      offsetRef.current = next;
      setOffsetX(next);
    },
    [maxOffset]
  );

  const loadChallenge = useCallback(async () => {
    setLoading(true);
    setStatus("idle");
    setImgReady(false);
    offsetRef.current = 0;
    setOffsetX(0);
    try {
      const res = await fetchCaptcha();
      setCaptchaId(res.captchaId || "");
      setBackgroundImage(res.backgroundImage || res.imageBase64 || "");
      setSliderImage(res.sliderImage || "");
      setPuzzleY(res.puzzleY || 0);
      setImageWidth(res.imageWidth || 320);
      setImageHeight(res.imageHeight || 168);
      setSliderSize(res.sliderSize || 48);
      setThemeAccent(res.themeAccent || "#60a5fa");
      setDebugAnswer(res.answer || "");
    } catch (err) {
      handleApiError(err, { fallbackMessage: "滑动验证码加载失败" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) {
      void loadChallenge();
    } else {
      offsetRef.current = 0;
      setOffsetX(0);
      setStatus("idle");
      setDragging(false);
      setShake(false);
      setImgReady(false);
    }
  }, [open, loadChallenge]);

  // 监听弹窗宽度，适配小屏缩放
  useEffect(() => {
    if (!open || !panelRef.current) return;
    const el = panelRef.current;
    const sync = () => setRenderWidth(el.clientWidth || imageWidth);
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, [open, imageWidth, backgroundImage]);

  const submitAt = useCallback(
    async (x: number) => {
      const normalized = phone.replace(/\D/g, "");
      if (normalized.length !== 11) {
        toast.error("请先输入有效的 11 位手机号");
        onClose();
        return;
      }
      if (!captchaId) {
        toast.error("验证码未加载，请刷新后重试");
        return;
      }
      setVerifying(true);
      try {
        const res = await verifyCaptcha({
          captchaId,
          slideX: Math.round(x),
          phone: normalized,
          scene,
        });
        setStatus("ok");
        window.setTimeout(() => {
          onSuccess(res.captchaTicket);
        }, 420);
      } catch (err) {
        setStatus("fail");
        setShake(true);
        window.setTimeout(() => setShake(false), 480);
        handleApiError(err, { fallbackMessage: "滑动验证未通过" });
        await loadChallenge();
      } finally {
        setVerifying(false);
      }
    },
    [captchaId, loadChallenge, onClose, onSuccess, phone, scene]
  );

  const onPointerDown = (clientX: number) => {
    if (loading || verifying || status === "ok") return;
    setDragging(true);
    setStatus("idle");
    dragStartX.current = clientX;
    dragStartOffset.current = offsetRef.current;
  };

  const onPointerMove = useCallback(
    (clientX: number) => {
      if (!dragging) return;
      const deltaLogical = (clientX - dragStartX.current) / (scale || 1);
      applyOffset(dragStartOffset.current + deltaLogical);
    },
    [applyOffset, dragging, scale]
  );

  const onPointerUp = useCallback(() => {
    if (!dragging) return;
    setDragging(false);
    void submitAt(offsetRef.current);
  }, [dragging, submitAt]);

  useEffect(() => {
    if (!dragging) return;
    const move = (e: PointerEvent) => onPointerMove(e.clientX);
    const up = () => onPointerUp();
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };
  }, [dragging, onPointerMove, onPointerUp]);

  if (!open) return null;

  const displayOffset = offsetX * scale;
  const trackFillPct = maxOffset > 0 ? (offsetX / maxOffset) * 100 : 0;
  const accent = themeAccent || "#60a5fa";

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="slide-captcha-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !verifying) onClose();
      }}
    >
      {/* 背景遮罩：柔和模糊 */}
      <div className="absolute inset-0 bg-black/55 backdrop-blur-md" />

      <div
        className={`relative w-full max-w-[360px] overflow-hidden rounded-2xl border shadow-[0_24px_80px_rgba(0,0,0,0.55)] transition-[border-color,box-shadow] duration-300 ${
          status === "ok"
            ? "border-emerald-400/50 shadow-[0_0_0_1px_rgba(52,211,153,0.25),0_24px_80px_rgba(0,0,0,0.55)]"
            : status === "fail"
              ? "border-rose-400/45"
              : "border-white/12"
        } ${shake ? "animate-[captcha-shake_0.45s_ease]" : ""}`}
        style={{
          background:
            "linear-gradient(165deg, rgba(255,255,255,0.09) 0%, rgba(255,255,255,0.03) 40%, rgba(10,14,24,0.92) 100%)",
          backdropFilter: "blur(28px)",
          WebkitBackdropFilter: "blur(28px)",
        }}
      >
        {/* 顶部主题光带 */}
        <div
          className="pointer-events-none absolute inset-x-0 top-0 h-24 opacity-60"
          style={{
            background: `radial-gradient(ellipse 80% 100% at 50% -20%, ${accent}55, transparent 70%)`,
          }}
        />

        <div className="relative p-5">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-white/35">
                Security Check
              </p>
              <h2 id="slide-captcha-title" className="mt-1 text-[17px] font-semibold tracking-tight text-white">
                完成拼图验证
              </h2>
              <p className="mt-0.5 text-xs text-white/45">拖动滑块，将拼图块对齐缺口</p>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void loadChallenge()}
                disabled={loading || verifying}
                title="换一张"
                className="rounded-lg p-1.5 text-white/45 transition-colors hover:bg-white/10 hover:text-white/85 disabled:opacity-40"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
              <button
                type="button"
                onClick={onClose}
                disabled={verifying}
                className="rounded-lg p-1.5 text-white/45 transition-colors hover:bg-white/10 hover:text-white/85 disabled:opacity-40"
                aria-label="关闭"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div
            ref={panelRef}
            className="relative w-full overflow-hidden rounded-xl ring-1 ring-white/10"
            style={{
              boxShadow: `inset 0 0 0 1px rgba(255,255,255,0.04), 0 8px 32px rgba(0,0,0,0.35)`,
            }}
          >
            {/* 加载骨架 */}
            {(loading || !imgReady) && (
              <div
                className="absolute inset-0 z-[1] animate-pulse bg-gradient-to-br from-white/5 via-white/[0.02] to-transparent"
                style={{ aspectRatio: `${imageWidth} / ${imageHeight}` }}
              />
            )}

            {backgroundImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={backgroundImage}
                alt=""
                draggable={false}
                onLoad={() => setImgReady(true)}
                className={`block h-auto w-full select-none transition-opacity duration-300 ${
                  imgReady ? "opacity-100" : "opacity-0"
                }`}
                style={{ aspectRatio: `${imageWidth} / ${imageHeight}` }}
              />
            ) : (
              <div
                className="flex items-center justify-center text-sm text-white/35"
                style={{ height: imageHeight * scale }}
              >
                {loading ? "加载中…" : "暂无验证图"}
              </div>
            )}

            {sliderImage && imgReady ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={sliderImage}
                alt=""
                draggable={false}
                className="pointer-events-none absolute left-0 z-[2] select-none"
                style={{
                  top: puzzleY * scale,
                  width: (sliderSize + 14) * scale,
                  height: (sliderSize + 14) * scale,
                  transform: `translateX(${displayOffset}px)`,
                  filter:
                    status === "ok"
                      ? "drop-shadow(0 0 8px rgba(52,211,153,0.55))"
                      : "drop-shadow(0 4px 10px rgba(0,0,0,0.45))",
                  transition: dragging ? "none" : "filter 0.25s ease",
                }}
              />
            ) : null}

            {/* 成功遮罩 */}
            {status === "ok" ? (
              <div className="absolute inset-0 z-[3] flex items-center justify-center bg-emerald-950/35 backdrop-blur-[1px]">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500 text-white shadow-[0_0_24px_rgba(52,211,153,0.55)]">
                  <Check className="h-6 w-6" strokeWidth={2.5} />
                </div>
              </div>
            ) : null}
          </div>

          {/* 滑轨 */}
          <div
            className="relative mt-4 h-11 select-none overflow-hidden rounded-full"
            style={{
              background: "rgba(255,255,255,0.06)",
              boxShadow: "inset 0 1px 2px rgba(0,0,0,0.35)",
            }}
          >
            <div
              className="absolute inset-y-0 left-0 rounded-full transition-[width,background] duration-150"
              style={{
                width: `calc(${trackFillPct}% + 22px)`,
                background:
                  status === "ok"
                    ? "linear-gradient(90deg, rgba(16,185,129,0.25), rgba(52,211,153,0.45))"
                    : status === "fail"
                      ? "linear-gradient(90deg, rgba(244,63,94,0.2), rgba(251,113,133,0.4))"
                      : `linear-gradient(90deg, ${accent}22, ${accent}55)`,
              }}
            />
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <span
                className={`text-[11px] tracking-wide transition-opacity ${
                  offsetX > 8 || status !== "idle" ? "opacity-0" : "opacity-100"
                } text-white/35`}
              >
                按住滑块拖动完成拼图
              </span>
              {status === "ok" ? (
                <span className="text-[11px] font-medium text-emerald-300/90">验证通过</span>
              ) : null}
              {status === "fail" ? (
                <span className="text-[11px] font-medium text-rose-300/90">未对齐，已刷新</span>
              ) : null}
            </div>
            <button
              type="button"
              disabled={loading || verifying || !captchaId || status === "ok"}
              onPointerDown={(e) => {
                e.preventDefault();
                (e.currentTarget as HTMLButtonElement).setPointerCapture?.(e.pointerId);
                onPointerDown(e.clientX);
              }}
              className={`absolute top-1 flex h-9 w-12 items-center justify-center rounded-[10px] text-white transition-shadow disabled:opacity-50 ${
                dragging ? "scale-[1.03]" : ""
              }`}
              style={{
                left: displayOffset,
                touchAction: "none",
                background:
                  status === "ok"
                    ? "linear-gradient(135deg, #10b981, #34d399)"
                    : status === "fail"
                      ? "linear-gradient(135deg, #e11d48, #fb7185)"
                      : `linear-gradient(135deg, ${accent}, ${accent}cc)`,
                boxShadow: dragging
                  ? `0 0 0 3px ${accent}33, 0 8px 20px rgba(0,0,0,0.35)`
                  : `0 4px 14px rgba(0,0,0,0.35)`,
              }}
              aria-label="拖动完成拼图"
            >
              {status === "ok" ? (
                <Check className="h-4 w-4" strokeWidth={2.5} />
              ) : (
                <ChevronsRight className={`h-4 w-4 ${dragging ? "animate-pulse" : ""}`} />
              )}
            </button>
          </div>

          {debugAnswer ? (
            <p className="mt-2.5 text-center text-[11px] text-amber-400/75">
              开发模式目标 X：{debugAnswer}
            </p>
          ) : null}
        </div>
      </div>

      <style>{`
        @keyframes captcha-shake {
          0%, 100% { transform: translateX(0); }
          20% { transform: translateX(-6px); }
          40% { transform: translateX(6px); }
          60% { transform: translateX(-4px); }
          80% { transform: translateX(3px); }
        }
      `}</style>
    </div>
  );
}
