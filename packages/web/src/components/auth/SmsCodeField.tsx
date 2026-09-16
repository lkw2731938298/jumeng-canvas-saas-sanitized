"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { sendAuthCode, type SmsAuthScene } from "@/lib/api/auth";
import { handleApiError } from "@/lib/errors/handleApiError";

interface SmsCodeFieldProps {
  phone: string;
  scene: SmsAuthScene;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  /** 登录弹窗浅色样式 */
  appearance?: "dark" | "light";
  /** 发短信前完成滑动验证并返回 ticket；未启用验证码时可省略 */
  resolveCaptchaTicket?: () => Promise<string>;
  /** 发短信失败且 ticket 已消费时回调（滑动验证下次点击会重新弹窗） */
  onCaptchaNeedRefresh?: () => void | Promise<void>;
  /** 冷却结束回调（滑动验证无需预加载） */
  onCaptchaPrepareResend?: () => void | Promise<void>;
}

export function SmsCodeField({
  phone,
  scene,
  value,
  onChange,
  disabled,
  appearance = "dark",
  resolveCaptchaTicket,
  onCaptchaNeedRefresh,
  onCaptchaPrepareResend,
}: SmsCodeFieldProps) {
  const [sending, setSending] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const hadCountdownRef = useRef(false);
  const prepareResendRef = useRef(onCaptchaPrepareResend);
  const needRefreshRef = useRef(onCaptchaNeedRefresh);
  const resolveTicketRef = useRef(resolveCaptchaTicket);

  prepareResendRef.current = onCaptchaPrepareResend;
  needRefreshRef.current = onCaptchaNeedRefresh;
  resolveTicketRef.current = resolveCaptchaTicket;

  useEffect(() => {
    if (countdown > 0) {
      hadCountdownRef.current = true;
      const timer = window.setTimeout(() => setCountdown((c) => c - 1), 1000);
      return () => window.clearTimeout(timer);
    }
    // 冷却刚结束：通知上层（滑动验证下次点击时再弹窗）
    if (hadCountdownRef.current && countdown === 0) {
      hadCountdownRef.current = false;
      void prepareResendRef.current?.();
    }
    return undefined;
  }, [countdown]);

  const handleSend = useCallback(async () => {
    const normalized = phone.replace(/\D/g, "");
    if (normalized.length !== 11) {
      toast.error("请先输入有效的 11 位手机号");
      return;
    }
    setSending(true);
    let ticketIssued = false;
    try {
      let ticket = "";
      if (resolveTicketRef.current) {
        ticket = await resolveTicketRef.current();
        ticketIssued = Boolean(ticket);
      }
      const res = await sendAuthCode(normalized, scene, ticket || undefined);
      setCountdown(60);
      if (res.code) {
        toast.success(`验证码已发送（开发模式：${res.code}）`);
      } else {
        toast.success(`验证码已发送至 ${res.maskedPhone}`);
      }
    } catch (err) {
      // 用户取消滑动验证：静默，不 toast 网络错误
      if (err instanceof Error && (err.message === "已取消滑动验证" || err.message === "CAPTCHA_SUPERSEDED")) {
        return;
      }
      // ticket 已签发但发短信失败：下次获取验证码会重新弹窗
      if (ticketIssued) {
        await needRefreshRef.current?.();
      }
      handleApiError(err, {
        onSmsCooldown: (waitSeconds) => setCountdown(Math.max(waitSeconds, 1)),
      });
    } finally {
      setSending(false);
    }
  }, [phone, scene]);

  const isLight = appearance === "light";

  return (
    <label className="block">
      <span className={`mb-1.5 block text-sm ${isLight ? "text-neutral-500" : "text-white/60"}`}>
        短信验证码
      </span>
      <div className="flex gap-2">
        <input
          type="text"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
          placeholder="请输入验证码"
          disabled={disabled}
          className={
            isLight
              ? "min-w-0 flex-1 rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-neutral-900 outline-none focus:border-neutral-900 disabled:opacity-50"
              : "min-w-0 flex-1 rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50 disabled:opacity-50"
          }
          required
        />
        <button
          type="button"
          onClick={() => void handleSend()}
          disabled={disabled || sending || countdown > 0}
          className={
            isLight
              ? "shrink-0 rounded-lg border border-neutral-900 bg-white px-3 py-2.5 text-sm text-neutral-900 hover:bg-neutral-50 disabled:opacity-50"
              : "shrink-0 rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-sm text-white/80 hover:bg-white/10 disabled:opacity-50"
          }
        >
          {sending ? "发送中…" : countdown > 0 ? `${countdown}s` : "获取验证码"}
        </button>
      </div>
    </label>
  );
}
