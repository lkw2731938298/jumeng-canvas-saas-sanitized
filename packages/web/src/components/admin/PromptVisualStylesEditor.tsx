"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ImagePlus, Link2, Loader2, Plus, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import {
  applyVisualStyleToPrompt,
  DEFAULT_VISUAL_STYLE_ID,
  validateVisualStylesConfig,
  type VisualStyleItem,
  type VisualStylesPromptToolConfig,
} from "@/lib/canvas/renderToolPrompt";
import { validateCanvasImageFile } from "@/lib/canvas/imageSizePolicy";
import { resolveVisualStyleImageUrl } from "@/lib/canvas/visualStyleImage";
import { uploadAdminVisualStyleImage } from "@/lib/api/admin";

function newStyleId(existing: VisualStyleItem[]): string {
  let n = existing.length;
  let candidate = `style_${n}`;
  const ids = new Set(existing.map((item) => item.id));
  while (ids.has(candidate)) {
    n += 1;
    candidate = `style_${n}`;
  }
  return candidate;
}

function pickImageFile(files: FileList | File[] | null | undefined): File | undefined {
  if (!files) return undefined;
  return Array.from(files).find((file) => file.type.startsWith("image/"));
}

/** 16:9 展示图：编辑区点击/拖入/粘贴上传；列表卡片点击为选中，拖入仍可上传 */
function StyleCoverUpload({
  item,
  locked,
  compact,
  uploading,
  onFile,
  onSelect,
}: {
  item: VisualStyleItem;
  locked: boolean;
  compact?: boolean;
  uploading: boolean;
  onFile: (file: File) => void;
  onSelect?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const previewUrl = resolveVisualStyleImageUrl(item.imageUrl);

  const acceptFile = (file: File | undefined) => {
    if (!file || locked || uploading) return;
    onFile(file);
  };

  if (locked) {
    return (
      <button
        type="button"
        className={cn(
          "flex aspect-video w-full items-center justify-center border border-dashed border-border bg-muted/30 text-xs text-muted-foreground",
          compact ? "rounded-none" : "rounded-xl"
        )}
        onClick={onSelect}
      >
        固定风格无需展示图
      </button>
    );
  }

  return (
    <>
      <div
        tabIndex={0}
        role={compact ? undefined : "button"}
        aria-label={previewUrl ? `更换「${item.label}」展示图` : `上传「${item.label}」展示图`}
        title={compact ? "拖入图片可直接上传" : "点击、拖入或粘贴图片，建议 16:9 横图"}
        className={cn(
          "group relative aspect-video w-full overflow-hidden border-border bg-muted/30 text-left transition-colors",
          compact ? "border-b" : "cursor-pointer rounded-xl border hover:border-primary/60",
          compact && "cursor-pointer",
          dragOver && "border-primary bg-primary/10",
          uploading && "pointer-events-none"
        )}
        onClick={() => {
          if (compact) {
            onSelect?.();
            return;
          }
          inputRef.current?.click();
        }}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          if (compact) {
            onSelect?.();
            return;
          }
          inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setDragOver(false);
          acceptFile(pickImageFile(e.dataTransfer.files));
        }}
        onPaste={
          compact
            ? undefined
            : (e) => {
                const file = pickImageFile(e.clipboardData.files);
                if (!file) return;
                e.preventDefault();
                acceptFile(file);
              }
        }
      >
        {previewUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- 运营 CDN/代理图，需随 URL 强制刷新
          <img
            key={previewUrl}
            src={previewUrl}
            alt={item.label}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1.5 px-3 text-muted-foreground">
            <ImagePlus className={cn(compact ? "h-5 w-5" : "h-7 w-7")} />
            {!compact ? (
              <>
                <span className="text-sm font-medium text-foreground">上传 16:9 展示图</span>
                <span className="text-center text-[11px] leading-relaxed">
                  点击选择、拖入文件，或在此粘贴
                </span>
              </>
            ) : (
              <span className="text-[11px]">16:9</span>
            )}
          </div>
        )}
        {compact ? (
          <button
            type="button"
            className="absolute right-1.5 top-1.5 inline-flex items-center gap-1 rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] text-white opacity-0 transition-opacity group-hover:opacity-100"
            onClick={(e) => {
              e.stopPropagation();
              inputRef.current?.click();
            }}
          >
            <Upload className="h-3 w-3" />
            {previewUrl ? "更换" : "上传"}
          </button>
        ) : (
          <span className="pointer-events-none absolute inset-x-0 bottom-0 flex items-center justify-center gap-1 bg-black/55 py-1.5 text-[11px] text-white opacity-0 transition-opacity group-hover:opacity-100">
            <Upload className="h-3 w-3" />
            {previewUrl ? "更换图片" : "上传图片"}
          </span>
        )}
        {uploading ? (
          <span className="absolute inset-0 flex items-center justify-center bg-background/70">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
          </span>
        ) : null}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp,image/bmp"
        className="hidden"
        onChange={(e) => {
          acceptFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </>
  );
}

export function defaultVisualStylesDraft(label = "视觉风格"): VisualStylesPromptToolConfig {
  return {
    kind: "visual_styles",
    menuLabel: label,
    joiner: "，",
    items: [
      {
        id: DEFAULT_VISUAL_STYLE_ID,
        label: "无",
        prompt: "",
        imageUrl: "",
        enabled: true,
        sortOrder: 0,
      },
    ],
  };
}

export function PromptVisualStylesEditor({
  value,
  onChange,
  dirty = false,
  saving = false,
  onSave,
}: {
  value: VisualStylesPromptToolConfig;
  onChange: (next: VisualStylesPromptToolConfig) => void;
  dirty?: boolean;
  saving?: boolean;
  onSave?: () => void;
}) {
  const [previewBase, setPreviewBase] = useState("一位角色站在城市天台");
  const [previewStyleId, setPreviewStyleId] = useState(DEFAULT_VISUAL_STYLE_ID);
  const [selectedId, setSelectedId] = useState(DEFAULT_VISUAL_STYLE_ID);
  const [uploadingId, setUploadingId] = useState<string | null>(null);
  const [showUrlField, setShowUrlField] = useState(false);
  const valueRef = useRef(value);
  const errors = useMemo(() => validateVisualStylesConfig(value), [value]);
  const previewPrompt = useMemo(
    () => applyVisualStyleToPrompt(previewBase, value, previewStyleId),
    [previewBase, previewStyleId, value]
  );

  useEffect(() => {
    valueRef.current = value;
  }, [value]);

  const sortedItems = [...value.items].sort(
    (a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0) || (a.id === DEFAULT_VISUAL_STYLE_ID ? -1 : 0)
  );

  const resolvedSelectedId = sortedItems.some((item) => item.id === selectedId)
    ? selectedId
    : (sortedItems[0]?.id ?? DEFAULT_VISUAL_STYLE_ID);

  const selected = sortedItems.find((item) => item.id === resolvedSelectedId) ?? sortedItems[0];
  const selectedIndex = selected ? value.items.findIndex((row) => row.id === selected.id) : -1;
  const selectedLocked = selected?.id === DEFAULT_VISUAL_STYLE_ID;

  const updateItem = (index: number, patch: Partial<VisualStyleItem>) => {
    const items = value.items.map((item, idx) => (idx === index ? { ...item, ...patch } : item));
    onChange({ ...value, items });
  };

  /** 异步上传结束后按 id 打补丁，避免覆盖上传期间其它字段的编辑 */
  const patchItemById = (id: string, patch: Partial<VisualStyleItem>) => {
    const current = valueRef.current;
    onChange({
      ...current,
      items: current.items.map((item) => (item.id === id ? { ...item, ...patch } : item)),
    });
  };

  const removeItem = (index: number) => {
    const item = value.items[index];
    if (!item || item.id === DEFAULT_VISUAL_STYLE_ID) return;
    const nextItems = value.items.filter((_, idx) => idx !== index);
    onChange({ ...value, items: nextItems });
    const fallback = [...nextItems].sort(
      (a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0) || (a.id === DEFAULT_VISUAL_STYLE_ID ? -1 : 0)
    )[0];
    setSelectedId(fallback?.id ?? DEFAULT_VISUAL_STYLE_ID);
  };

  const addItem = () => {
    const id = newStyleId(value.items);
    onChange({
      ...value,
      items: [
        ...value.items,
        {
          id,
          label: `风格${value.items.length}`,
          prompt: "",
          imageUrl: "",
          enabled: true,
          sortOrder: value.items.length,
        },
      ],
    });
    setSelectedId(id);
    setShowUrlField(false);
  };

  const handleUpload = async (styleId: string, file: File) => {
    const sizeError = validateCanvasImageFile(file);
    if (sizeError) {
      toast.error(sizeError);
      return;
    }
    if (!valueRef.current.items.some((row) => row.id === styleId)) return;
    setUploadingId(styleId);
    setSelectedId(styleId);
    try {
      const result = await uploadAdminVisualStyleImage(styleId, file);
      // 新 URL（含唯一 OSS 路径）写入草稿；须再点「保存当前工具」才落库
      patchItemById(styleId, { imageUrl: result.imageUrl });
      toast.success("展示图已上传，请再点「保存当前工具」才会生效");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploadingId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">菜单名称</label>
          <Input
            value={value.menuLabel}
            onChange={(e) => onChange({ ...value, menuLabel: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">与用户提示词拼接符</label>
          <Input
            value={value.joiner ?? "，"}
            onChange={(e) => onChange({ ...value, joiner: e.target.value })}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-sm font-medium">风格列表</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            点击卡片编辑。展示图按 16:9 显示，可点击、拖入或粘贴上传；其它比例会居中裁切预览。
          </p>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={addItem}>
          <Plus className="mr-1 h-4 w-4" />
          添加风格
        </Button>
      </div>

      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]">
        <div className="grid max-h-[min(72vh,840px)] grid-cols-2 gap-3 overflow-y-auto pr-1 sm:grid-cols-3 lg:grid-cols-4">
          {sortedItems.map((item) => {
            const locked = item.id === DEFAULT_VISUAL_STYLE_ID;
            const active = item.id === selected?.id;
            return (
              <div
                key={item.id}
                className={cn(
                  "overflow-hidden rounded-xl border bg-card text-left transition-colors",
                  active
                    ? "border-primary ring-2 ring-primary/25"
                    : "border-border hover:border-primary/40"
                )}
              >
                <StyleCoverUpload
                  item={item}
                  locked={locked}
                  compact
                  uploading={uploadingId === item.id}
                  onFile={(file) => void handleUpload(item.id, file)}
                  onSelect={() => setSelectedId(item.id)}
                />
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-1 px-2.5 py-2"
                  onClick={() => setSelectedId(item.id)}
                >
                  <span className="truncate text-sm font-medium">{item.label || item.id}</span>
                  {item.enabled === false ? (
                    <span className="shrink-0 text-[10px] text-muted-foreground">停用</span>
                  ) : null}
                </button>
              </div>
            );
          })}
        </div>

        {selected && selectedIndex >= 0 ? (
          <div className="space-y-3 rounded-xl border border-border bg-card p-4 xl:sticky xl:top-20">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">
                  {selectedLocked ? "固定风格 · 无" : selected.label || "未命名风格"}
                </p>
                <p className="text-[11px] text-muted-foreground">id: {selected.id}</p>
              </div>
              {!selectedLocked ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0 text-destructive"
                  onClick={() => removeItem(selectedIndex)}
                  aria-label="删除风格"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              ) : null}
            </div>

            <StyleCoverUpload
              item={selected}
              locked={selectedLocked}
              uploading={uploadingId === selected.id}
              onFile={(file) => void handleUpload(selected.id, file)}
            />

            {!selectedLocked ? (
              <>
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                  onClick={() => setShowUrlField((open) => !open)}
                >
                  <Link2 className="h-3 w-3" />
                  {showUrlField ? "收起图片 URL" : "或粘贴图片 URL"}
                </button>
                {showUrlField ? (
                  <Input
                    value={selected.imageUrl ?? ""}
                    onChange={(e) => updateItem(selectedIndex, { imageUrl: e.target.value })}
                    placeholder="/api/storage/object?key=... 或 https://..."
                  />
                ) : null}
                <p className="text-[11px] leading-relaxed text-muted-foreground">
                  建议 16:9（如 1920×1080）。JPG / PNG / GIF / WebP / BMP，最大 10MB。上传后写入
                  OSS，仍需保存模板才会出现在画布菜单。
                </p>
              </>
            ) : (
              <p className="text-[11px] text-muted-foreground">固定风格「无」(id=none) 不可删除。</p>
            )}

            <div className="space-y-1.5">
              <label className="text-xs text-muted-foreground">名称</label>
              <Input
                value={selected.label}
                onChange={(e) => updateItem(selectedIndex, { label: e.target.value })}
              />
            </div>

            {!selectedLocked ? (
              <div className="space-y-1.5">
                <label className="text-xs text-muted-foreground">风格提示词</label>
                <textarea
                  value={selected.prompt}
                  onChange={(e) => updateItem(selectedIndex, { prompt: e.target.value })}
                  rows={5}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  placeholder="生成时自动拼接到用户提示词后"
                />
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-4">
              <div className="w-24 space-y-1.5">
                <label className="text-xs text-muted-foreground">排序</label>
                <Input
                  type="number"
                  value={selected.sortOrder ?? 0}
                  onChange={(e) =>
                    updateItem(selectedIndex, { sortOrder: Number.parseInt(e.target.value, 10) || 0 })
                  }
                />
              </div>
              <label className="flex items-center gap-2 pt-5 text-sm">
                <Switch
                  checked={selected.enabled !== false}
                  disabled={selectedLocked}
                  onCheckedChange={(checked) =>
                    updateItem(selectedIndex, { enabled: checked === true })
                  }
                />
                启用
              </label>
            </div>

            {onSave ? (
              <Button
                type="button"
                className="w-full"
                disabled={saving || !dirty}
                onClick={onSave}
              >
                {saving ? "保存中…" : dirty ? "保存当前工具" : "已保存"}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="rounded-xl border border-border p-4">
        <div className="mb-2 text-sm font-medium">组合预览</div>
        <Input
          className="mb-3"
          value={previewBase}
          onChange={(e) => setPreviewBase(e.target.value)}
          placeholder="用户输入的提示词"
        />
        <label className="mb-2 block text-xs text-muted-foreground">预览所用风格</label>
        <select
          className="mb-3 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          value={previewStyleId}
          onChange={(e) => setPreviewStyleId(e.target.value)}
        >
          {sortedItems
            .filter((item) => item.enabled !== false)
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
        </select>
        {errors.length ? <p className="mb-2 text-sm text-destructive">{errors.join("；")}</p> : null}
        <pre className="whitespace-pre-wrap rounded-md bg-muted/40 p-3 text-sm">
          {previewPrompt || "（空）"}
        </pre>
      </div>
    </div>
  );
}
