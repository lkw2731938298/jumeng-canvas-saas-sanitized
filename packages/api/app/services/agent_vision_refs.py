"""Agent 附件 → 视觉多模态 URL。

续聊 / 澄清 / Team 扩写共用：从消息 `[附件:…|assetId=…]`、brief.referenceAssetIds、
以及画布快照中的图片 assetId（故事板结果优先）解析图片，签发 HTTPS 地址供豆包等
视觉模型读取画面（不仅是文字 id）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.llm.chat import (
    build_vision_user_content,
    supports_vision,
)
from ..services.agent_controller import pick_controller_model

logger = logging.getLogger(__name__)

# 与 agent_sessions 一致：MySQL 自增 id 可能很短
_ASSET_ID_RE = re.compile(r"assetId=([0-9a-zA-Z_-]{1,128})", re.IGNORECASE)

_IMAGE_EXT_RE = re.compile(
    r"\.(jpe?g|png|gif|webp|bmp|svg)(?:\?|$)", re.IGNORECASE
)


def extract_asset_ids_from_text(text: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for m in _ASSET_ID_RE.finditer(text or ""):
        aid = (m.group(1) or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        ids.append(aid)
    return ids


_VIDEO_EXT_RE = re.compile(
    r"\.(mp4|mov|webm|mkv|avi|m4v)(?:\?|$)", re.IGNORECASE
)


def _looks_like_image(row: dict[str, Any]) -> bool:
    cat = str(row.get("category") or "").lower()
    if cat == "image":
        return True
    if cat in ("video", "audio"):
        return False
    ft = str(row.get("fileType") or row.get("file_type") or "").lower()
    if ft.startswith("image/"):
        return True
    if ft.startswith("video/") or ft.startswith("audio/"):
        return False
    name = str(row.get("title") or row.get("fileUrl") or row.get("file_url") or "")
    return bool(_IMAGE_EXT_RE.search(name))


def _looks_like_video(row: dict[str, Any]) -> bool:
    cat = str(row.get("category") or "").lower()
    if cat == "video":
        return True
    if cat in ("image", "audio"):
        return False
    ft = str(row.get("fileType") or row.get("file_type") or "").lower()
    if ft.startswith("video/"):
        return True
    name = str(row.get("title") or row.get("fileUrl") or row.get("file_url") or "")
    return bool(_VIDEO_EXT_RE.search(name))


def extract_video_asset_ids_from_canvas_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    max_ids: int = 4,
) -> list[str]:
    """快照里视频节点的 assetId；准星引用（focused）优先。"""
    if not isinstance(snapshot, dict):
        return []
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    focused_ids: list[str] = []
    other_ids: list[str] = []
    seen: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        ntype = str(n.get("type") or "")
        if ntype and ntype not in ("video_input", "video"):
            continue
        aid = str(n.get("assetId") or "").strip()
        if not aid:
            params = n.get("params") if isinstance(n.get("params"), dict) else {}
            aid = str(params.get("assetId") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        if n.get("focused"):
            focused_ids.append(aid)
        else:
            other_ids.append(aid)
    return [*focused_ids, *other_ids][: max(1, max_ids)]


def extract_asset_ids_from_canvas_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    prefer_storyboard: bool = True,
    max_ids: int = 8,
) -> list[str]:
    """从画布快照节点提取图片 assetId。

    故事板/调度故事板结果节点（label 含关键词）优先，便于续聊视觉读板分镜。
    """
    if not isinstance(snapshot, dict):
        return []
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    focused_ids: list[str] = []
    board_ids: list[str] = []
    other_ids: list[str] = []
    seen: set[str] = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        ntype = str(n.get("type") or "")
        # 仅取图片节点；精简快照可能省略 type，有 assetId 仍收入
        if ntype and ntype not in ("image_input", "image"):
            continue
        aid = str(n.get("assetId") or "").strip()
        if not aid:
            params = n.get("params") if isinstance(n.get("params"), dict) else {}
            aid = str(params.get("assetId") or "").strip()
        if not aid or aid in seen:
            continue
        seen.add(aid)
        label = str(n.get("label") or "")
        role = str(n.get("role") or "").strip()
        is_board = role == "storyboard_sheet" or any(
            k in label
            for k in ("故事板", "调度故事板", "storyboard", "blocking")
        )
        # 准星引用 / 选中的焦点图优先，再故事板，再其它
        if n.get("focused"):
            focused_ids.append(aid)
        elif prefer_storyboard and is_board:
            board_ids.append(aid)
        else:
            other_ids.append(aid)
    ordered = [*focused_ids, *board_ids, *other_ids]
    return ordered[: max(1, max_ids)]


async def _collect_vision_urls(
    db: AsyncSession,
    project_id: int | str,
    ids: list[str],
    *,
    kind: str,
    max_urls: int,
) -> list[str]:
    """按 assetId 列表签发可公网拉取的 HTTPS 媒体 URL。"""
    from .asset_store import get_project_assets_by_ids

    if not ids:
        return []
    look = _looks_like_image if kind == "image" else _looks_like_video
    try:
        rows = await get_project_assets_by_ids(db, str(project_id), ids[:20])
    except Exception as exc:  # noqa: BLE001
        logger.warning("collect_vision_%s_urls: load assets failed: %s", kind, exc)
        return []

    by_id: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "").strip()
        if rid:
            by_id[rid] = r
        leg = str(r.get("legacyId") or "").strip()
        if leg:
            by_id[leg] = r

    urls: list[str] = []
    for aid in ids:
        row = by_id.get(aid)
        if not row or not look(row):
            continue
        url = str(row.get("fileUrl") or row.get("thumbnailUrl") or "").strip()
        if not url:
            continue
        if url.startswith("http://") or url.startswith("https://"):
            if url.startswith("http://"):
                url = "https://" + url[len("http://") :]
            urls.append(url)
        if len(urls) >= max_urls:
            break
    return urls


def _merge_asset_ids(*groups: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for aid in group:
            s = str(aid or "").strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
    return out


async def collect_vision_image_urls(
    db: AsyncSession,
    project_id: int | str,
    *,
    message_text: str = "",
    reference_asset_ids: list[str] | None = None,
    canvas_snapshot: dict[str, Any] | None = None,
    max_images: int = 4,
) -> list[str]:
    """合并消息附件、会话参考图与画布快照图片，返回可供上游拉取的 HTTPS 图片 URL。"""
    ids = _merge_asset_ids(
        extract_asset_ids_from_text(message_text),
        extract_asset_ids_from_canvas_snapshot(canvas_snapshot),
        [str(x).strip() for x in (reference_asset_ids or []) if str(x).strip()],
    )
    return await _collect_vision_urls(
        db, project_id, ids, kind="image", max_urls=max(1, max_images)
    )


async def collect_vision_video_urls(
    db: AsyncSession,
    project_id: int | str,
    *,
    message_text: str = "",
    reference_asset_ids: list[str] | None = None,
    canvas_snapshot: dict[str, Any] | None = None,
    max_videos: int = 3,
) -> list[str]:
    """消息附件与准星引用的视频 → HTTPS URL（模型支持时送 video_url）。"""
    ids = _merge_asset_ids(
        extract_asset_ids_from_text(message_text),
        extract_video_asset_ids_from_canvas_snapshot(canvas_snapshot),
        [str(x).strip() for x in (reference_asset_ids or []) if str(x).strip()],
    )
    return await _collect_vision_urls(
        db, project_id, ids, kind="video", max_urls=max(1, max_videos)
    )


def pick_vision_controller_model(
    preferred: str | None,
    *,
    has_images: bool,
) -> str | None:
    """有附图/附视频时优先选支持视觉的控制器；否则走原推荐序。"""
    model = pick_controller_model(preferred)
    if not has_images:
        return model
    if model and supports_vision(model):
        return model
    # 附图但当前模型不支持视觉 → 尽量切到 Seed Evolving（豆包多模态）
    fb = pick_controller_model("doubao_seed_evolving")
    if fb and supports_vision(fb):
        return fb
    return model


def build_controller_user_content(
    text: str,
    *,
    model_id: str | None,
    image_urls: list[str] | None,
    video_urls: list[str] | None = None,
) -> str | list[dict[str, Any]]:
    """有附图/附视频且模型支持视觉时返回多模态 content，否则纯文本。"""
    imgs = [u for u in (image_urls or []) if (u or "").strip()]
    vids = [u for u in (video_urls or []) if (u or "").strip()]
    if (not imgs and not vids) or not model_id or not supports_vision(model_id):
        return text
    t = text or ""
    if any(k in t for k in ("故事板", "调度故事板", "读板", "分镜格")):
        hint = (
            "\n\n【附图】下方含故事板/调度故事板合成图（及可能的产品参考图）。"
            "请仔细用视觉阅读故事板：①数清分镜格数与阅读顺序（通常左→右、上→下）；"
            "②按格分配合理视频镜头（可 1 格 1 镜，或合并相邻格）；"
            "③为每镜写可执行的 params.prompt（运镜/时间推进，少改产品外形）；"
            "④把故事板结果图与产品参考图连到各 video 的 ref_in。"
            "消息/快照里的 assetId 须落到节点 params.assetId，不要编造 id。"
        )
    else:
        hint = (
            "\n\n【附图/附视频】下方是用户本轮上传或准星引用的媒体。"
            "请看清画面主体、构图、产品外形；视频则把握节奏与镜头。"
            "必须用这些参考落到画布（params.assetId），不要编造 id，不要视而不见。"
        )
    return build_vision_user_content(t + hint, image_urls=imgs, video_urls=vids)
