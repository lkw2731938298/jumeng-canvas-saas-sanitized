"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { discoverAdminComfyuiModels, syncAdminComfyuiModels } from "@/lib/api/admin";

/** 管理端：从用户自己的 ComfyUI 一键同步本机图/视频权重，不绑定固定型号。 */
export function ComfyUISyncPanel() {
  const queryClient = useQueryClient();
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8188");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [categoryOverride, setCategoryOverride] = useState<Record<string, string>>({});

  const discover = useQuery({
    queryKey: ["admin", "comfyui-discover", baseUrl],
    queryFn: () => discoverAdminComfyuiModels(baseUrl),
    enabled: false,
  });

  const items = discover.data?.items ?? [];
  const keyOf = (folder: string, filename: string) => `${folder}::${filename}`;

  const selectedItems = useMemo(() => {
    return items
      .filter((it) => selected[keyOf(it.folder, it.filename)])
      .map((it) => ({
        filename: it.filename,
        folder: it.folder,
        category: categoryOverride[keyOf(it.folder, it.filename)] || it.category,
        displayName: it.displayName,
      }));
  }, [items, selected, categoryOverride]);

  const syncMut = useMutation({
    mutationFn: () =>
      syncAdminComfyuiModels({
        baseUrl,
        items: items.length ? selectedItems : undefined,
      }),
    onSuccess: (res) => {
      toast.success(`已导入 ${res.created} 个，跳过已存在 ${res.skipped} 个`);
      queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err: Error) => toast.error(err.message || "同步失败"),
  });

  const toggleAll = (on: boolean) => {
    const next: Record<string, boolean> = {};
    for (const it of items) next[keyOf(it.folder, it.filename)] = on;
    setSelected(next);
  };

  return (
    <section className="mb-6 rounded-xl border border-border bg-card/60 p-4">
      <h3 className="text-sm font-medium">从本机 ComfyUI 同步模型</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        填写你自己的 ComfyUI 地址（默认 8188）。Docker 里的画布访问宿主机请用
        http://host.docker.internal:8188。探测到的是该实例实际加载的权重，可改图片/视频分类后再导入。
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <label className="text-xs text-muted-foreground">ComfyUI 地址</label>
          <Input
            className="w-[320px]"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://127.0.0.1:8188"
          />
        </div>
        <Button
          variant="outline"
          onClick={() => {
            void discover.refetch().then((r) => {
              if (r.data?.items) {
                const next: Record<string, boolean> = {};
                for (const it of r.data.items) next[keyOf(it.folder, it.filename)] = true;
                setSelected(next);
                toast.success(`发现 ${r.data.count} 个本机模型`);
              }
            });
          }}
          disabled={discover.isFetching}
        >
          {discover.isFetching ? "探测中…" : "探测本机模型"}
        </Button>
        <Button
          onClick={() => syncMut.mutate()}
          disabled={syncMut.isPending || (!items.length && !selectedItems.length)}
        >
          {syncMut.isPending ? "导入中…" : "导入到画布目录"}
        </Button>
        {items.length ? (
          <>
            <Button variant="ghost" size="sm" onClick={() => toggleAll(true)}>
              全选
            </Button>
            <Button variant="ghost" size="sm" onClick={() => toggleAll(false)}>
              全不选
            </Button>
          </>
        ) : null}
      </div>
      {discover.isError ? (
        <p className="mt-2 text-sm text-destructive">
          {(discover.error as Error)?.message || "无法连接 ComfyUI，请确认已启动且地址正确"}
        </p>
      ) : null}
      {items.length ? (
        <div className="mt-3 max-h-64 overflow-auto rounded-lg border border-border">
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-muted/80 text-muted-foreground">
              <tr>
                <th className="px-3 py-2">选</th>
                <th className="px-3 py-2">文件</th>
                <th className="px-3 py-2">目录</th>
                <th className="px-3 py-2">分类</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => {
                const k = keyOf(it.folder, it.filename);
                return (
                  <tr key={k} className="border-t border-border/60">
                    <td className="px-3 py-1.5">
                      <input
                        type="checkbox"
                        checked={Boolean(selected[k])}
                        onChange={(e) => setSelected((s) => ({ ...s, [k]: e.target.checked }))}
                      />
                    </td>
                    <td className="px-3 py-1.5 font-mono">{it.filename}</td>
                    <td className="px-3 py-1.5 text-muted-foreground">{it.folder}</td>
                    <td className="px-3 py-1.5">
                      <select
                        className="rounded border border-border bg-background px-1 py-0.5"
                        value={categoryOverride[k] || it.category}
                        onChange={(e) =>
                          setCategoryOverride((s) => ({ ...s, [k]: e.target.value }))
                        }
                      >
                        <option value="image">图片</option>
                        <option value="video">视频</option>
                      </select>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
