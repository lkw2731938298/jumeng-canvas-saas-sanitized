"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Input } from "@/components/ui/input";
import { previewAdminPromptTool } from "@/lib/api/admin";
import {
  ELEVATION_KEYS,
  HORIZONTAL_KEYS,
  LIGHT_DIRECTION_KEYS,
  SHOT_KEYS,
  mergeComposerBase,
  renderComposerPrompt,
  validateComposerConfig,
  type ComposerBaseMode,
  type ComposerPromptToolConfig,
  type LightDirection,
} from "@/lib/canvas/renderToolPrompt";
import type { ShotScale } from "@/lib/canvas/multiAnglePresets";

function LabelTable({
  title,
  rows,
  onChange,
}: {
  title: string;
  rows: { key: string; label: string }[];
  onChange: (key: string, label: string) => void;
}) {
  return (
    <div className="rounded-xl border border-border">
      <div className="border-b border-border bg-muted/40 px-4 py-2 text-sm font-medium">{title}</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] text-left text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">固定角度</th>
              <th className="px-4 py-2 font-medium">按钮显示名</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-border/60">
                <td className="px-4 py-2 font-mono text-xs">{row.key}</td>
                <td className="px-4 py-2">
                  <Input value={row.label} onChange={(e) => onChange(row.key, e.target.value)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ShotTable({
  rows,
  onChange,
}: {
  rows: { key: string; label: string; content: string }[];
  onChange: (key: string, field: "label" | "content", value: string) => void;
}) {
  return (
    <div className="rounded-xl border border-border">
      <div className="border-b border-border bg-muted/40 px-4 py-2 text-sm font-medium">景别 $s（按钮 + Prompt）</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">键</th>
              <th className="px-4 py-2 font-medium">按钮名</th>
              <th className="px-4 py-2 font-medium">Prompt 文案</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-border/60">
                <td className="px-4 py-2 font-mono text-xs">{row.key}</td>
                <td className="px-4 py-2">
                  <Input value={row.label} onChange={(e) => onChange(row.key, "label", e.target.value)} />
                </td>
                <td className="px-4 py-2">
                  <Input value={row.content} onChange={(e) => onChange(row.key, "content", e.target.value)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DirTable({
  rows,
  onChange,
}: {
  rows: { key: string; label: string; content: string }[];
  onChange: (key: string, field: "label" | "content", value: string) => void;
}) {
  return (
    <div className="rounded-xl border border-border">
      <div className="border-b border-border bg-muted/40 px-4 py-2 text-sm font-medium">主光源 $dir</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">键</th>
              <th className="px-4 py-2 font-medium">按钮名</th>
              <th className="px-4 py-2 font-medium">Prompt 文案</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} className="border-t border-border/60">
                <td className="px-4 py-2 font-mono text-xs">{row.key}</td>
                <td className="px-4 py-2">
                  <Input value={row.label} onChange={(e) => onChange(row.key, "label", e.target.value)} />
                </td>
                <td className="px-4 py-2">
                  <Input value={row.content} onChange={(e) => onChange(row.key, "content", e.target.value)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function PromptComposerEditor({
  value,
  onChange,
  toolId = "multi_angle",
}: {
  value: ComposerPromptToolConfig;
  onChange: (next: ComposerPromptToolConfig) => void;
  toolId?: string;
}) {
  const [previewCanvasBase, setPreviewCanvasBase] = useState("一位角色站在城市天台");
  const [previewExtra, setPreviewExtra] = useState("");
  const [previewAzimuth, setPreviewAzimuth] = useState("37.5");
  const [previewElevation, setPreviewElevation] = useState("-12.5");
  const [previewShot, setPreviewShot] = useState<ShotScale>("medium");
  const [previewDirection, setPreviewDirection] = useState<LightDirection>("front");
  const [previewBrightness, setPreviewBrightness] = useState("50");
  const [previewColor, setPreviewColor] = useState("");
  const [previewRim, setPreviewRim] = useState(false);
  const [previewSmart, setPreviewSmart] = useState(false);

  const isLighting = toolId === "lighting" || value.template.includes("$dir");

  const previewRuntime = useMemo(() => {
    if (isLighting) {
      return {
        base: previewCanvasBase,
        extra: previewExtra,
        direction: previewDirection,
        brightness: Number(previewBrightness),
        color: previewColor.trim() || null,
        rimLight: previewRim,
        smartMode: previewSmart,
      };
    }
    return {
      base: previewCanvasBase,
      extra: previewExtra,
      azimuth: Number(previewAzimuth),
      elevation: Number(previewElevation),
      shot: previewShot,
    };
  }, [
    isLighting,
    previewCanvasBase,
    previewExtra,
    previewDirection,
    previewBrightness,
    previewColor,
    previewRim,
    previewSmart,
    previewAzimuth,
    previewElevation,
    previewShot,
  ]);

  const { data: previewData, isFetching: previewFetching } = useQuery({
    queryKey: ["admin", "prompt-preview", toolId, value, previewRuntime],
    queryFn: () =>
      previewAdminPromptTool(toolId, previewRuntime, value as unknown as Record<string, unknown>),
  });

  const previewPrompt = previewData?.prompt ?? renderComposerPrompt(value, previewRuntime as never);

  const previewMergedBase = useMemo(
    () => mergeComposerBase(value, previewCanvasBase),
    [value, previewCanvasBase]
  );

  const previewErrors = useMemo(() => validateComposerConfig(value, toolId), [value, toolId]);

  const updateLabel = (lookupKey: "h" | "e" | "s" | "dir", rowKey: string, label: string) => {
    const labels = { ...(value.labels ?? {}) };
    labels[lookupKey] = { ...(labels[lookupKey] ?? {}), [rowKey]: label };
    onChange({ ...value, labels });
  };

  const updateShot = (rowKey: string, field: "label" | "content", fieldValue: string) => {
    const lookups = { ...(value.lookups ?? {}) };
    const labels = { ...(value.labels ?? {}) };
    if (field === "content") {
      lookups.s = { ...(lookups.s ?? {}), [rowKey]: fieldValue };
    } else {
      labels.s = { ...(labels.s ?? {}), [rowKey]: fieldValue };
    }
    onChange({ ...value, lookups, labels });
  };

  const updateDir = (rowKey: string, field: "label" | "content", fieldValue: string) => {
    const lookups = { ...(value.lookups ?? {}) };
    const labels = { ...(value.labels ?? {}) };
    if (field === "content") {
      lookups.dir = { ...(lookups.dir ?? {}), [rowKey]: fieldValue };
    } else {
      labels.dir = { ...(labels.dir ?? {}), [rowKey]: fieldValue };
    }
    onChange({ ...value, lookups, labels });
  };

  const updateToggleLookup = (lookupKey: "rim" | "smart", toggleKey: "on" | "off", fieldValue: string) => {
    const lookups = { ...(value.lookups ?? {}) };
    lookups[lookupKey] = { ...(lookups[lookupKey] ?? {}), [toggleKey]: fieldValue };
    onChange({ ...value, lookups });
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border p-4">
        <label className="mb-2 block text-sm font-medium">主模板</label>
        <textarea
          value={value.template}
          onChange={(e) => onChange({ ...value, template: e.target.value })}
          rows={3}
          className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
          placeholder="{base} → $h → $e → $s → $c → {extra?}"
        />
        <p className="mt-2 text-xs text-muted-foreground">
          {isLighting
            ? "$smart/$dir/$bright/$color/$rim/$c 见下方配置。"
            : "{base} = 后台基础描述 + 画布文本（见下方 baseMode）；$b = 仅后台基础描述；$h/$e/$s/$c 见下方配置。"}
        </p>
      </div>

      <div className="rounded-xl border border-border p-4">
        <label className="mb-2 block text-sm font-medium">基础描述 {"{base}"} / $b</label>
        <textarea
          value={value.static?.base ?? ""}
          onChange={(e) =>
            onChange({ ...value, static: { ...value.static, base: e.target.value } })
          }
          rows={2}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          placeholder="基于参考图对同一主体重新取景，除摄像机机位外人物、服装、场景与画风保持不变"
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="text-sm text-muted-foreground">与画布合并方式</span>
          {(
            [
              ["merge", "后台 + 画布（默认）"],
              ["admin", "仅后台"],
              ["canvas", "仅画布"],
            ] as const
          ).map(([mode, label]) => (
            <label key={mode} className="flex cursor-pointer items-center gap-1.5 text-sm">
              <input
                type="radio"
                name="baseMode"
                checked={(value.static?.baseMode ?? "admin") === mode}
                onChange={() =>
                  onChange({
                    ...value,
                    static: { ...value.static, baseMode: mode as ComposerBaseMode },
                  })
                }
              />
              {label}
            </label>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          画布侧来自节点描述或 @文本输入；后台在此统一配置多角度的基础语义，避免在节点 prompt 里重复粘贴。
        </p>
      </div>

      {isLighting ? (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">亮度格式 $bright</label>
            <Input
              value={value.formats?.bright ?? "光照强度{brightness}%（{brightnessDesc}）"}
              onChange={(e) =>
                onChange({ ...value, formats: { ...value.formats, bright: e.target.value } })
              }
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">颜色格式 $color</label>
            <Input
              value={value.formats?.color ?? "{colorDesc}"}
              onChange={(e) =>
                onChange({ ...value, formats: { ...value.formats, color: e.target.value } })
              }
            />
          </div>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className="text-sm font-medium">水平角度格式 $h</label>
            <Input
              value={value.formats?.h ?? "水平环绕{azimuth}度（{azimuthDesc}）"}
              onChange={(e) =>
                onChange({ ...value, formats: { ...value.formats, h: e.target.value } })
              }
              placeholder="水平环绕{azimuth}度（{azimuthDesc}）"
            />
            <p className="text-xs text-muted-foreground">
              {"{azimuth}"} = 0–359 精确角度；{"{azimuthDesc}"} = 自动生成的方位语义（正/右/背/左等）
            </p>
          </div>
          <div className="space-y-1.5">
            <label className="text-sm font-medium">俯仰角度格式 $e</label>
            <Input
              value={value.formats?.e ?? "俯仰{elevation}度（{elevationDesc}）"}
              onChange={(e) =>
                onChange({ ...value, formats: { ...value.formats, e: e.target.value } })
              }
              placeholder="俯仰{elevation}度（{elevationDesc}）"
            />
            <p className="text-xs text-muted-foreground">
              {"{elevation}"} = -90–90 精确角度；{"{elevationDesc}"} = 自动生成的俯仰语义（平视/俯拍/仰拍等）
            </p>
          </div>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1.5">
          <label className="text-sm font-medium">拼接符 $j</label>
          <Input
            value={value.static?.j ?? "，"}
            onChange={(e) => onChange({ ...value, static: { ...value.static, j: e.target.value } })}
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-sm font-medium">一致性约束 $c</label>
          <textarea
            value={value.static?.c ?? ""}
            onChange={(e) => onChange({ ...value, static: { ...value.static, c: e.target.value } })}
            rows={2}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
        </div>
      </div>

      {isLighting ? (
        <>
          <DirTable
            rows={LIGHT_DIRECTION_KEYS.map((key) => ({
              key,
              label: value.labels?.dir?.[key] ?? key,
              content: value.lookups?.dir?.[key] ?? "",
            }))}
            onChange={updateDir}
          />
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">轮廓光 $rim（开启）</label>
              <Input
                value={value.lookups?.rim?.on ?? ""}
                onChange={(e) => updateToggleLookup("rim", "on", e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">智能模式 $smart（开启）</label>
              <Input
                value={value.lookups?.smart?.on ?? ""}
                onChange={(e) => updateToggleLookup("smart", "on", e.target.value)}
              />
            </div>
          </div>
        </>
      ) : (
        <>
          <LabelTable
            title="水平环绕按钮（0–359°，固定角度，仅 UI）"
            rows={HORIZONTAL_KEYS.map((key) => ({
              key,
              label: value.labels?.h?.[key] ?? key,
            }))}
            onChange={(key, label) => updateLabel("h", key, label)}
          />
          <LabelTable
            title="俯仰按钮（-90° 仰拍 · 0° 平视 · +90° 顶视，仅 UI）"
            rows={ELEVATION_KEYS.map((key) => ({
              key,
              label: value.labels?.e?.[key] ?? key,
            }))}
            onChange={(key, label) => updateLabel("e", key, label)}
          />
          <ShotTable
            rows={SHOT_KEYS.map((key) => ({
              key,
              label: value.labels?.s?.[key] ?? key,
              content: value.lookups?.s?.[key] ?? "",
            }))}
            onChange={updateShot}
          />
        </>
      )}

      <div className="rounded-xl border border-border p-4">
        <div className="mb-3 text-sm font-medium">实时预览（可输入非档位精确角度）</div>
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          <Input
            value={previewCanvasBase}
            onChange={(e) => setPreviewCanvasBase(e.target.value)}
            placeholder="画布补充 base"
          />
          <Input value={previewExtra} onChange={(e) => setPreviewExtra(e.target.value)} placeholder="extra prompt" />
          {isLighting ? (
            <>
              <Input
                value={previewBrightness}
                onChange={(e) => setPreviewBrightness(e.target.value)}
                placeholder="brightness %"
              />
              <Input
                value={previewColor}
                onChange={(e) => setPreviewColor(e.target.value)}
                placeholder="color hex（空=无）"
              />
            </>
          ) : (
            <>
              <Input value={previewAzimuth} onChange={(e) => setPreviewAzimuth(e.target.value)} placeholder="azimuth °" />
              <Input
                value={previewElevation}
                onChange={(e) => setPreviewElevation(e.target.value)}
                placeholder="elevation °"
              />
            </>
          )}
        </div>
        {isLighting ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {LIGHT_DIRECTION_KEYS.map((dir) => (
              <button
                key={dir}
                type="button"
                onClick={() => setPreviewDirection(dir)}
                className={`rounded-md px-3 py-1 text-xs ${previewDirection === dir ? "bg-primary text-primary-foreground" : "bg-muted"}`}
              >
                {value.labels?.dir?.[dir] ?? dir}
              </button>
            ))}
            <button
              type="button"
              onClick={() => setPreviewRim((v) => !v)}
              className={`rounded-md px-3 py-1 text-xs ${previewRim ? "bg-primary text-primary-foreground" : "bg-muted"}`}
            >
              轮廓光
            </button>
            <button
              type="button"
              onClick={() => setPreviewSmart((v) => !v)}
              className={`rounded-md px-3 py-1 text-xs ${previewSmart ? "bg-primary text-primary-foreground" : "bg-muted"}`}
            >
              智能模式
            </button>
          </div>
        ) : (
          <div className="mt-3 flex flex-wrap gap-2">
            {SHOT_KEYS.map((shot) => (
              <button
                key={shot}
                type="button"
                onClick={() => setPreviewShot(shot)}
                className={`rounded-md px-3 py-1 text-xs ${previewShot === shot ? "bg-primary text-primary-foreground" : "bg-muted"}`}
              >
                {value.labels?.s?.[shot] ?? shot}
              </button>
            ))}
          </div>
        )}
        {previewErrors.length ? (
          <p className="mt-3 text-sm text-destructive">{previewErrors.join("；")}</p>
        ) : null}
        <p className="mt-3 text-xs text-muted-foreground">
          合并后 {"{base}"}：{previewMergedBase || "（空）"}
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          画布多角度面板使用与下方相同的后端合成逻辑；修改后请点击页顶「保存当前工具」再回画布验证。
        </p>
        {previewFetching ? (
          <p className="mt-3 text-sm text-muted-foreground">合成预览中…</p>
        ) : null}
        <pre className="mt-3 whitespace-pre-wrap rounded-md bg-muted/40 p-3 text-sm">{previewPrompt || "（空）"}</pre>
      </div>
    </div>
  );
}

export function defaultComposerDraft(): ComposerPromptToolConfig {
  return {
    kind: "composer",
    template: "{base} → $h → $e → $s → $c → {extra?}",
    static: {
      j: "，",
      base: "基于参考图对同一主体重新取景，除摄像机机位外人物、服装、场景与画风保持不变",
      baseMode: "admin",
      c: "严格保持参考图同一人物身份与造型，禁止换脸换衣或改变场景，仅改变观察角度与构图",
    },
    formats: { h: "水平环绕{azimuth}度（{azimuthDesc}）", e: "俯仰{elevation}度（{elevationDesc}）" },
    lookups: { s: {} },
    labels: { h: {}, e: {}, s: {} },
  };
}
