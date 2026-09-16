"use client";

import { Suspense, FormEvent, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AuthShell } from "@/components/auth/AuthShell";
import { CaptchaField, type CaptchaFieldHandle } from "@/components/auth/CaptchaField";
import { SmsCodeField } from "@/components/auth/SmsCodeField";
import * as authApi from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { clearUserScopedClientState } from "@/lib/auth/session";
import { clearAdminTabsStorage } from "@/lib/admin/adminTabsStorage";
import { normalizeReturnPath } from "@/lib/basePath";
import { useAuthStore } from "@/stores/authStore";

function AdminLoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const loginStore = useAuthStore((s) => s.login);
  const captchaRef = useRef<CaptchaFieldHandle>(null);
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [smsCode, setSmsCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [captchaEnabled, setCaptchaEnabled] = useState(true);

  useEffect(() => {
    authApi
      .getAuthConfig()
      .then((cfg) => setCaptchaEnabled(cfg.captchaEnabled !== false))
      .catch(() => setCaptchaEnabled(true));
  }, []);

  const finishLogin = (res: Awaited<ReturnType<typeof authApi.adminLogin>>) => {
    // 重新登录：清空历史页签，避免上次会话标签残留
    clearAdminTabsStorage();
    clearUserScopedClientState(queryClient);
    loginStore(res.sessionToken, res.user);
    const nextRaw = searchParams.get("next") || "/admin";
    router.replace(normalizeReturnPath(nextRaw));
  };

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
    setLoading(true);
    try {
      finishLogin(
        await authApi.adminLogin({
          phone: phone.trim(),
          password,
          smsCode: smsCode.trim(),
        })
      );
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === "ADMIN_REQUIRED") {
          toast.error("该账号无管理后台访问权限");
          return;
        }
        toast.error(err.message || "登录失败");
        return;
      }
      toast.error("登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      title="管理后台登录"
      subtitle="须使用管理员账号；需同时验证登录密码、滑动验证与短信验证码"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="mb-1.5 block text-sm text-white/60">管理员手机号</span>
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

        <label className="block">
          <span className="mb-1.5 block text-sm text-white/60">登录密码</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="登录密码"
            className="w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50"
            required
          />
        </label>

        {/* 滑动验证弹窗：点击「获取验证码」时弹出 */}
        <CaptchaField
          ref={captchaRef}
          scene="admin_login"
          enabled={captchaEnabled}
          disabled={loading}
        />

        <SmsCodeField
          phone={phone}
          scene="admin_login"
          value={smsCode}
          onChange={setSmsCode}
          disabled={loading}
          resolveCaptchaTicket={captchaEnabled ? resolveCaptchaTicket : undefined}
          onCaptchaNeedRefresh={captchaEnabled ? refreshCaptcha : undefined}
          onCaptchaPrepareResend={captchaEnabled ? prepareCaptchaResend : undefined}
        />

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {loading ? "登录中…" : "进入管理后台"}
        </button>
      </form>
    </AuthShell>
  );
}

export default function AdminLoginPage() {
  return (
    <Suspense fallback={null}>
      <AdminLoginForm />
    </Suspense>
  );
}
