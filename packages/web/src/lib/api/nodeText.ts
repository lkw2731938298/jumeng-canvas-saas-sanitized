import { withBasePath } from "@/lib/basePath";

export interface NodeTextRecord {
  projectId: string;
  nodeId: string;
  content: string;
  model: string;
  ossKey: string;
  fileUrl: string;
  updatedAt: string;
}

function authHeaders(): Record<string, string> {
  const token =
    typeof window !== "undefined" ? localStorage.getItem("jm_canvas_session_token") : null;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function fetchNodeText(projectId: string, nodeId: string): Promise<NodeTextRecord | null> {
  const url = new URL(withBasePath("/api/node-text"), window.location.origin);
  url.searchParams.set("projectId", projectId);
  url.searchParams.set("nodeId", nodeId);
  const res = await fetch(url.toString(), {
    headers: authHeaders(),
    credentials: "include",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("加载文本失败");
  return res.json();
}

export async function saveNodeText(
  projectId: string,
  nodeId: string,
  content: string,
  model: string
): Promise<NodeTextRecord> {
  const res = await fetch(withBasePath("/api/node-text"), {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    credentials: "include",
    body: JSON.stringify({ projectId, nodeId, content, model }),
  });
  if (!res.ok) throw new Error("保存文本失败");
  return res.json();
}
