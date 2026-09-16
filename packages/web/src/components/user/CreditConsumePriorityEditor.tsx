"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, GripVertical, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  CREDIT_TYPE_LABELS,
  DEFAULT_CONSUME_PRIORITY,
  getConsumePriority,
  putConsumePriority,
} from "@/lib/api/credits";
import { cn } from "@/lib/utils";

interface CreditConsumePriorityEditorProps {
  initialPriority?: string[];
  onSaved?: (priority: string[]) => void;
  className?: string;
}

export function CreditConsumePriorityEditor({
  initialPriority,
  onSaved,
  className,
}: CreditConsumePriorityEditorProps) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["credits", "consume-priority"],
    queryFn: getConsumePriority,
    staleTime: 30_000,
  });

  const [priority, setPriority] = useState<string[]>(
    initialPriority ?? [...DEFAULT_CONSUME_PRIORITY]
  );

  useEffect(() => {
    if (data?.priority?.length) {
      setPriority(data.priority);
    }
  }, [data?.priority]);

  const saveMutation = useMutation({
    mutationFn: (next: string[]) => putConsumePriority(next),
    onSuccess: (result) => {
      setPriority(result.priority);
      queryClient.setQueryData(["credits", "consume-priority"], result);
      queryClient.invalidateQueries({ queryKey: ["credits", "balance"] });
      onSaved?.(result.priority);
      toast.success("消耗顺序已保存");
    },
    onError: (err: Error) => {
      toast.error(err.message || "保存失败");
    },
  });

  const move = (index: number, direction: -1 | 1) => {
    const next = [...priority];
    const target = index + direction;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setPriority(next);
  };

  const labels = data?.labels ?? CREDIT_TYPE_LABELS;
  const dirty =
    data?.priority?.length &&
    JSON.stringify(priority) !== JSON.stringify(data.priority);

  if (isLoading && !priority.length) {
    return (
      <div className={cn("flex items-center gap-2 text-sm text-white/50", className)}>
        <Loader2 className="h-4 w-4 animate-spin" />
        加载消耗顺序…
      </div>
    );
  }

  return (
    <div className={className}>
      <p className="mb-3 text-xs text-white/50">
        生成任务扣费时，将按从上到下的顺序优先消耗对应类型的算力；同类型内优先消耗即将过期的批次。
      </p>
      <ol className="space-y-2">
        {priority.map((type, index) => (
          <li
            key={type}
            className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/5 px-3 py-2"
          >
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/10 text-xs font-medium text-white/70">
              {index + 1}
            </span>
            <GripVertical className="h-4 w-4 shrink-0 text-white/30" />
            <span className="min-w-0 flex-1 text-sm text-white/90">
              {labels[type] ?? formatFallbackLabel(type)}
            </span>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                disabled={index === 0}
                onClick={() => move(index, -1)}
                className="rounded-lg p-1.5 text-white/60 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-30"
                aria-label="上移"
              >
                <ArrowUp className="h-4 w-4" />
              </button>
              <button
                type="button"
                disabled={index === priority.length - 1}
                onClick={() => move(index, 1)}
                className="rounded-lg p-1.5 text-white/60 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-30"
                aria-label="下移"
              >
                <ArrowDown className="h-4 w-4" />
              </button>
            </div>
          </li>
        ))}
      </ol>
      <div className="mt-4 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => data?.priority && setPriority(data.priority)}
          disabled={!dirty || saveMutation.isPending}
          className="rounded-lg px-3 py-1.5 text-sm text-white/60 transition-colors hover:bg-white/10 hover:text-white disabled:opacity-40"
        >
          重置
        </button>
        <button
          type="button"
          onClick={() => saveMutation.mutate(priority)}
          disabled={!dirty || saveMutation.isPending}
          className="flex items-center gap-2 rounded-lg border border-white/15 bg-white/10 px-4 py-1.5 text-sm text-white/90 transition-colors hover:bg-white/15 disabled:opacity-40"
        >
          {saveMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          保存顺序
        </button>
      </div>
    </div>
  );
}

function formatFallbackLabel(type: string): string {
  return CREDIT_TYPE_LABELS[type] ?? type;
}
