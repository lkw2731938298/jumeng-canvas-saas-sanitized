"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createAdminSensitiveWord,
  deleteAdminSensitiveWord,
  importAdminSensitiveWords,
  listAdminSensitiveWords,
  testAdminSensitiveText,
  updateAdminSensitiveWord,
} from "@/lib/api/admin";

export default function AdminSensitiveWordsPage() {
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [newWord, setNewWord] = useState("");
  const [importText, setImportText] = useState("");
  const [testText, setTestText] = useState("");
  const [testResult, setTestResult] = useState<{ ok: boolean; matchedWords: string[] } | null>(
    null
  );

  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["admin", "sensitive-words", search],
    queryFn: () => listAdminSensitiveWords({ q: search || undefined, pageSize: 100 }),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "sensitive-words"] });
  };

  const createMutation = useMutation({
    mutationFn: () => createAdminSensitiveWord(newWord.trim()),
    onSuccess: () => {
      toast.success("已添加敏感词");
      setNewWord("");
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "添加失败"),
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      updateAdminSensitiveWord(id, { enabled }),
    onSuccess: () => {
      toast.success("已更新");
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "更新失败"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAdminSensitiveWord(id),
    onSuccess: () => {
      toast.success("已删除");
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "删除失败"),
  });

  const importMutation = useMutation({
    mutationFn: () => importAdminSensitiveWords({ text: importText }),
    onSuccess: (res) => {
      toast.success(`导入完成：新增 ${res.added}，跳过 ${res.skipped}`);
      setImportText("");
      invalidate();
    },
    onError: (err: Error) => toast.error(err.message || "导入失败"),
  });

  const testMutation = useMutation({
    mutationFn: () => testAdminSensitiveText(testText),
    onSuccess: (res) => {
      setTestResult(res);
      if (res.ok) toast.success("未命中敏感词");
      else toast.error(`命中：${res.matchedWords.join("、")}`);
    },
    onError: (err: Error) => toast.error(err.message || "试检失败"),
  });

  const items = data?.items ?? [];

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <AdminHeader
        title="敏感词"
        description="保存在 MySQL；启用词写入 Redis 单 key（cache:sensitive_words:active，有效期 1 天）。过期后回源数据库再加载缓存。用户点生成时先校验，命中则阻断。"
      />

      <div className="mb-6 flex flex-wrap items-end gap-2">
        <div className="min-w-[200px] flex-1">
          <label className="mb-1 block text-xs text-muted-foreground">新增敏感词</label>
          <Input
            value={newWord}
            onChange={(e) => setNewWord(e.target.value)}
            placeholder="输入后添加"
            onKeyDown={(e) => {
              if (e.key === "Enter" && newWord.trim()) createMutation.mutate();
            }}
          />
        </div>
        <Button
          type="button"
          onClick={() => createMutation.mutate()}
          disabled={!newWord.trim() || createMutation.isPending}
        >
          {createMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Plus className="h-4 w-4" />
          )}
          <span className="ml-1">添加</span>
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => void refetch()}
          disabled={isFetching}
        >
          <RefreshCw className={`h-4 w-4 ${isFetching ? "animate-spin" : ""}`} />
        </Button>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        <Input
          className="max-w-xs"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索词条"
          onKeyDown={(e) => {
            if (e.key === "Enter") setSearch(q.trim());
          }}
        />
        <Button type="button" variant="secondary" onClick={() => setSearch(q.trim())}>
          <Search className="mr-1 h-4 w-4" />
          搜索
        </Button>
      </div>

      <div className="mb-8 overflow-hidden rounded-xl border border-border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">词条</th>
              <th className="px-4 py-3 font-medium">规范化</th>
              <th className="px-4 py-3 font-medium">启用</th>
              <th className="px-4 py-3 font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                  暂无敏感词
                </td>
              </tr>
            ) : (
              items.map((row) => (
                <tr key={row.id} className="border-t border-border">
                  <td className="px-4 py-3">{row.word}</td>
                  <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                    {row.wordNorm}
                  </td>
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={row.enabled}
                      onChange={(e) =>
                        toggleMutation.mutate({ id: row.id, enabled: e.target.checked })
                      }
                      disabled={toggleMutation.isPending}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="text-destructive"
                      onClick={() => {
                        if (window.confirm(`确认删除「${row.word}」？`)) {
                          deleteMutation.mutate(row.id);
                        }
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {data ? (
          <p className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
            共 {data.total} 条
          </p>
        ) : null}
      </div>

      <section className="mb-8">
        <h2 className="mb-2 text-sm font-medium">批量导入</h2>
        <p className="mb-2 text-xs text-muted-foreground">每行一个词，已存在的会跳过。</p>
        <textarea
          className="min-h-[100px] w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          placeholder={"词1\n词2\n词3"}
        />
        <Button
          type="button"
          className="mt-2"
          variant="secondary"
          disabled={!importText.trim() || importMutation.isPending}
          onClick={() => importMutation.mutate()}
        >
          {importMutation.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          导入
        </Button>
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium">试检</h2>
        <textarea
          className="min-h-[80px] w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          value={testText}
          onChange={(e) => setTestText(e.target.value)}
          placeholder="粘贴一段提示词试检是否命中"
        />
        <Button
          type="button"
          className="mt-2"
          variant="outline"
          disabled={!testText.trim() || testMutation.isPending}
          onClick={() => testMutation.mutate()}
        >
          {testMutation.isPending ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          试检
        </Button>
        {testResult ? (
          <p className="mt-2 text-sm text-muted-foreground">
            {testResult.ok
              ? "通过：未命中"
              : `命中词：${testResult.matchedWords.join("、")}`}
          </p>
        ) : null}
      </section>
    </div>
  );
}
