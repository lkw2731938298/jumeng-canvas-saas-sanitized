"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Award,
  BookOpen,
  CircleHelp,
  Folder,
  Gem,
  Home,
  LogIn,
  Plus,
  ScrollText,
  Smile,
  User,
  Zap,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { LoginModal } from "@/components/auth/LoginModal";
import { UserAccountMenu } from "@/components/user/UserAccountMenu";
import { NotificationBell } from "@/components/huabu/NotificationBell";
import { performLogout } from "@/lib/auth/session";
import { createProject } from "@/lib/api/projects";
import { getSiteFooter } from "@/lib/api/site";
import { withBasePath } from "@/lib/basePath";
import { useCreditBalance } from "@/lib/canvas/useGenerationCreditQuote";
import { useAuthStore } from "@/stores/authStore";
import type { Project } from "@/types";
import "./huabuTheme.css";
import "./huabuTheme.tokens.css";

/** 顶栏教程：仅允许 http(s) 或站内绝对路径 */
function resolveHelpOpenUrl(raw: string | undefined | null): string {
  const url = String(raw || "").trim();
  if (!url) return "";
  const lower = url.toLowerCase();
  if (lower.startsWith("https://") || lower.startsWith("http://")) return url;
  if (url.startsWith("/") && !url.startsWith("//")) return withBasePath(url);
  return "";
}

type HuabuPublicShellProps = {
  children: React.ReactNode;
};

const sideNavItems = [
  { icon: Home, label: "发现", href: "/" },
  { icon: Plus, label: "新建", href: null },
  { icon: Folder, label: "项目", href: "/projects" },
  { icon: Smile, label: "资产", href: null },
  { icon: BookOpen, label: "技能", href: "/skills" },
] as const;

/** huabu 公共外壳：品牌、顶栏账户区、侧栏导航、微信入口，作用域限定在 .huabu-scope。 */
export function HuabuPublicShell({ children }: HuabuPublicShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const { data: creditBalance } = useCreditBalance(Boolean(user));
  const [loginOpen, setLoginOpen] = useState(false);
  const [afterLoginPath, setAfterLoginPath] = useState<string | null>(null);
  // 顶栏教程链接：后台「首页内容」配置
  const { data: siteFooter } = useQuery({
    queryKey: ["site", "footer"],
    queryFn: getSiteFooter,
    staleTime: 5 * 60_000,
  });
  const helpUrl = resolveHelpOpenUrl(siteFooter?.helpUrl);
  // 顶栏 / 侧栏「教程」共用：未配置 toast；外链新窗口；站内路径路由跳转
  const openHelp = () => {
    if (!helpUrl) {
      toast.message("教程链接尚未配置");
      return;
    }
    if (helpUrl.startsWith("http://") || helpUrl.startsWith("https://")) {
      window.open(helpUrl, "_blank", "noopener,noreferrer");
      return;
    }
    router.push(helpUrl);
  };
  // 侧栏微信弹层：与后台「首页内容 → 联系我们·二维码」同一配置
  const wechatQrCodes = (siteFooter?.contactUs?.qrCodes ?? []).filter((q) =>
    Boolean(String(q.imageUrl || "").trim())
  );

  const isActivities = pathname === "/activities";

  const createMutation = useMutation({
    mutationFn: () => createProject("未命名项目"),
    onSuccess: (project: Project) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/${project.id}`);
    },
    onError: () => toast.error("新建项目失败，请稍后重试"),
  });

  const balance =
    creditBalance?.creditsEnabled && creditBalance.balance != null
      ? creditBalance.balance
      : (user?.computePower ?? 0);

  const requireLogin = () => {
    if (user) return true;
    setAfterLoginPath(null);
    setLoginOpen(true);
    return false;
  };

  const openLogin = (nextPath?: string | null) => {
    setAfterLoginPath(nextPath || null);
    setLoginOpen(true);
  };

  return (
    <div className="huabu-scope">
      <div className="app-shell">
        <div className="noise" aria-hidden="true" />

        <header className="topbar">
          <div className="topbar-actions">
            <Link
              className={`topbar-challenge${isActivities ? " is-active" : ""}`}
              href="/activities"
            >
              <Award size={13} strokeWidth={1.8} />
              <span className="challenge-text">活动</span>
            </Link>

            <a
              className="topbar-icon-btn"
              href="https://example.com/"
              target="_blank"
              rel="noopener noreferrer"
              aria-label="剧本平台"
              title="剧本平台"
            >
              <ScrollText size={17} strokeWidth={1.7} />
            </a>

            <button
              className="topbar-icon-btn"
              type="button"
              aria-label="教程"
              title="教程"
              onClick={openHelp}
            >
              <CircleHelp size={17} strokeWidth={1.7} />
            </button>

            <button
              className="topbar-store"
              type="button"
              onClick={() => {
                if (user) router.push("/account");
                else openLogin("/account");
              }}
            >
              <User size={15} strokeWidth={1.8} />
              <span>个人中心</span>
            </button>

            <div className="topbar-member">
              <NotificationBell enabled={Boolean(user)} />
              {user ? (
                <UserAccountMenu
                  displayName={user.displayName ?? "用户"}
                  variant="projects"
                  onLogout={() => {
                    void performLogout(queryClient);
                    router.push("/login");
                  }}
                  trigger={
                    <span className="member-panel" aria-label="会员中心">
                      <Gem size={15} strokeWidth={1.8} className="member-gem" />
                      <span className="member-label">会员中心</span>
                      <span className="member-credits">
                        <Zap size={13} strokeWidth={2.2} fill="currentColor" />
                        {balance.toLocaleString()}
                      </span>
                      <span className="member-avatar" aria-hidden="true">
                        <svg viewBox="0 0 24 24" className="avatar-mascot" aria-hidden="true">
                          <path d="M12 4L20 18H4L12 4Z" fill="currentColor" />
                          <circle cx="9.5" cy="13" r="1.1" fill="#1a1028" />
                          <circle cx="14.5" cy="13" r="1.1" fill="#1a1028" />
                          <path d="M10 15.5Q12 17 14 15.5" stroke="#1a1028" strokeWidth="1.2" fill="none" strokeLinecap="round" />
                        </svg>
                        <span className="avatar-dot" />
                      </span>
                    </span>
                  }
                />
              ) : (
                <button
                  type="button"
                  className="member-panel"
                  aria-label="登录或注册"
                  onClick={() => openLogin()}
                >
                  <LogIn size={15} strokeWidth={1.8} className="member-gem" />
                  <span className="member-label">登录 / 注册</span>
                </button>
              )}
            </div>
          </div>
        </header>

        {/* 品牌区复用画布顶栏：logo +「聚梦 画布」 */}
        <Link className="brand-logo" href="/" aria-label="聚梦画布">
          <Image
            src={withBasePath("/brand-logo.png")}
            alt="聚梦画布"
            width={36}
            height={20}
            className="brand-logo-img"
            priority
            unoptimized
          />
          <span className="brand-name brand-name-primary">聚梦</span>
          <span className="brand-name-rest">画布</span>
        </Link>

        <aside className="sidebar">
          <nav className="side-nav" aria-label="侧边导航">
            {sideNavItems.map(({ icon: Icon, label, href }) => {
              const active =
                Boolean(
                  href &&
                    (pathname === href || (href === "/" && pathname === "/discover")),
                ) && !isActivities;
              const content = (
                <>
                  <Icon size={22} strokeWidth={1.6} absoluteStrokeWidth />
                  <span>{label}</span>
                </>
              );
              if (href) {
                return (
                  <Link
                    key={label}
                    href={href}
                    className={active ? "active" : undefined}
                  >
                    {content}
                  </Link>
                );
              }
              return (
                <button
                  type="button"
                  key={label}
                  onClick={() => {
                    if (!requireLogin()) return;
                    if (label === "新建") createMutation.mutate();
                    else toast.message("资产中心即将上线");
                  }}
                  disabled={label === "新建" && createMutation.isPending}
                >
                  {content}
                </button>
              );
            })}
            {/* 技能下方：教程入口，链接与顶栏教程按钮同源（后台 helpUrl） */}
            <button type="button" aria-label="教程" title="教程" onClick={openHelp}>
              <CircleHelp size={22} strokeWidth={1.6} absoluteStrokeWidth />
              <span>教程</span>
            </button>
          </nav>
          <div className="side-wechat-wrap">
            <button
              className="side-wechat"
              type="button"
              aria-label="微信"
              title={
                wechatQrCodes.length
                  ? siteFooter?.contactUs?.label || "微信"
                  : "请在后台「首页内容」上传微信二维码"
              }
              onClick={() => {
                if (!wechatQrCodes.length) {
                  toast.message("微信二维码未配置，请到管理后台「首页内容」上传");
                }
              }}
            >
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path
                  fill="currentColor"
                  d="M9.5 4C5.91 4 3 6.48 3 9.55c0 1.77.92 3.35 2.36 4.4l-.6 2.1 2.36-1.18c.7.2 1.44.3 2.2.3.2 0 .4 0 .59-.02A4.8 4.8 0 0 1 9.3 13.5c0-2.9 2.72-5.25 6.07-5.25.14 0 .28 0 .42.02C15.1 5.66 12.5 4 9.5 4Zm-2.2 3.2a.85.85 0 1 1 0 1.7.85.85 0 0 1 0-1.7Zm4.1 0a.85.85 0 1 1 0 1.7.85.85 0 0 1 0-1.7Zm4 2.1c-2.9 0-5.25 1.95-5.25 4.35S12.5 18 15.4 18c.62 0 1.22-.1 1.78-.28l1.82.9-.45-1.6A3.9 3.9 0 0 0 20.1 14.65c0-2.4-2.35-4.35-5.25-4.35Zm-1.7 2.55a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4Zm3.4 0a.7.7 0 1 1 0 1.4.7.7 0 0 1 0-1.4Z"
                />
              </svg>
            </button>
            {wechatQrCodes.length > 0 ? (
              <div className="wechat-popover" role="tooltip">
                {wechatQrCodes.map((qr) => (
                  <div className="wechat-qr-item" key={qr.id}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={qr.imageUrl} alt={qr.label || "微信二维码"} />
                    <span>{qr.label || "扫码联系"}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </aside>

        <main>{children}</main>
      </div>

      <LoginModal
        open={loginOpen}
        onOpenChange={setLoginOpen}
        onLoggedIn={() => {
          if (afterLoginPath) {
            const next = afterLoginPath;
            setAfterLoginPath(null);
            router.push(next);
          }
        }}
      />
    </div>
  );
}
