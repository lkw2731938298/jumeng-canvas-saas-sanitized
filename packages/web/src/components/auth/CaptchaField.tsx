"use client";

import {
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";
import { SlideCaptchaModal } from "@/components/auth/SlideCaptchaModal";
import type { SmsAuthScene } from "@/lib/api/auth";

export type CaptchaFieldHandle = {
  /** 弹出滑动拼图，通过后返回一次性 ticket；取消则抛错 */
  issueTicket: (phone: string) => Promise<string>;
  /** 兼容旧调用：滑动验证无需预加载 */
  refresh: () => Promise<void>;
  markVerified: () => void;
  prepareForResend: () => Promise<void>;
};

interface CaptchaFieldProps {
  scene: SmsAuthScene | "user_login";
  disabled?: boolean;
  /** 为 false 时不弹窗（CAPTCHA_ENABLED=false） */
  enabled?: boolean;
}

type PendingResolve = {
  phone: string;
  resolve: (ticket: string) => void;
  reject: (err: Error) => void;
};

/**
 * 滑动验证码控制器：不在表单内展示输入框，
 * 由「获取验证码」/密码登录风控通过 issueTicket 弹出拼图弹窗。
 */
export const CaptchaField = forwardRef<CaptchaFieldHandle, CaptchaFieldProps>(
  function CaptchaField({ scene, enabled = true }, ref) {
    const [open, setOpen] = useState(false);
    const [phoneForModal, setPhoneForModal] = useState("");
    const pendingRef = useRef<PendingResolve | null>(null);

    const closeAndReject = useCallback((message = "已取消滑动验证") => {
      const pending = pendingRef.current;
      pendingRef.current = null;
      setOpen(false);
      pending?.reject(new Error(message));
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        refresh: async () => undefined,
        markVerified: () => undefined,
        prepareForResend: async () => undefined,
        issueTicket: async (phone: string) => {
          if (!enabled) return "";
          const normalized = phone.replace(/\D/g, "");
          if (normalized.length !== 11) {
            toast.error("请先输入有效的 11 位手机号");
            throw new Error("INVALID_PHONE");
          }
          // 若已有未完成弹窗，先取消旧 Promise
          if (pendingRef.current) {
            pendingRef.current.reject(new Error("CAPTCHA_SUPERSEDED"));
            pendingRef.current = null;
          }
          setPhoneForModal(normalized);
          setOpen(true);
          return new Promise<string>((resolve, reject) => {
            pendingRef.current = { phone: normalized, resolve, reject };
          });
        },
      }),
      [enabled]
    );

    if (!enabled) return null;

    return (
      <SlideCaptchaModal
        open={open}
        phone={phoneForModal}
        scene={scene}
        onClose={() => closeAndReject()}
        onSuccess={(ticket) => {
          const pending = pendingRef.current;
          pendingRef.current = null;
          setOpen(false);
          pending?.resolve(ticket);
        }}
      />
    );
  }
);
