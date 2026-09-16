"""Scan OSS and recover workflow pointers to the richest nodes.json per project."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..integrations.oss.canvas_storage import get_canvas_storage
from ..integrations.oss.service import get_oss
from ..services.asset_index import storage_folder_to_uuid

_WORKFLOW_NODES_RE = re.compile(
    r"/projects/[^/]+/workflows/([^/]+)/nodes\.json$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WorkflowOssCandidate:
    oss_key: str
    node_count: int
    edge_count: int
    byte_size: int


def _workflow_prefixes(project_id: str, storage_folder: str) -> list[str]:
    storage = get_canvas_storage()
    folders = [storage_folder]
    dashed = storage_folder_to_uuid(storage_folder)
    if dashed != storage_folder:
        folders.append(dashed)
    prefixes: list[str] = []
    for folder in folders:
        base = storage.project_key(project_id, "workflows/", storage_folder=folder)
        if not base.endswith("/"):
            base = f"{base}/"
        prefixes.append(base)
        if base.startswith("Ihuabu/"):
            prefixes.append(base.replace("Ihuabu/", "canvas/", 1))
        elif base.startswith("canvas/"):
            prefixes.append(base.replace("canvas/", "Ihuabu/", 1))
    return list(dict.fromkeys(prefixes))


def _count_nodes_edges(raw: bytes) -> tuple[int, int]:
    if not raw:
        return 0, 0
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0, 0
    if not isinstance(data, dict):
        return 0, 0
    nodes = data.get("nodes")
    edges = data.get("edges")
    node_count = len(nodes) if isinstance(nodes, list) else 0
    edge_count = len(edges) if isinstance(edges, list) else 0
    return node_count, edge_count


def scan_workflow_candidates(project_id: str, storage_folder: str) -> list[WorkflowOssCandidate]:
    """List workflow nodes.json objects under a project OSS folder."""
    oss = get_oss()
    storage = get_canvas_storage()
    if not oss.bucket:
        return []

    seen: set[str] = set()
    candidates: list[WorkflowOssCandidate] = []
    for prefix in _workflow_prefixes(project_id, storage_folder):
        for key in oss.list_object_keys(prefix):
            if not key.endswith("/nodes.json"):
                continue
            if not _WORKFLOW_NODES_RE.search(key):
                continue
            resolved = storage.resolve_existing_key(key) or key
            if resolved in seen:
                continue
            seen.add(resolved)
            raw = storage.get_bytes(resolved)
            node_count, edge_count = _count_nodes_edges(raw)
            candidates.append(
                WorkflowOssCandidate(
                    oss_key=resolved,
                    node_count=node_count,
                    edge_count=edge_count,
                    byte_size=len(raw),
                )
            )
    return candidates


def pick_best_workflow_candidate(candidates: list[WorkflowOssCandidate]) -> WorkflowOssCandidate | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item.node_count, item.edge_count, item.byte_size),
    )


def make_flow_pointer(oss_key: str) -> str:
    return json.dumps({"_storage": "oss", "ossKey": oss_key}, ensure_ascii=False)


def project_folder_from_workflow_key(oss_key: str) -> str | None:
    match = re.search(r"/projects/([^/]+)/workflows/", str(oss_key or ""), re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower().replace("-", "")


def resolve_workflow_flow_json(
    flow_json: str,
    *,
    project_id: str,
    storage_folder: str,
    storage=None,
) -> tuple[str, str | None]:
    """Return (flow_json_content, better_oss_key_if_recovered)."""
    from ..integrations.oss.canvas_storage import get_canvas_storage, resolve_flow_json

    store = storage or get_canvas_storage()
    current = resolve_flow_json(flow_json, store)
    current_nodes, _ = _count_nodes_edges(current.encode("utf-8") if current else b"")

    candidates = scan_workflow_candidates(project_id, storage_folder)
    best = pick_best_workflow_candidate(candidates)
    if not best or best.node_count <= 0:
        return current, None

    try:
        ptr = json.loads(flow_json)
        current_key = ptr.get("ossKey") if isinstance(ptr, dict) else None
    except json.JSONDecodeError:
        current_key = None

    if (
        current_nodes >= best.node_count
        and current_key
        and str(current_key) == best.oss_key
    ):
        return current, None

    if current_nodes >= best.node_count and current_nodes > 0:
        return current, None

    recovered = store.get_bytes(best.oss_key).decode("utf-8")
    if current_key == best.oss_key:
        return recovered, None
    return recovered, best.oss_key
