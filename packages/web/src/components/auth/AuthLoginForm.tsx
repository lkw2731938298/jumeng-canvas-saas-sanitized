"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CaptchaField, type CaptchaFieldHandle } from "@/components/auth/CaptchaField";
import { LegalDocumentDialog } from "@/components/auth/LegalDocumentDialog";
import { SmsCodeField } from "@/components/auth/SmsCodeField";
import type { SiteLegalDocType } from "@/lib/api/site";
import "./authPage.css";
import * as authApi from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { clearUserScopedClientState } from "@/lib/auth/session";
import { useAuthStore } from "@/stores/authStore";

export type LoginMode = "password" | "sms";

type AuthLoginFormProps = {
  appearance?: "page" | "modal";
  defaultMode?: LoginMode;
  onLoggedIn?: () => void;
  onNavigateAway?: () => void;
};

/** 登录表单：全页深色 / 弹窗浅色共用同一套短信与密码逻辑。 */
export function AuthLoginForm({
  appearance = "page",
  defaultMode = "password",
  onLoggedIn,
  onNavigateAway,
}: AuthLoginFormProps) {
  const queryClient = useQueryClient();
  const loginStore = useAuthStore((s) => s.login);
  const captchaRef = useRef<CaptchaFieldHandle>(null);
  const [mode, setMode] = useState<LoginMode>(defaultMode);
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [captchaEnabled, setCaptchaEnabled] = useState(true);
  const [passwordCaptchaRequired, setPasswordCaptchaRequired] = useState(false);
  const [passwordSmsRequired, setPasswordSmsRequired] = useState(false);
  const isModal = appearance === "modal";
  const [legalDocType, setLegalDocType] = useState<SiteLegalDocType | null>(null);
  const [legalDialogOpen, setLegalDialogOpen] = useState(false);

  useEffect(() => {
    authApi
      .getAuthConfig()
      .then((cfg) => setCaptchaEnabled(cfg.captchaEnabled !== false))
      .catch(() => setCaptchaEnabled(true));
  }, []);

  useEffect(() => {
    const normalized = phone.replace(/\D/g, "");
    if (mode !== "password" || normalized.length !== 11 || !captchaEnabled) {
      setPasswordCaptchaRequired(false);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      authApi
        .getCaptchaStatus(normalized)
        .then((st) => {
          if (!cancelled) setPasswordCaptchaRequired(Boolean(st.requiredForPasswordLogin));
        })
        .catch(() => {
          if (!cancelled) setPasswordCaptchaRequired(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [phone, mode, captchaEnabled]);

  const finishLogin = (res: Awaited<ReturnType<typeof authApi.login>>) => {
    clearUserScopedClientState(queryClient);
    loginStore(res.sessionToken, res.user);
    onLoggedIn?.();
  };

  const resolveCaptchaTicket = async () => {
    if (!captchaEnabled) return "";
    return captchaRef.current!.issueTicket(phone);
  };

  const refreshCaptcha = async () => {
    await captchaRef.current?.refresh();
  };

  const prepareCaptchaPrepareResend = async () => {
    await captchaRef.current?.prepareForResend();
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const trimmedPhone = phone.trim();
      if (mode === "sms") {
        finishLogin(await authApi.loginSms(trimmedPhone, smsCode.trim()));
      } else {
        if (passwordSmsRequired && !smsCode.trim()) {
          toast.error("请输入短信验证码");
          return;
        }
        let ticket = "";
        if (captchaEnabled && passwordCaptchaRequired) {
          ticket = await resolveCaptchaTicket();
        }
        finishLogin(
          await authApi.login(
            trimmedPhone,
            password,
            ticket || undefined,
            passwordSmsRequired ? smsCode.trim() : undefined
          )
        );
      }
    } catch (err) {
      if (err instanceof Error && (err.message === "已取消滑动验证" || err.message === "CAPTCHA_SUPERSEDED")) {
        return;
      }
      if (err instanceof ApiError) {
        if (err.code === "LOGIN_SMS_REQUIRED") {
          setPasswordSmsRequired(true);
          setPasswordCaptchaRequired(false);
          toast.error(err.message || "检测到异地登录，请输入短信验证码完成验证");
          return;
        }
        if (err.code === "CAPTCHA_REQUIRED") {
          setPasswordCaptchaRequired(true);
          toast.error(err.message || "请先完成滑动验证");
          return;
        }
        const detail = err.detail as { code?: string; message?: string } | undefined;
        if (detail?.code === "USER_NOT_REGISTERED" || err.code === "USER_NOT_REGISTERED") {
          toast.error("该手机号尚未注册，请先前往注册页创建账号");
          return;
        }
        if (err.code === "INVALID_PASSWORD" || err.code === "LOGIN_LOCKED") {
          const normalized = phone.replace(/\D/g, "");
          if (normalized.length === 11 && captchaEnabled) {
            authApi
              .getCaptchaStatus(normalized)
              .then((st) => setPasswordCaptchaRequired(Boolean(st.requiredForPasswordLogin)))
              .catch(() => undefined);
          }
        }
        toast.error(err.message || "登录失败");
        return;
      }
      toast.error("登录失败");
    } finally {
      setLoading(false);
    }
  };

  const inputClass = isModal
    ? "w-full rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-neutral-900 outline-none focus:border-neutral-900"
    : "w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50";
  const labelClass = isModal ? "mb-1.5 block text-sm text-neutral-500" : "mb-1.5 block text-sm text-white/60";

  return (
    <>
      <div className={isModal ? "login-modal-tabs" : "mb-4 flex rounded-lg border border-white/10 p-0.5"}>
        {isModal ? (
          <>
            <button
              type="button"
              onClick={() => setMode("sms")}
              className={mode === "sms" ? "is-active" : undefined}
            >
              手机号登录
            </button>
            <button
              type="button"
              onClick={() => setMode("password")}
              className={mode === "password" ? "is-active" : undefined}
            >
              密码登录
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => setMode("password")}
              className={`flex-1 rounded-md py-2 text-sm transition-colors ${
                mode === "password" ? "bg-primary text-primary-foreground" : "text-white/60 hover:text-white/80"
              }`}
            >
              密码登录
            </button>
            <button
              type="button"
              onClick={() => setMode("sms")}
              className={`flex-1 rounded-md py-2 text-sm transition-colors ${
                mode === "sms" ? "bg-primary text-primary-foreground" : "text-white/60 hover:text-white/80"
              }`}
            >
              验证码登录
            </button>
          </>
        )}
      </div>

      <form onSubmit={handleSubmit} className={isModal ? "login-modal-form" : "space-y-4"}>
        <label className="block">
          <span className={labelClass}>手机号</span>
          {isModal ? (
            <div className="login-modal-phone">
              <span>+86</span>
              <input
                type="tel"
                autoComplete="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="请输入手机号"
                required
              />
            </div>
          ) : (
            <input
              type="tel"
              autoComplete="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="11 位手机号"
              className={inputClass}
              required
            />
          )}
        </label>

        {mode === "password" ? (
          <label className="block">
            <span className={labelClass}>密码</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="登录密码"
              className={inputClass}
              required
            />
          </label>
        ) : null}

        <CaptchaField
          ref={captchaRef}
          scene="user_login"
          enabled={captchaEnabled}
          disabled={loading}
        />

        {mode === "password" && passwordSmsRequired ? (
          <p
            className={
              isModal
                ? "rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800"
                : "rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200/90"
            }
          >
            检测到异地登录，为保障账号安全，请输入短信验证码完成验证。
          </p>
        ) : null}

        {mode === "sms" || (mode === "password" && passwordSmsRequired) ? (
          <SmsCodeField
            phone={phone}
            scene="user_login"
            value={smsCode}
            onChange={setSmsCode}
            disabled={loading}
            appearance={isModal ? "light" : "dark"}
            resolveCaptchaTicket={captchaEnabled ? resolveCaptchaTicket : undefined}
            onCaptchaNeedRefresh={captchaEnabled ? refreshCaptcha : undefined}
            onCaptchaPrepareResend={captchaEnabled ? prepareCaptchaPrepareResend : undefined}
          />
        ) : null}

        {mode === "password" ? (
          <p className={isModal ? "text-right text-sm" : "text-right text-sm"}>
            <Link
              href="/reset-password"
              className={isModal ? "text-neutral-500 hover:text-neutral-900" : "text-primary hover:underline"}
              onClick={() => onNavigateAway?.()}
            >
              忘记密码？
            </Link>
          </p>
        ) : null}

        <button
          type="submit"
          disabled={loading}
          className={
            isModal
              ? "login-modal-submit"
              : "w-full rounded-lg bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          }
        >
          {loading ? "登录中…" : isModal ? "登录/注册" : "登录"}
        </button>
        <p className={isModal ? "login-modal-legal" : "auth-page-legal"}>
          登录视为您已阅读并同意
          <button
            type="button"
            className={isModal ? "login-modal-legal-link" : "auth-page-legal-link"}
            onClick={() => {
              setLegalDocType("user-agreement");
              setLegalDialogOpen(true);
            }}
          >
            用户协议
          </button>
          和
          <button
            type="button"
            className={isModal ? "login-modal-legal-link" : "auth-page-legal-link"}
            onClick={() => {
              setLegalDocType("privacy-policy");
              setLegalDialogOpen(true);
            }}
          >
            隐私政策
          </button>
        </p>
        <LegalDocumentDialog
          docType={legalDocType}
          open={legalDialogOpen}
          onOpenChange={setLegalDialogOpen}
        />
        {isModal ? (
          <p className="text-center text-sm text-neutral-500">
            还没有账号？{" "}
            <Link
              href="/register"
              className="font-medium text-primary underline-offset-2 hover:underline"
              onClick={() => onNavigateAway?.()}
            >
              去注册
            </Link>
          </p>
        ) : (
          <p className="text-center text-sm text-white/50">
            还没有账号？{" "}
            <Link href="/register" className="text-primary hover:underline">
              注册
            </Link>
          </p>
        )}
      </form>
    </>
  );
}
