"""画布/分镜各功能的模型切换（主/副模型）配置。

权威存 MySQL ``platform_settings.canvas_tool_models``；
后台可为「多角度/打光/全景/九宫格子功能/扩图/抠图/高清/人像调节/情绪调节/视频高清/人声分离/消除人声」及「解析剧本/主体提取/运镜词/视频词/草图/主体生图」
分别配置主模型与副模型（副模型作 failover 兜底）。
九宫格弹层内 25宫格/四宫格/推演/设定图等已拆开独立配置；未单独落库时继承原 ``grid_9``。
抠图为本地处理，主模型名用于算力结算（金额跟主模型 pricing），不调用上游生图。
智能抠像为本地 rembg（主模型名结算；金额跟主模型 pricing）；主体消除 / 修改 / 替换走视频上游。
人像调节 / 情绪调节走图片上游（图生图），后台可切换图片模型；算力跟主模型档位价。
人声分离 / 消除人声走 RunningHub 分离音频（后台可切换）。
智能去字幕（智能擦除 / 框选擦除）走聚梦网关火山字幕擦除精准版（后台可切换；RunningHub 精细化版作副模型）。

- 主模型：该功能优先使用的模型（后台配置为权威，覆盖前端传入）。
- 副模型：主模型上游失败时自动切换的兜底模型（图片走 media 通道 failover，文本走 LLM failover）。
- 未配置时回退各功能的历史默认模型，保持既有行为不变。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..models.job import Model
from .canvas_tool_pricing import (
    CREATIVE_GRID_CHILD_TOOL_IDS,
    canvas_tool_group,
    canvas_tool_label,
)
from .platform_settings import (
    get_canvas_tool_models_raw,
    set_canvas_tool_models_raw,
)

# (tool_id, 模型目录类别 image|text, 默认主模型)
# 说明：仅纳入「会调用模型」的功能；grid_split（宫格切分）为本地工具、不调模型，故不纳管。
CANVAS_TOOL_MODEL_DEFS: tuple[tuple[str, str, str], ...] = (
    ("multi_angle", "image", ""),
    ("lighting", "image", ""),
    ("panorama", "image", ""),
    # 九宫格顶栏兼容旧路径；弹层内各功能独立主/副模型
    ("grid_9", "image", ""),
    ("grid_25", "image", ""),
    ("plot_grid_4", "image", ""),
    ("frame_forward_3s", "image", ""),
    ("frame_back_5s", "image", ""),
    ("cinematic_lighting", "image", ""),
    ("multi_cam_grid_9", "image", ""),
    ("face_tri_view", "image", ""),
    ("character_sheet", "image", ""),
    ("character_tri_view", "image", ""),
    ("scene_sheet", "image", ""),
    ("product_sheet", "image", ""),
    ("outpaint", "image", ""),
    ("cutout", "image", ""),
    ("hd_upscale", "image", ""),
    # 人像质感调节：图生图上游；后台可切换图片模型 + 固定算力
    ("portrait_adjust", "image", ""),
    ("emotion_adjust", "image", ""),
    ("hd_upscale_video", "video", ""),
    ("drawing_board_ai", "image", ""),
    # 故事板 / 调度故事板：有参考图走图生图；无图时提交层会改走对应文生图
    ("storyboard", "image", ""),
    ("blocking_storyboard", "image", ""),
    # 画面编辑：智能抠像本地；主体消除/修改/替换走视频上游（后台可切换视频模型）
    ("video_smart_matting", "video", ""),
    ("video_subject_remove", "video", ""),
    ("video_subject_edit", "video", ""),
    ("video_subject_replace", "video", ""),
    # 智能去字幕：聚梦网关火山字幕擦除（精准版）；精细化版作后台副模型
    ("video_subtitle_smart_erase", "video", ""),
    ("video_subtitle_box_erase", "video", ""),
    # 人声分离 / 消除人声：RunningHub 分离音频 Vocals / Other
    ("vocal_separate", "audio", ""),
    ("vocal_remove", "audio", ""),
    ("storyboard_table", "text", ""),
    ("storyboard_from_video", "text", ""),
    ("storyboard_from_image", "text", ""),
    ("storyboard_overseas_localize", "text", ""),
    ("text_subject", "text", ""),
    ("storyboard_camera", "text", ""),
    ("storyboard_video", "text", ""),
    ("storyboard_sketch", "image", ""),
    ("storyboard_subject_image", "image", ""),
)

CANVAS_TOOL_MODEL_IDS = frozenset(tid for tid, _, _ in CANVAS_TOOL_MODEL_DEFS)
_TOOL_CATEGORY = {tid: cat for tid, cat, _ in CANVAS_TOOL_MODEL_DEFS}
_TOOL_DEFAULT_PRIMARY = {tid: primary for tid, _, primary in CANVAS_TOOL_MODEL_DEFS}


@dataclass(frozen=True)
class CanvasToolModelResolution:
    """画布工具主/副模型解析结果。"""

    catalog_model: Model
    model_name: str
    secondary_name: str | None
    category: str


def canvas_tool_model_category(tool_id: str) -> str | None:
    """返回该工具的模型目录类别（image|text）；未纳管返回 None。"""
    return _TOOL_CATEGORY.get((tool_id or "").strip())


def t2i_counterpart_model_name(model_name: str) -> str | None:
    """将 ``nano_*_i2i`` 映射为对应文生图模型名；无法映射返回 None。

    故事板 / 设定图允许无参考图纯文字出图；后台默认主模型仍是图生图，
    无图时必须改走 ``*_t2i``，否则 RunningHub edit 接口空 ``imageUrls`` 必失败。
    """
    name = (model_name or "").strip()
    if "_i2i" not in name:
        return None
    counterpart = name.replace("_i2i", "_t2i", 1)
    return counterpart if counterpart != name else None


def media_request_has_image_source(
    source_url: str | None,
    references: list[Any] | None,
) -> bool:
    """请求是否带可用参考图（源图 URL 或 image 类 references.url）。"""
    if str(source_url or "").strip():
        return True
    for item in references or []:
        url = ""
        kind = ""
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
            kind = str(item.get("type") or "").strip().lower()
        else:
            url = str(getattr(item, "url", None) or "").strip()
            kind = str(getattr(item, "type", None) or "").strip().lower()
        if url and (not kind or kind == "image"):
            return True
    return False


def normalize_canvas_tool_models(raw: Any) -> dict[str, Any]:
    """规范化主副模型配置（含 version + tools）。缺省用各功能历史默认主模型、空副模型。"""
    version = 1
    tools: dict[str, dict[str, str]] = {
        tid: {"primary": _TOOL_DEFAULT_PRIMARY[tid], "secondary": ""}
        for tid in CANVAS_TOOL_MODEL_IDS
    }
    # 画面编辑曾用图片模型名结算，现改为视频类；读库时自动迁移常见旧默认
    _legacy_image_primaries = frozenset()
    # 智能抠像仍本地；主体消除/修改/替换已改视频上游，旧配置若仍是图片模型名一并迁移
    _video_frame_tools_migrate = frozenset(
        {
            "video_smart_matting",
            "video_subject_remove",
            "video_subject_edit",
            "video_subject_replace",
        }
    )
    if isinstance(raw, dict):
        try:
            version = max(1, int(raw.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        src = raw.get("tools") if isinstance(raw.get("tools"), dict) else raw
        if isinstance(src, dict):
            for tid, _, default_primary in CANVAS_TOOL_MODEL_DEFS:
                entry = src.get(tid)
                if not isinstance(entry, dict):
                    continue
                primary = str(entry.get("primary") or "").strip()
                secondary = str(entry.get("secondary") or "").strip()
                if primary:
                    if tid in _video_frame_tools_migrate and primary in _legacy_image_primaries:
                        primary = default_primary
                    tools[tid]["primary"] = primary
                # secondary 允许为空（表示不配置副模型）
                if secondary in _legacy_image_primaries and tid in _video_frame_tools_migrate:
                    secondary = ""
                tools[tid]["secondary"] = secondary

            # 九宫格子功能：库中尚未单独配置时，继承原 grid_9 主/副模型
            legacy_entry = src.get("grid_9") if isinstance(src.get("grid_9"), dict) else None
            if legacy_entry:
                legacy_primary = str(legacy_entry.get("primary") or "").strip()
                legacy_secondary = str(legacy_entry.get("secondary") or "").strip()
                for child_tid in CREATIVE_GRID_CHILD_TOOL_IDS:
                    if child_tid in src and isinstance(src.get(child_tid), dict):
                        continue
                    if legacy_primary:
                        tools[child_tid]["primary"] = legacy_primary
                    tools[child_tid]["secondary"] = legacy_secondary

    return {"version": version, "tools": tools}


async def get_canvas_tool_models(db: AsyncSession) -> dict[str, Any]:
    """读取并规范化主副模型配置。"""
    raw = await get_canvas_tool_models_raw(db)
    return normalize_canvas_tool_models(raw)


async def _assert_model_valid(db: AsyncSession, name: str, *, category: str, field: str) -> None:
    """校验模型存在且类别匹配；否则抛业务错误。

    注意：这里允许「用户目录默认停用」的模型（如全能图片官方稳定版），
    因为后台在此为各功能显式指定权威主/副模型，官方稳定版需可被选择作兜底。
    """
    from .model_catalog_cache import get_cached_model_by_name

    model = await get_cached_model_by_name(db, name, category=category, available_only=False)
    if not model:
        fail(
            ErrorCode.BAD_REQUEST,
            message=f"{field}「{name}」不存在（须为 {category} 类已接入模型）",
        )


async def set_canvas_tool_models(
    db: AsyncSession,
    tools: dict[str, dict[str, str]],
    *,
    bump_version: bool = True,
) -> dict[str, Any]:
    """保存各功能主副模型；校验模型有效后写库并递增 version。"""
    current = await get_canvas_tool_models(db)
    next_tools = {tid: dict(cfg) for tid, cfg in current["tools"].items()}

    for tid, entry in (tools or {}).items():
        if tid not in CANVAS_TOOL_MODEL_IDS:
            fail(ErrorCode.BAD_REQUEST, message=f"无效的画布工具：{tid}")
        if not isinstance(entry, dict):
            continue
        category = _TOOL_CATEGORY[tid]
        primary = str(entry.get("primary") or "").strip()
        secondary = str(entry.get("secondary") or "").strip()
        if not primary:
            fail(ErrorCode.BAD_REQUEST, message=f"{canvas_tool_label(tid)} 主模型不能为空")
        await _assert_model_valid(db, primary, category=category, field=f"{canvas_tool_label(tid)} 主模型")
        if secondary:
            if secondary == primary:
                fail(
                    ErrorCode.BAD_REQUEST,
                    message=f"{canvas_tool_label(tid)} 副模型不能与主模型相同",
                )
            await _assert_model_valid(
                db, secondary, category=category, field=f"{canvas_tool_label(tid)} 副模型"
            )
        next_tools[tid] = {"primary": primary, "secondary": secondary}

    next_version = int(current["version"]) + (1 if bump_version else 0)
    if next_version < 1:
        next_version = 1
    payload = {"version": next_version, "tools": next_tools}
    await set_canvas_tool_models_raw(db, payload)
    return payload


def canvas_tool_models_public_items(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """按固定顺序输出各功能主副模型配置（管理端展示用）。"""
    normalized = normalize_canvas_tool_models(cfg)
    tools = normalized["tools"]
    items: list[dict[str, Any]] = []
    for tid, category, _ in CANVAS_TOOL_MODEL_DEFS:
        entry = tools.get(tid) or {}
        items.append(
            {
                "toolId": tid,
                "label": canvas_tool_label(tid),
                "group": canvas_tool_group(tid),
                "category": category,
                "primary": str(entry.get("primary") or _TOOL_DEFAULT_PRIMARY[tid]),
                "secondary": str(entry.get("secondary") or ""),
            }
        )
    return items


async def resolve_canvas_tool_generation_model(
    db: AsyncSession,
    tool_id: str,
    *,
    requested_model: str | None = None,
) -> CanvasToolModelResolution | None:
    """按后台配置解析某画布工具的主/副生成模型（后台配置为权威）。

    返回 ``CanvasToolModelResolution``；工具未纳管或无可用模型时返回 None（调用方沿用自身逻辑）。

    - 主模型：配置的 primary 若可用则用之；否则依次尝试 secondary、requested_model。
    - 副模型（failover 用）：配置内另一个可用且与主模型不同的模型。
    """
    tid = (tool_id or "").strip()
    if tid not in CANVAS_TOOL_MODEL_IDS:
        return None

    from .model_catalog_cache import get_cached_model_by_name

    category = _TOOL_CATEGORY[tid]
    cfg = await get_canvas_tool_models(db)
    entry = cfg["tools"].get(tid) or {}
    primary_name = str(entry.get("primary") or _TOOL_DEFAULT_PRIMARY[tid]).strip()
    secondary_name = str(entry.get("secondary") or "").strip()

    # 配置候选（主、副）优先；均不存在时兜底到请求模型。
    # 后台配置为权威，允许选中「默认停用」的官方稳定版模型，故 available_only=False。
    config_candidates: list[str] = []
    for name in (primary_name, secondary_name):
        if name and name not in config_candidates:
            config_candidates.append(name)

    effective_model: Model | None = None
    effective_name: str | None = None
    for name in config_candidates:
        model = await get_cached_model_by_name(db, name, category=category, available_only=False)
        if model is None:
            model = await get_cached_model_by_name(db, name, available_only=False)
        if model:
            effective_model = model
            effective_name = name
            break

    if effective_model is None:
        # 前端传入模型仅作最后兜底；后台开关仅隐藏前端可选项，模型始终可用，故 available_only=False。
        req = (requested_model or "").strip()
        if req:
            model = await get_cached_model_by_name(db, req, category=category, available_only=False)
            if model is None:
                model = await get_cached_model_by_name(db, req, available_only=False)
            if model:
                effective_model = model
                effective_name = req

    if effective_model is None or effective_name is None:
        return None

    # 副模型：配置内另一个存在且与主模型不同的模型（失败时 failover 兜底）
    failover_name: str | None = None
    for name in config_candidates:
        if name == effective_name:
            continue
        model = await get_cached_model_by_name(db, name, category=category, available_only=False)
        if model is None:
            model = await get_cached_model_by_name(db, name, available_only=False)
        if model:
            failover_name = name
            break

    return CanvasToolModelResolution(
        catalog_model=effective_model,
        model_name=effective_name,
        secondary_name=failover_name,
        category=category,
    )
