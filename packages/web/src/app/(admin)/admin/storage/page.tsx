"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HardDrive, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  getAdminStorageSettings,
  updateAdminStorageSettings,
} from "@/lib/api/admin";

export default function AdminStoragePage() {
  const queryClient = useQueryClient();
  const [gbInput, setGbInput] = useState("");

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["admin", "storage-settings"],
    queryFn: getAdminStorageSettings,
  });

  const saveMutation = useMutation({
    mutationFn: () => updateAdminStorageSettings(Number(gbInput)),
    onSuccess: () => {
      toast.success("通用存储配额已更新");
      void queryClient.invalidateQueries({ queryKey: ["admin", "storage-settings"] });
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const currentGb = data?.defaultStorageGb ?? 2;

  return (
    <div className="space-y-6">
      <AdminHeader
        title="内存管理"
        description="配置全平台用户的通用云存储配额（GiB）。会员到期后仍适用此配额；会员套餐可额外增加空间。"
      />

      <div className="mb-4">
        <Button variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          <RefreshCw className={`mr-2 h-4 w-4 ${isFetching ? "animate-spin" : ""}`} />
          刷新
        </Button>
      </div>

      <div className="max-w-lg rounded-xl border border-border bg-card p-6">
        <div className="mb-4 flex items-center gap-2 text-muted-foreground">
          <HardDrive className="h-5 w-5" />
          <span className="text-sm font-medium">通用存储配额</span>
        </div>

        {isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载中…
          </div>
        ) : (
          <>
            <p className="mb-4 text-sm text-muted-foreground">
              当前：<span className="font-mono text-foreground">{currentGb} GiB</span>
              <span className="ml-2 text-xs">（系统默认 {data?.envDefaultStorageGb ?? 2} GiB）</span>
            </p>
            <div className="space-y-3">
              <Label htmlFor="defaultStorageGb">通用配额（GiB）</Label>
              <Input
                id="defaultStorageGb"
                type="number"
                min={0}
                max={10000}
                placeholder={String(currentGb)}
                value={gbInput}
                onChange={(e) => setGbInput(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                按用户名下全部项目的 OSS 素材占用汇总；协作项目计入项目主人空间。
              </p>
              <Button
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending || !gbInput.trim()}
              >
                {saveMutation.isPending ? "保存中…" : "保存"}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
