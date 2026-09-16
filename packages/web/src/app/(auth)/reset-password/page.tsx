"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AuthShell } from "@/components/auth/AuthShell";
import { CaptchaField, type CaptchaFieldHandle } from "@/components/auth/CaptchaField";
import { SmsCodeField } from "@/components/auth/SmsCodeField";
import * as authApi from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { clearUserScopedClientState } from "@/lib/auth/session";
import { useAuthStore } from "@/stores/authStore";

export default function ResetPasswordPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const login = useAuthStore((s) => s.login);
  const captchaRef = useRef<CaptchaFieldHandle>(null);
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [captchaEnabled, setCaptchaEnabled] = useState(true);

  useEffect(() => {
    authApi
      .getAuthConfig()
      .then((cfg) => setCaptchaEnabled(cfg.captchaEnabled !== false))
      .catch(() => setCaptchaEnabled(true));
  }, []);

  const resolveCaptchaTicket = async () => {
    if (!captchaEnabled) return "";
    return captchaRef.current!.issueTicket(phone);
  };

  const refreshCaptcha = async () => {
    await captchaRef.current?.refresh();
  };

  const prepareCaptchaResend = async () => {
    await captchaRef.current?.prepareForResend();
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      toast.error("两次输入的密码不一致");
      return;
    }
    setLoading(true);
    try {
      const res = await authApi.resetPassword(phone.trim(), code.trim(), password);
      clearUserScopedClientState(queryClient);
      login(res.sessionToken, res.user);
      toast.success("密码已重置");
      // 重置并自动登录后进入 huabu 发现首页。
      router.replace("/");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "重置失败";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell title="找回密码" subtitle="通过短信验证码重置登录密码">
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-1.5 block text-sm text-white/60">手机号</span>
          <input
            type="tel"
            autoComplete="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="11 位手机号"
            className="w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50"
            required
          />
        </label>
        {/* 滑动验证弹窗：点击「获取验证码」时弹出 */}
        <CaptchaField
          ref={captchaRef}
          scene="password_reset"
          enabled={captchaEnabled}
          disabled={loading}
        />
        <SmsCodeField
          phone={phone}
          scene="password_reset"
          value={code}
          onChange={setCode}
          disabled={loading}
          resolveCaptchaTicket={captchaEnabled ? resolveCaptchaTicket : undefined}
          onCaptchaNeedRefresh={captchaEnabled ? refreshCaptcha : undefined}
          onCaptchaPrepareResend={captchaEnabled ? prepareCaptchaResend : undefined}
        />
        <label className="block">
          <span className="mb-1.5 block text-sm text-white/60">新密码</span>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="至少 8 位，含字母、数字和特殊字符"
            className="w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50"
            required
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm text-white/60">确认新密码</span>
          <input
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder="再次输入新密码"
            className="w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50"
            required
          />
        </label>
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? "提交中…" : "重置密码并登录"}
        </button>
        <p className="text-center text-sm text-white/50">
          <Link href="/login" className="text-primary hover:underline">
            返回登录
          </Link>
        </p>
      </form>
    </AuthShell>
  );
}
