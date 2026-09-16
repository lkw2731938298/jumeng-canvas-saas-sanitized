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

export default function RegisterPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const login = useAuthStore((s) => s.login);
  const captchaRef = useRef<CaptchaFieldHandle>(null);
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [registrationOpen, setRegistrationOpen] = useState<boolean | null>(null);
  const [captchaEnabled, setCaptchaEnabled] = useState(true);

  useEffect(() => {
    try {
      const fromUrl = new URLSearchParams(window.location.search).get("invite") || "";
      if (fromUrl.trim()) setInviteCode(fromUrl.trim().toUpperCase());
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    authApi
      .getAuthConfig()
      .then((cfg) => {
        setRegistrationOpen(cfg.registrationEnabled);
        setCaptchaEnabled(cfg.captchaEnabled !== false);
      })
      .catch(() => {
        setRegistrationOpen(true);
        setCaptchaEnabled(true);
      });
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
    if (!code.trim()) {
      toast.error("请先获取并填写短信验证码");
      return;
    }
    setLoading(true);
    try {
      const res = await authApi.register(
        phone.trim(),
        password,
        code.trim(),
        displayName.trim() || undefined,
        inviteCode.trim() || undefined
      );
      clearUserScopedClientState(queryClient);
      login(res.sessionToken, res.user);
      const bonusAmount = res.registerBonus?.amount;
      const rewardAmount = res.inviteReward?.inviteeAmount;
      if (typeof bonusAmount === "number" && bonusAmount > 0) {
        toast.success(`注册成功，赠送 ${bonusAmount} 算力已到账`);
      } else if (typeof rewardAmount === "number" && rewardAmount > 0) {
        toast.success(`注册成功，邀请奖励 ${rewardAmount} 算力已到账`);
      } else if (inviteCode.trim()) {
        toast.success("注册成功，邀请关系已绑定");
      }
      router.replace("/");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "注册失败";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell title="注册" subtitle="创建账号，项目与素材将仅属于你">
      {registrationOpen === false ? (
        <div className="space-y-4 text-center">
          <p className="text-sm text-white/70">当前已关闭新用户注册，请联系管理员或使用已有账号登录。</p>
          <Link href="/login" className="inline-block text-primary hover:underline">
            返回登录
          </Link>
        </div>
      ) : (
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
          <CaptchaField
            ref={captchaRef}
            scene="user_register"
            enabled={captchaEnabled}
            disabled={loading}
          />
          <SmsCodeField
            phone={phone}
            scene="user_register"
            value={code}
            onChange={setCode}
            disabled={loading}
            resolveCaptchaTicket={captchaEnabled ? resolveCaptchaTicket : undefined}
            onCaptchaNeedRefresh={captchaEnabled ? refreshCaptcha : undefined}
            onCaptchaPrepareResend={captchaEnabled ? prepareCaptchaResend : undefined}
          />
          <label className="block">
            <span className="mb-1.5 block text-sm text-white/60">昵称（可选）</span>
            <input
              type="text"
              autoComplete="nickname"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="显示名称"
              className="w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm text-white/60">邀请码（可选）</span>
            <input
              type="text"
              value={inviteCode}
              onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
              placeholder="6 位邀请码"
              maxLength={6}
              className="w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 font-mono tracking-widest text-white outline-none focus:border-primary/50"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm text-white/60">密码</span>
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
            <span className="mb-1.5 block text-sm text-white/60">确认密码</span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              placeholder="再次输入密码"
              className="w-full rounded-lg border border-white/10 bg-black/20 px-4 py-2.5 text-white outline-none focus:border-primary/50"
              required
            />
          </label>
          <button
            type="submit"
            disabled={loading || registrationOpen === null}
            className="w-full rounded-lg bg-primary py-2.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {loading ? "注册中…" : "注册并登录"}
          </button>
          <p className="text-center text-sm text-white/50">
            已有账号？{" "}
            <Link href="/login" className="text-primary hover:underline">
              登录
            </Link>
          </p>
        </form>
      )}
    </AuthShell>
  );
}
