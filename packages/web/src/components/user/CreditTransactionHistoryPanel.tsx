"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { CreditTransactionTable } from "@/components/credits/CreditTransactionTable";
import { Button } from "@/components/ui/button";
import { listCreditTransactions } from "@/lib/api/credits";

interface CreditTransactionHistoryPanelProps {
  variant?: "default" | "account";
  pageSize?: number;
}

export function CreditTransactionHistoryPanel({
  variant = "default",
  pageSize = 10,
}: CreditTransactionHistoryPanelProps) {
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: ["credits", "transactions", page, pageSize],
    queryFn: () => listCreditTransactions(page, pageSize),
  });

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));
  const isAccount = variant === "account";

  return (
    <div className="space-y-3">
      <CreditTransactionTable
        items={data?.items ?? []}
        loading={isLoading}
        variant={variant}
        showId={false}
        jobLinkPrefix={undefined}
      />
      {(data?.total ?? 0) > pageSize ? (
        <div
          className={
            isAccount
              ? "flex items-center justify-between text-xs text-white/50"
              : "flex items-center justify-between text-sm text-muted-foreground"
          }
        >
          <span>
            第 {page} / {totalPages} 页，共 {data?.total ?? 0} 条
          </span>
          <div className="flex gap-2">
            <Button
              type="button"
              size="sm"
              variant={isAccount ? "ghost" : "outline"}
              className={isAccount ? "text-white/70 hover:bg-white/10 hover:text-white" : undefined}
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              size="sm"
              variant={isAccount ? "ghost" : "outline"}
              className={isAccount ? "text-white/70 hover:bg-white/10 hover:text-white" : undefined}
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
