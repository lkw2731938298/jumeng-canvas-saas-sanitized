"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { SimpleMarkdown } from "@/components/auth/SimpleMarkdown";
import { getSiteLegalDocument, type SiteLegalDocType } from "@/lib/api/site";
import "./legalDocumentDialog.css";

type LegalDocumentDialogProps = {
  docType: SiteLegalDocType | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/** 登录弹窗内展示用户协议 / 隐私政策正文。 */
export function LegalDocumentDialog({ docType, open, onOpenChange }: LegalDocumentDialogProps) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["site", "legal", docType],
    queryFn: () => getSiteLegalDocument(docType!),
    enabled: open && Boolean(docType),
    staleTime: 5 * 60 * 1000,
  });

  const title = data?.title || (docType === "user-agreement" ? "用户协议" : "隐私政策");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        overlayClassName="legal-document-overlay z-[80]"
        className="legal-document-dialog z-[81] p-0"
      >
        <header className="legal-document-header">
          <DialogTitle className="legal-document-title">{title}</DialogTitle>
          <button
            type="button"
            className="legal-document-close"
            aria-label="关闭"
            onClick={() => onOpenChange(false)}
          >
            <X size={18} />
          </button>
        </header>
        <div className="legal-document-body">
          {isLoading ? (
            <div className="legal-document-status">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中…
            </div>
          ) : isError ? (
            <p className="legal-document-status is-error">
              {error instanceof Error ? error.message : "加载失败"}
            </p>
          ) : !data?.configured || !data.markdown.trim() ? (
            <p className="legal-document-status">暂未配置文档内容</p>
          ) : (
            <SimpleMarkdown source={data.markdown} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
