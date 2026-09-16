"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api/client";
import { Input } from "@/components/ui/input";
import { useMemo, useState } from "react";

interface ErrorCodeRow {
  code: string;
  messageZh: string;
  category: string;
  httpStatus?: number | null;
}

const CATEGORY_LABEL: Record<string, string> = {
  auth: "认证",
  project: "项目/工作流",
  credits: "算力",
  generation: "生成",
  storage: "存储",
  upstream: "上游服务",
  admin: "管理端",
};

export default function AdminErrorCodesPage() {
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");

  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["error-codes"],
    queryFn: () => apiFetch<ErrorCodeRow[]>("/api/v1/error-codes", { skipAuth: true }),
  });

  const categories = useMemo(() => {
    const set = new Set(data.map((row) => row.category));
    return Array.from(set).sort();
  }, [data]);

  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    return data.filter((row) => {
      if (category && row.category !== category) return false;
      if (!term) return true;
      return (
        row.code.toLowerCase().includes(term) ||
        row.messageZh.toLowerCase().includes(term) ||
        row.category.toLowerCase().includes(term)
      );
    });
  }, [data, search, category]);

  return (
    <div>
      <header className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">错误信息编码表</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          平台统一错误码与中文说明映射；API 与前端按 code 展示用户可读提示。
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Input
          className="max-w-xs"
          placeholder="搜索编码或中文说明…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="h-9 rounded-md border border-border bg-background px-3 text-sm"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">全部分类</option>
          {categories.map((cat) => (
            <option key={cat} value={cat}>
              {CATEGORY_LABEL[cat] ?? cat}
            </option>
          ))}
        </select>
        <span className="text-xs text-muted-foreground">共 {filtered.length} 条</span>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">错误编码</th>
              <th className="px-4 py-3 font-medium">分类</th>
              <th className="px-4 py-3 font-medium">HTTP</th>
              <th className="px-4 py-3 font-medium">中文说明</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                  加载中…
                </td>
              </tr>
            ) : isError ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-destructive">
                  加载失败
                </td>
              </tr>
            ) : !filtered.length ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                  无匹配项
                </td>
              </tr>
            ) : (
              filtered.map((row) => (
                <tr key={row.code} className="border-b border-border/60 hover:bg-muted/20">
                  <td className="px-4 py-3 font-mono text-xs">{row.code}</td>
                  <td className="px-4 py-3 text-xs">
                    {CATEGORY_LABEL[row.category] ?? row.category}
                  </td>
                  <td className="px-4 py-3 tabular-nums text-muted-foreground">
                    {row.httpStatus ?? "—"}
                  </td>
                  <td className="px-4 py-3">{row.messageZh}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
