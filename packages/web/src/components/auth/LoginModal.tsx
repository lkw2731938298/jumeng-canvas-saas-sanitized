"use client";

import Image from "next/image";
import { X } from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { AuthLoginForm } from "@/components/auth/AuthLoginForm";
import { useSiteHomepage } from "@/components/site/SiteHomepageBackground";
import { resolveVisualStyleImageUrl } from "@/lib/canvas/visualStyleImage";
import { withBasePath } from "@/lib/basePath";
import "./loginModal.css";

type LoginModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onLoggedIn?: () => void;
};

/** 顶栏「登录/注册」弹窗：左图（后台可配）+ 右表单。 */
export function LoginModal({ open, onOpenChange, onLoggedIn }: LoginModalProps) {
  const { data } = useSiteHomepage();
  const leftImage = resolveVisualStyleImageUrl(data?.loginModalImageUrl || "");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        overlayClassName="bg-black/55 supports-backdrop-filter:backdrop-blur-sm z-[70]"
        className="login-modal-dialog z-[71] p-0 gap-0 overflow-hidden border-0 bg-white shadow-2xl sm:max-w-[920px]"
      >
        <DialogTitle className="sr-only">登录或注册</DialogTitle>
        <div className="login-modal">
          <div className="login-modal-left" aria-hidden={!leftImage}>
            {leftImage ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={leftImage} alt="" />
            ) : (
              <div className="login-modal-left-fallback">
                <Image
                  src={withBasePath("/brand-logo.png")}
                  alt=""
                  width={72}
                  height={40}
                  unoptimized
                />
                <p>人人都是创作者</p>
                <span>AI 驱动的内容创作平台</span>
              </div>
            )}
          </div>
          <div className="login-modal-right">
            <button
              type="button"
              className="login-modal-close"
              aria-label="关闭"
              onClick={() => onOpenChange(false)}
            >
              <X size={18} strokeWidth={2} />
            </button>
            <h2>欢迎来到聚梦画布</h2>
            <AuthLoginForm
              appearance="modal"
              defaultMode="sms"
              onLoggedIn={() => {
                onLoggedIn?.();
                onOpenChange(false);
              }}
              onNavigateAway={() => onOpenChange(false)}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
