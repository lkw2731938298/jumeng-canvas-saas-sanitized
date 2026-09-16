"""Project Graph 读写。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..models.project_graph import ProjectGraph


def empty_graph(project_id: str | int) -> dict[str, Any]:
    return {
        "projectId": str(project_id),
        "revision": 1,
        "style": {
            "styleId": None,
            "labels": [],
            "aspectRatio": "9:16",
            "paletteNotes": "",
        },
        "characters": [],
        "scenes": [],
        "script": {"title": "", "beats": []},
        "shots": [],
        "timeline": {"shotIds": [], "transitions": []},
        # 单一产品电影级宣传片 Skill 专用：产品多视角定妆图身份锁（与 characters[] 并列，
        # 语义独立于「角色」，因该 Skill 走画布操控 followup、无 Team 预建条目）
        "products": [],
    }


def graph_row_to_dict(row: ProjectGraph) -> dict[str, Any]:
    data = dict(row.graph_json or {})
    data["projectId"] = str(row.project_id)
    data["revision"] = int(row.revision or 1)
    return {
        "projectId": str(row.project_id),
        "revision": int(row.revision or 1),
        "graph": data,
        "canvasOps": row.canvas_ops or [],
        "updatedAt": to_cst_iso(row.updated_at) if row.updated_at else None,
    }


async def get_or_create_graph(db: AsyncSession, project_id: int) -> ProjectGraph:
    row = (
        await db.execute(
            select(ProjectGraph).where(ProjectGraph.project_id == int(project_id)).limit(1)
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = ProjectGraph(
        project_id=int(project_id),
        revision=1,
        graph_json=empty_graph(project_id),
        canvas_ops=[],
    )
    db.add(row)
    await db.flush()
    return row


async def get_graph_dict(db: AsyncSession, project_id: int) -> dict[str, Any]:
    row = await get_or_create_graph(db, project_id)
    return graph_row_to_dict(row)


async def save_graph(
    db: AsyncSession,
    project_id: int,
    graph: dict[str, Any],
    *,
    canvas_ops: list[dict[str, Any]] | None = None,
    bump_revision: bool = True,
) -> dict[str, Any]:
    """覆盖写入 Graph；可选附带 canvasOps 供前端投影。"""
    row = await get_or_create_graph(db, project_id)
    next_rev = int(row.revision or 1) + (1 if bump_revision else 0)
    payload = dict(graph or {})
    payload["projectId"] = str(project_id)
    payload["revision"] = next_rev
    row.graph_json = payload
    row.revision = next_rev
    if canvas_ops is not None:
        row.canvas_ops = canvas_ops
    row.updated_at = now_cst_naive()
    await db.flush()
    return graph_row_to_dict(row)


async def clear_canvas_ops(db: AsyncSession, project_id: int) -> None:
    row = (
        await db.execute(
            select(ProjectGraph).where(ProjectGraph.project_id == int(project_id)).limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return
    row.canvas_ops = []
    row.updated_at = now_cst_naive()
    await db.flush()


_SHOT_STATUS_VALUES = {"planned", "generating", "ready", "failed"}


async def bind_shot_canvas_nodes(
    db: AsyncSession,
    project_id: int,
    bindings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """前端投影后回写 shotId → canvasNodeId，供续聊引用。"""
    if not bindings:
        return None
    row = await get_or_create_graph(db, project_id)
    graph = dict(row.graph_json or {})
    shots = list(graph.get("shots") or []) if isinstance(graph.get("shots"), list) else []
    by_id = {
        str(s.get("id")): s
        for s in shots
        if isinstance(s, dict) and s.get("id")
    }
    changed = 0
    for b in bindings:
        if not isinstance(b, dict):
            continue
        shot_id = str(b.get("shotId") or "").strip()
        node_id = str(b.get("canvasNodeId") or "").strip()
        if not shot_id or not node_id:
            continue
        shot = by_id.get(shot_id)
        if shot is None:
            continue
        if shot.get("canvasNodeId") != node_id:
            shot["canvasNodeId"] = node_id
            changed += 1
    if changed:
        graph["shots"] = shots
        row.graph_json = graph
        row.updated_at = now_cst_naive()
        await db.flush()
    return graph_row_to_dict(row)


async def sync_shot_progress(
    db: AsyncSession,
    project_id: int,
    updates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    单镜（image_input / video_input）生成成功后，把最新 outputAssetId / status
    回写到 Project Graph 的 shots[]——这是自由创作 Agent Team 路径「改单镜后
    自动 reflow 整条时间线」的数据落点：timeline.shotIds 排序在建图时已算好
    （agent_team_orchestrator.py），这里只补「生成完成后没人回写状态」的缺口。

    只信任 shotId 命中的行；不接受调用方直接改 canvasNodeId（那由
    bind_shot_canvas_nodes 在画布投影时写一次，属于结构绑定，不随生成变化）。
    """
    if not updates:
        return None
    row = await get_or_create_graph(db, project_id)
    graph = dict(row.graph_json or {})
    shots = list(graph.get("shots") or []) if isinstance(graph.get("shots"), list) else []
    by_id = {
        str(s.get("id")): s
        for s in shots
        if isinstance(s, dict) and s.get("id")
    }
    changed = 0
    for u in updates:
        if not isinstance(u, dict):
            continue
        shot_id = str(u.get("shotId") or "").strip()
        if not shot_id:
            continue
        shot = by_id.get(shot_id)
        if shot is None:
            continue
        output_asset_id = u.get("outputAssetId")
        if output_asset_id is not None:
            output_asset_id = str(output_asset_id).strip() or None
            if shot.get("outputAssetId") != output_asset_id:
                shot["outputAssetId"] = output_asset_id
                changed += 1
        status = str(u.get("status") or "").strip()
        if status and status in _SHOT_STATUS_VALUES and shot.get("status") != status:
            shot["status"] = status
            changed += 1
    if changed:
        graph["shots"] = shots
        row.graph_json = graph
        row.updated_at = now_cst_naive()
        await db.flush()
        # 全部镜头 ready 且剪辑台草稿为空时，静默按 Graph 组装（不覆盖手工稿、不强制打开）
        await _maybe_auto_assemble_editor_draft(db, project_id, shots)
    return graph_row_to_dict(row)


async def _maybe_auto_assemble_editor_draft(
    db: AsyncSession,
    project_id: int,
    shots: list[Any],
) -> None:
    """镜头全部成片后自动写入剪辑台草稿（仅草稿为空时）。"""
    ready_shots = [
        s
        for s in shots
        if isinstance(s, dict) and str(s.get("id") or "").strip()
    ]
    if not ready_shots:
        return
    if not all(
        str(s.get("status") or "") == "ready" and str(s.get("outputAssetId") or "").strip()
        for s in ready_shots
    ):
        return
    try:
        from .video_editor_assemble import assemble_timeline_from_graph

        await assemble_timeline_from_graph(db, project_id, replace=False)
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception(
            "auto assemble editor draft failed project=%s", project_id
        )


_CHARACTER_SHEET_ASSET_CAP = 4


async def bind_character_canvas_nodes(
    db: AsyncSession,
    project_id: int,
    bindings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """前端投影后回写 characterId → canvasNodeId（角色定妆图节点），结构对齐
    bind_shot_canvas_nodes，供续聊引用与 identityLock 回写定位节点。"""
    if not bindings:
        return None
    row = await get_or_create_graph(db, project_id)
    graph = dict(row.graph_json or {})
    characters = (
        list(graph.get("characters") or []) if isinstance(graph.get("characters"), list) else []
    )
    by_id = {
        str(c.get("id")): c
        for c in characters
        if isinstance(c, dict) and c.get("id")
    }
    changed = 0
    for b in bindings:
        if not isinstance(b, dict):
            continue
        character_id = str(b.get("characterId") or "").strip()
        node_id = str(b.get("canvasNodeId") or "").strip()
        if not character_id or not node_id:
            continue
        character = by_id.get(character_id)
        if character is None:
            continue
        if character.get("canvasNodeId") != node_id:
            character["canvasNodeId"] = node_id
            changed += 1
    if changed:
        graph["characters"] = characters
        row.graph_json = graph
        row.updated_at = now_cst_naive()
        await db.flush()
    return graph_row_to_dict(row)


async def sync_character_sheet_asset(
    db: AsyncSession,
    project_id: int,
    updates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    角色「定妆图」节点（characterAssetRole=sheet）生成成功后，把产物 assetId
    去重追加进对应角色 identityLock.sheetAssetIds（上限 4）——这是 identityLock
    三视图闭环的回写点：镜头节点已通过 connect_nodes 连到该定妆图节点上游，一旦
    定妆图生成完成，后续镜头手动生成时会经既有「上游连线媒体自动作为参考」通道
    自动带上该形象参考，无需改生成提交逻辑。

    只信任 characterId 命中的行；不接受调用方直接改 canvasNodeId（结构绑定见
    bind_character_canvas_nodes，不随生成变化）。
    """
    if not updates:
        return None
    row = await get_or_create_graph(db, project_id)
    graph = dict(row.graph_json or {})
    characters = (
        list(graph.get("characters") or []) if isinstance(graph.get("characters"), list) else []
    )
    by_id = {
        str(c.get("id")): c
        for c in characters
        if isinstance(c, dict) and c.get("id")
    }
    changed = 0
    for u in updates:
        if not isinstance(u, dict):
            continue
        character_id = str(u.get("characterId") or "").strip()
        if not character_id:
            continue
        character = by_id.get(character_id)
        if character is None:
            continue
        sheet_asset_id = str(u.get("sheetAssetId") or "").strip()
        if not sheet_asset_id:
            continue
        lock = character.get("identityLock") if isinstance(character.get("identityLock"), dict) else {}
        sheet_ids = [str(x).strip() for x in (lock.get("sheetAssetIds") or []) if str(x).strip()]
        if sheet_asset_id not in sheet_ids:
            sheet_ids.append(sheet_asset_id)
            lock["sheetAssetIds"] = sheet_ids[-_CHARACTER_SHEET_ASSET_CAP:]
            character["identityLock"] = lock
            changed += 1
    if changed:
        graph["characters"] = characters
        row.graph_json = graph
        row.updated_at = now_cst_naive()
        await db.flush()
    return graph_row_to_dict(row)


_PRODUCT_SHEET_ASSET_CAP = 4


async def bind_product_canvas_nodes(
    db: AsyncSession,
    project_id: int,
    bindings: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """单一产品电影级宣传片 Skill：产品「多视角定妆图」节点 → productId 绑定。

    与 bind_character_canvas_nodes 的关键差异：该 Skill 走画布操控 followup，
    不经 Team 的 _build_canvas_ops 预建 products[] 条目，所以命中不到时要
    自动创建一条新记录，而不是像角色那样直接跳过。
    """
    if not bindings:
        return None
    row = await get_or_create_graph(db, project_id)
    graph = dict(row.graph_json or {})
    products = list(graph.get("products") or []) if isinstance(graph.get("products"), list) else []
    by_id = {
        str(p.get("id")): p
        for p in products
        if isinstance(p, dict) and p.get("id")
    }
    changed = 0
    for b in bindings:
        if not isinstance(b, dict):
            continue
        product_id = str(b.get("productId") or "").strip()
        node_id = str(b.get("canvasNodeId") or "").strip()
        if not product_id or not node_id:
            continue
        product = by_id.get(product_id)
        if product is None:
            product = {"id": product_id, "canvasNodeId": node_id, "identityLock": {"sheetAssetIds": []}}
            products.append(product)
            by_id[product_id] = product
            changed += 1
            continue
        if product.get("canvasNodeId") != node_id:
            product["canvasNodeId"] = node_id
            changed += 1
    if changed:
        graph["products"] = products
        row.graph_json = graph
        row.updated_at = now_cst_naive()
        await db.flush()
    return graph_row_to_dict(row)


async def sync_product_sheet_asset(
    db: AsyncSession,
    project_id: int,
    updates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    产品「多视角定妆图」节点（productAssetRole=sheet）生成成功后，把产物 assetId
    去重追加进对应产品 identityLock.sheetAssetIds（上限 4）——对齐角色
    identityLock 闭环：镜头节点已通过 connect_nodes 连到该定妆图节点上游
    （前端确定性自动连线，非依赖 LLM 记忆），生成完成后续镜头手动生成时
    自动带上该产品形象参考，无需改生成提交逻辑。

    只信任 productId 命中的行；不接受调用方直接改 canvasNodeId（结构绑定见
    bind_product_canvas_nodes，不随生成变化）。
    """
    if not updates:
        return None
    row = await get_or_create_graph(db, project_id)
    graph = dict(row.graph_json or {})
    products = list(graph.get("products") or []) if isinstance(graph.get("products"), list) else []
    by_id = {
        str(p.get("id")): p
        for p in products
        if isinstance(p, dict) and p.get("id")
    }
    changed = 0
    for u in updates:
        if not isinstance(u, dict):
            continue
        product_id = str(u.get("productId") or "").strip()
        if not product_id:
            continue
        product = by_id.get(product_id)
        if product is None:
            continue
        sheet_asset_id = str(u.get("sheetAssetId") or "").strip()
        if not sheet_asset_id:
            continue
        lock = product.get("identityLock") if isinstance(product.get("identityLock"), dict) else {}
        sheet_ids = [str(x).strip() for x in (lock.get("sheetAssetIds") or []) if str(x).strip()]
        if sheet_asset_id not in sheet_ids:
            sheet_ids.append(sheet_asset_id)
            lock["sheetAssetIds"] = sheet_ids[-_PRODUCT_SHEET_ASSET_CAP:]
            product["identityLock"] = lock
            changed += 1
    if changed:
        graph["products"] = products
        row.graph_json = graph
        row.updated_at = now_cst_naive()
        await db.flush()
    return graph_row_to_dict(row)
