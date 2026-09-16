"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Coins, Crown, Gift, LogOut, Mail, User, UserPlus } from "lucide-react";
import { performLogout } from "@/lib/auth/session";
import { useAppTheme } from "@/components/providers/AppThemeProvider";
import { AppThemePicker } from "@/components/user/AppThemePicker";
import { GlobalWatermarkToggle } from "@/components/user/GlobalWatermarkToggle";
import { CreditRechargeDialog } from "@/components/user/CreditRechargeDialog";
import { MembershipDialog } from "@/components/user/MembershipDialog";
import { usePendingProjectInviteCount } from "@/lib/canvas/useProjectInvites";

interface UserAccountMenuProps {
  displayName: string;
  onLogout?: () => void;
  variant?: "canvas" | "projects";
  /** 自定义触发器：传入时替换默认账户胶囊（如 huabu 会员面板），保留 hover 下拉。 */
  trigger?: React.ReactNode;
}

export function UserAccountMenu({
  displayName,
  onLogout,
  variant = "canvas",
  trigger,
}: UserAccountMenuProps) {
  const router = useRouter();
  const { themeId, selectTheme } = useAppTheme();
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [rechargeOpen, setRechargeOpen] = useState(false);
  const [membershipOpen, setMembershipOpen] = useState(false);
  const { data: pendingInviteCount = 0 } = usePendingProjectInviteCount();

  const showMenu = (menu: HTMLElement) => {
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
    menu.style.display = "block";
  };

  const hideMenu = (menu: HTMLElement) => {
    hideTimerRef.current = setTimeout(() => {
      menu.style.display = "none";
    }, 180);
  };

  const closeMenu = () => {
    if (menuRef.current) menuRef.current.style.display = "none";
  };

  const goInvites = () => {
    closeMenu();
    router.push("/invites");
  };

  const goReferral = () => {
    closeMenu();
    router.push("/referral");
  };

  const goAccount = () => {
    closeMenu();
    router.push("/account");
  };

  const handleLogout = async () => {
    if (onLogout) {
      onLogout();
      return;
    }
    await performLogout();
    router.push(variant === "projects" ? "/login" : "/projects");
  };

  const triggerClass =
    variant === "projects"
      ? "border-white/10 bg-white/5 text-white/70 hover:bg-white/10"
      : "border-border bg-[var(--chrome-frost)] text-muted-foreground hover:bg-muted/50";

  const menuClass = "border-border bg-popover/95 shadow-lg";
  const itemClass = "text-foreground/80 hover:bg-muted hover:text-foreground";
  const labelClass = "text-muted-foreground";
  const dividerClass = "border-border";

  return (
    <>
      <div
        className="relative"
        onMouseEnter={(e) => {
          showMenu(e.currentTarget.querySelector(".user-account-menu") as HTMLElement);
        }}
        onMouseLeave={(e) => {
          hideMenu(e.currentTarget.querySelector(".user-account-menu") as HTMLElement);
        }}
      >
        {trigger ? (
          <div
            role="button"
            tabIndex={0}
            onClick={goAccount}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                goAccount();
              }
            }}
            className="cursor-pointer"
          >
            {trigger}
          </div>
        ) : (
          <div
            role="button"
            tabIndex={0}
            onClick={goAccount}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                goAccount();
              }
            }}
            className={`relative flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 text-sm transition-colors ${triggerClass}`}
            style={{ backdropFilter: "blur(16px)", WebkitBackdropFilter: "blur(16px)" }}
          >
            <User className={`h-3.5 w-3.5 ${variant === "projects" ? "text-white/50" : "text-muted-foreground"}`} />
            <span className={variant === "projects" ? "text-white/90" : "text-foreground"}>{displayName}</span>
            {pendingInviteCount > 0 ? (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-medium text-primary-foreground">
                {pendingInviteCount > 9 ? "9+" : pendingInviteCount}
              </span>
            ) : null}
          </div>
        )}

        <div
          ref={menuRef}
          className={`user-account-menu absolute right-0 top-full z-40 mt-0 w-56 rounded-xl border py-1 ${menuClass}`}
          style={{ display: "none", backdropFilter: "blur(24px)", WebkitBackdropFilter: "blur(24px)" }}
          onMouseEnter={() => {
            if (hideTimerRef.current) {
              clearTimeout(hideTimerRef.current);
              hideTimerRef.current = null;
            }
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLElement).style.display = "none";
          }}
        >
          <button
            type="button"
            onClick={goInvites}
            className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${itemClass}`}
          >
            <Mail className="h-4 w-4" />
            <span className="flex-1 text-left">协作邀请</span>
            {pendingInviteCount > 0 ? (
              <span className="rounded-full bg-primary px-1.5 py-0.5 text-[10px] font-medium text-primary-foreground">
                {pendingInviteCount > 9 ? "9+" : pendingInviteCount}
              </span>
            ) : null}
          </button>
          <button
            type="button"
            onClick={goReferral}
            className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${itemClass}`}
          >
            <Gift className="h-4 w-4" />
            用户邀请
          </button>
          <button
            type="button"
            onClick={goAccount}
            className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${itemClass}`}
          >
            <User className="h-4 w-4" />
            个人中心
          </button>
          <button
            type="button"
            onClick={() => {
              closeMenu();
              setRechargeOpen(true);
            }}
            className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${itemClass}`}
          >
            <Coins className="h-4 w-4" />
            充值算力
          </button>
          <button
            type="button"
            onClick={() => {
              closeMenu();
              setMembershipOpen(true);
            }}
            className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${itemClass}`}
          >
            <Crown className="h-4 w-4" />
            会员订阅
          </button>
          <button
            type="button"
            onClick={async () => {
              closeMenu();
              await performLogout();
              router.push("/login");
            }}
            className={`flex w-full items-center gap-2 px-3 py-2 text-sm transition-colors ${itemClass}`}
          >
            <UserPlus className="h-4 w-4" />
            切换用户
          </button>
          <div className={`my-1 border-t ${dividerClass}`} />
          <GlobalWatermarkToggle itemClass={itemClass} labelClass={labelClass} />
          <AppThemePicker
            value={themeId}
            onChange={selectTheme}
            itemClass={itemClass}
            labelClass={labelClass}
          />
          <div className={`my-1 border-t ${dividerClass}`} />
          <button
            type="button"
            onClick={handleLogout}
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-red-400 transition-colors hover:bg-red-400/10"
          >
            <LogOut className="h-4 w-4" />
            退出用户
          </button>
        </div>
      </div>

      <CreditRechargeDialog open={rechargeOpen} onOpenChange={setRechargeOpen} />
      <MembershipDialog open={membershipOpen} onOpenChange={setMembershipOpen} />
    </>
  );
}
