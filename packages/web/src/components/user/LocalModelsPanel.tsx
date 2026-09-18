"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createMyLocalModel,
  deleteMyLocalModel,
  listMyLocalModels,
} from "@/lib/api/localModels";

/** 个人中心：把用户自己的本地图/视频模型挂到画布。 */
export function LocalModelsPanel() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["me", "local-models"],
    queryFn: listMyLocalModels,
  });
  const [displayName, setDisplayName] = useState("");
  const [category, setCategory] = useState<"image" | "video">("image");
  const [backend, setBackend] = useState<"comfyui" | "openai_compat">("comfyui");
  const [upstream, setUpstream] = useState("");
  const [comfyUrl, setComfyUrl] = useState("http://127.0.0.1:8188");
  const [localBase, setLocalBase] = useState("http://127.0.0.1:11434/v1");
  const [workflow, setWorkflow] = useState("");

  const createMut = useMutation({
    mutationFn: async () => {
      let wf: unknown;
      if (workflow.trim()) {
        wf = JSON.parse(workflow);
      }
      return createMyLocalModel({
        displayName,
        category,
        backend,
        upstreamModel: upstream,
        comfyBaseUrl: backend === "comfyui" ? comfyUrl : undefined,
        localApiBase: backend === "openai_compat" ? localBase : undefined,
        comfyWorkflow: wf,
      });
    },
    onSuccess: () => {
      toast.success("已添加到画布模型列表");
      setDisplayName("");
      setUpstream("");
      setWorkflow("");
      queryClient.invalidateQueries({ queryKey: ["me", "local-models"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err: Error) => toast.error(err.message || "添加失败"),
  });

  const delMut = useMutation({
    mutationFn: (id: string) => deleteMyLocalModel(id),
    onSuccess: () => {
      toast.success("已移除");
      queryClient.invalidateQueries({ queryKey: ["me", "local-models"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err: Error) => toast.error(err.message || "删除失败"),
  });

  return (
    <div className="space-y-4 text-sm">
      <p className="text-xs text-muted-foreground">
        把你电脑上的模型接到画布：ComfyUI 填权重文件名；Ollama / vLLM / SGLang 填 OpenAI 兼容地址和
        model id。视频类 ComfyUI 模型请粘贴「另存为 API 格式」工作流，占位符可用
        {" {{PROMPT}} / {{MODEL}} / {{IMAGE}}"}。
      </p>

      {isLoading ? (
        <p className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载中…
        </p>
      ) : (
        <ul className="space-y-2">
          {(data?.items ?? []).length === 0 ? (
            <li className="text-xs text-muted-foreground">还没有自建本地模型</li>
          ) : (
            (data?.items ?? []).map((m) => (
              <li
                key={m.id}
                className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{m.displayName}</p>
                  <p className="truncate font-mono text-[11px] text-muted-foreground">
                    {m.category === "video" ? "视频" : "图片"} · {m.provider} · {m.upstreamModel || m.name}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => delMut.mutate(m.id)}
                  disabled={delMut.isPending}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </li>
            ))
          )}
        </ul>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        <Input
          placeholder="显示名称，例如 我的 SDXL"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
        />
        <Input
          placeholder={backend === "comfyui" ? "ComfyUI 文件名，如 xxx.safetensors" : "上游 model id"}
          value={upstream}
          onChange={(e) => setUpstream(e.target.value)}
        />
        <select
          className="h-9 rounded-md border border-border bg-background px-2"
          value={category}
          onChange={(e) => setCategory(e.target.value as "image" | "video")}
        >
          <option value="image">图片模型</option>
          <option value="video">视频模型</option>
        </select>
        <select
          className="h-9 rounded-md border border-border bg-background px-2"
          value={backend}
          onChange={(e) => setBackend(e.target.value as "comfyui" | "openai_compat")}
        >
          <option value="comfyui">ComfyUI（本机 8188）</option>
          <option value="openai_compat">OpenAI 兼容（Ollama / vLLM / SGLang）</option>
        </select>
        {backend === "comfyui" ? (
          <Input
            className="sm:col-span-2"
            placeholder="ComfyUI 地址"
            value={comfyUrl}
            onChange={(e) => setComfyUrl(e.target.value)}
          />
        ) : (
          <Input
            className="sm:col-span-2"
            placeholder="API Base，如 http://127.0.0.1:11434/v1"
            value={localBase}
            onChange={(e) => setLocalBase(e.target.value)}
          />
        )}
        {backend === "comfyui" ? (
          <textarea
            className="min-h-[88px] sm:col-span-2 rounded-md border border-border bg-background p-2 font-mono text-[11px]"
            placeholder="可选：ComfyUI API 工作流 JSON（视频模型建议填写）"
            value={workflow}
            onChange={(e) => setWorkflow(e.target.value)}
          />
        ) : null}
      </div>
      <Button
        onClick={() => createMut.mutate()}
        disabled={createMut.isPending || !displayName.trim() || !upstream.trim()}
      >
        <Plus className="mr-1 h-4 w-4" />
        {createMut.isPending ? "添加中…" : "添加到画布"}
      </Button>
    </div>
  );
}
