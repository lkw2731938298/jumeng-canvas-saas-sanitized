"use client";

import { Crown } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MembershipPanel } from "@/components/user/MembershipPanel";

interface MembershipDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** 账户菜单「会员订阅」弹窗：套餐列表 + 支付宝扫码开通 */
export function MembershipDialog({ open, onOpenChange }: MembershipDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* 与个人中心会员区一致：深色玻璃底，适配 MembershipPanel 的 white/ 文案 */}
      <DialogContent className="max-h-[min(85vh,720px)] overflow-y-auto border-white/10 bg-[#1a1a28] text-white sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-white">
            <Crown className="h-4 w-4 text-amber-400" />
            会员订阅
          </DialogTitle>
          <DialogDescription className="text-white/55">
            开通月会员，每账期赠送限时会员订阅算力；支付成功后自动到账。
          </DialogDescription>
        </DialogHeader>
        {/* 打开时再拉取套餐 / 当前订阅，避免账户菜单未点时多余请求 */}
        <MembershipPanel enabled={open} />
      </DialogContent>
    </Dialog>
  );
}
