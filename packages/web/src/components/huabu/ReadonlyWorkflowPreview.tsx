"use client";

import { memo, useMemo } from "react";
import {
  Background,
  BackgroundVariant,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { PublicationPreview, PublicationPreviewAsset } from "@/lib/api/workflowPublications";

const UUID_LIKE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function PreviewNode({ data }: NodeProps) {
  const d = data as {
    label: string;
    mediaUrl?: string;
    category?: string;
  };
  return (
    <div className="wf-preview-node">
      {/* 预览节点提供默认左右锚点，边不再依赖源画布具体 handle id */}
      <Handle
        type="target"
        position={Position.Left}
        className="wf-preview-handle"
        isConnectable={false}
      />
      <div className="wf-preview-node-media">
        {d.mediaUrl ? (
          d.category === "video" ? (
            <video src={d.mediaUrl} muted playsInline preload="metadata" />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={d.mediaUrl} alt="" />
          )
        ) : (
          <span className="wf-preview-node-empty">{d.label}</span>
        )}
      </div>
      <div className="wf-preview-node-label">{d.label}</div>
      <Handle
        type="source"
        position={Position.Right}
        className="wf-preview-handle"
        isConnectable={false}
      />
    </div>
  );
}

const nodeTypes = { preview: memo(PreviewNode) };

function buildAssetLookup(assets: PublicationPreviewAsset[]) {
  const map = new Map<string, PublicationPreviewAsset>();
  for (const a of assets) {
    map.set(a.id, a);
    if (a.legacyId) map.set(a.legacyId, a);
  }
  return map;
}

function resolveMedia(
  params: Record<string, unknown> | undefined,
  lookup: Map<string, PublicationPreviewAsset>
): { url?: string; category?: string } {
  if (!params) return {};
  const assetId = String(params.assetId ?? params.asset_id ?? "").trim();
  if (assetId) {
    const hit = lookup.get(assetId);
    if (hit?.fileUrl) return { url: hit.fileUrl, category: hit.category };
  }
  for (const key of ["imageUrl", "videoUrl", "fileUrl", "thumbnailUrl"]) {
    const u = String(params[key] ?? "").trim();
    if (u.startsWith("http") || u.startsWith("/")) {
      return { url: u, category: key.includes("video") ? "video" : "image" };
    }
  }
  return {};
}

function resolveLabel(n: Record<string, unknown>, index: number): string {
  const data = (n.data && typeof n.data === "object" ? n.data : {}) as Record<string, unknown>;
  const candidates = [data.label, data.title, n.type, `节点${index + 1}`];
  for (const raw of candidates) {
    const s = String(raw ?? "").trim();
    if (!s || UUID_LIKE.test(s)) continue;
    return s.slice(0, 32);
  }
  return `节点${index + 1}`;
}

interface ReadonlyWorkflowPreviewProps {
  preview: PublicationPreview;
}

/** 只读画布预览：展示节点布局、连线与素材，不可编辑 */
export function ReadonlyWorkflowPreview({ preview }: ReadonlyWorkflowPreviewProps) {
  const lookup = useMemo(() => buildAssetLookup(preview.assets || []), [preview.assets]);

  const { nodes, edges } = useMemo(() => {
    const rawNodes = Array.isArray(preview.flowJson?.nodes) ? preview.flowJson.nodes : [];
    const rawEdges = Array.isArray(preview.flowJson?.edges) ? preview.flowJson.edges : [];
    const nodeIdSet = new Set(
      rawNodes.map((n, index) => String(n.id ?? `n-${index}`)).filter(Boolean)
    );

    const nodesOut: Node[] = rawNodes.map((n, index) => {
      const id = String(n.id ?? `n-${index}`);
      const position =
        n.position && typeof n.position === "object"
          ? {
              x: Number((n.position as { x?: number }).x ?? index * 40),
              y: Number((n.position as { y?: number }).y ?? index * 40),
            }
          : { x: index * 220, y: Math.floor(index / 4) * 180 };
      const data = (n.data && typeof n.data === "object" ? n.data : {}) as Record<string, unknown>;
      const params =
        data.params && typeof data.params === "object"
          ? (data.params as Record<string, unknown>)
          : {};
      const media = resolveMedia(params, lookup);
      return {
        id,
        type: "preview",
        position,
        data: {
          label: resolveLabel(n, index),
          mediaUrl: media.url,
          category: media.category,
        },
        draggable: false,
        selectable: false,
      };
    });

    // 去掉源画布自定义 handle id：预览节点只有默认左右锚点，保留 handle 会导致边不渲染
    const edgesOut: Edge[] = rawEdges
      .map((e, index) => {
        const id = String(e.id ?? `e-${index}`);
        const source = String(e.source ?? "").trim();
        const target = String(e.target ?? "").trim();
        if (!source || !target) return null;
        if (!nodeIdSet.has(source) || !nodeIdSet.has(target)) return null;
        return {
          id,
          source,
          target,
          type: "default",
          style: { stroke: "#9b8fb8", strokeWidth: 2 },
        } as Edge;
      })
      .filter((e): e is Edge => e != null);

    return { nodes: nodesOut, edges: edgesOut };
  }, [preview.flowJson, lookup]);

  if (!nodes.length) {
    return <div className="wf-preview-empty">该工作流暂无节点</div>;
  }

  return (
    <div className="wf-preview-canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        edgesFocusable={false}
        panOnDrag
        zoomOnScroll
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.15}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{
          style: { stroke: "#9b8fb8", strokeWidth: 2 },
        }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#3a3444" />
      </ReactFlow>
    </div>
  );
}
