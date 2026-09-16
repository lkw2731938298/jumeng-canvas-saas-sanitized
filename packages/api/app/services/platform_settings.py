"""平台全局配置服务（MySQL 权威，管理端可覆盖环境变量默认值）。

涵盖注册开关、登录页背景图网格等 ``platform_settings`` 单行配置的读写。
"""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..models.platform_settings import PlatformSettings
from .homepage_background import (
    AUTH_GRID_IMAGE_MAX_COUNT,
    normalize_auth_grid_images,
    recompress_auth_grid_image_object,
    resolve_login_modal_image_url,
    serialize_auth_grid_images_for_client,
)
from .legal_documents import (
    build_legal_document_entry,
    load_legal_document_markdown,
    normalize_legal_documents,
    normalize_legal_doc_type,
    serialize_legal_documents_for_admin,
    upload_legal_document,
)


async def _ensure_settings_row(db: AsyncSession) -> PlatformSettings:
    result = await db.execute(select(PlatformSettings).filter(PlatformSettings.id == 1))
    row = result.scalar_one_or_none()
    if row:
        return row
    settings = get_settings()
    row = PlatformSettings(
        id=1,
        registration_enabled=bool(settings.registration_enabled),
        homepage_background_mode="default",
        homepage_background_oss_key=None,
        auth_grid_images=[],
        login_modal_oss_key=None,
        register_bonus_enabled=False,
        register_bonus_amount=0,
        default_storage_gb=2,
        updated_at=now_cst_naive(),
    )
    db.add(row)
    await db.flush()
    return row


def _homepage_settings_payload(row: PlatformSettings) -> dict:
    auth_grid_images = serialize_auth_grid_images_for_client(
        normalize_auth_grid_images(row.auth_grid_images)
    )
    return {
        "auth_grid_images": auth_grid_images,
        "login_modal_oss_key": str(getattr(row, "login_modal_oss_key", None) or "").strip(),
        "login_modal_image_url": resolve_login_modal_image_url(
            getattr(row, "login_modal_oss_key", None)
        ),
        "legal_documents": serialize_legal_documents_for_admin(
            getattr(row, "legal_documents", None)
        ),
        "updated_at": row.updated_at,
    }


async def is_registration_enabled(db: AsyncSession | None = None) -> bool:
    """查询用户注册是否开放；无 DB 会话时回退环境变量默认值。"""
    settings = get_settings()
    if db is None:
        return bool(settings.registration_enabled)
    row = await _ensure_settings_row(db)
    return bool(row.registration_enabled)


async def get_auth_settings(db: AsyncSession) -> dict:
    """返回注册开关当前值、环境默认值及更新时间。"""
    row = await _ensure_settings_row(db)
    env_default = bool(get_settings().registration_enabled)
    return {
        "registration_enabled": bool(row.registration_enabled),
        "env_registration_default": env_default,
        "updated_at": row.updated_at,
    }


async def set_registration_enabled(db: AsyncSession, enabled: bool) -> dict:
    """设置用户注册开关并返回更新后的认证配置。"""
    row = await _ensure_settings_row(db)
    row.registration_enabled = bool(enabled)
    row.updated_at = now_cst_naive()
    await db.flush()
    return await get_auth_settings(db)


async def get_register_bonus_settings(db: AsyncSession) -> dict:
    """首次注册赠送开关与点数。"""
    row = await _ensure_settings_row(db)
    try:
        amount = int(getattr(row, "register_bonus_amount", 0) or 0)
    except (TypeError, ValueError):
        amount = 0
    amount = max(0, min(amount, 1_000_000))
    return {
        "enabled": bool(getattr(row, "register_bonus_enabled", False)),
        "amount": amount,
        "updated_at": row.updated_at,
    }


async def set_register_bonus_settings(
    db: AsyncSession, *, enabled: bool, amount: int
) -> dict:
    """保存首次注册赠送配置。"""
    try:
        amt = int(amount)
    except (TypeError, ValueError):
        amt = 0
    amt = max(0, min(amt, 1_000_000))
    row = await _ensure_settings_row(db)
    row.register_bonus_enabled = bool(enabled)
    row.register_bonus_amount = amt
    row.updated_at = now_cst_naive()
    await db.flush()
    return await get_register_bonus_settings(db)


async def get_homepage_settings(db: AsyncSession) -> dict:
    """返回登录页背景图网格配置及更新时间。"""
    row = await _ensure_settings_row(db)
    return _homepage_settings_payload(row)


async def add_auth_grid_image(
    db: AsyncSession,
    *,
    image_id: str,
    oss_key: str,
) -> dict:
    """向登录背景图网格追加一张图片（受最大数量限制）。"""
    row = await _ensure_settings_row(db)
    items = normalize_auth_grid_images(row.auth_grid_images)
    if len(items) >= AUTH_GRID_IMAGE_MAX_COUNT:
        raise ValueError(f"最多上传 {AUTH_GRID_IMAGE_MAX_COUNT} 张登录背景图")

    next_order = max((item["sortOrder"] for item in items), default=-1) + 1
    items.append({"id": image_id, "ossKey": oss_key, "sortOrder": next_order})
    row.auth_grid_images = items
    row.updated_at = now_cst_naive()
    await db.flush()
    return _homepage_settings_payload(row)


async def remove_auth_grid_image(db: AsyncSession, image_id: str) -> dict:
    """按图片 ID 从登录背景图网格中移除一项。"""
    row = await _ensure_settings_row(db)
    target = str(image_id or "").strip()
    items = [
        item
        for item in normalize_auth_grid_images(row.auth_grid_images)
        if item["id"] != target
    ]
    row.auth_grid_images = items
    row.updated_at = now_cst_naive()
    await db.flush()
    return _homepage_settings_payload(row)


async def clear_auth_grid_images(db: AsyncSession) -> dict:
    """清空全部登录背景图网格配置。"""
    row = await _ensure_settings_row(db)
    row.auth_grid_images = []
    row.updated_at = now_cst_naive()
    await db.flush()
    return _homepage_settings_payload(row)


async def recompress_auth_grid_images(db: AsyncSession) -> dict:
    """一键重压已配置的登录背景图（历史大图 JPG/PNG → WebP 小图）。"""
    row = await _ensure_settings_row(db)
    items = normalize_auth_grid_images(row.auth_grid_images)
    rewritten = 0
    skipped = 0
    failed = 0
    next_items: list[dict] = []
    for item in items:
        new_key, action = recompress_auth_grid_image_object(item["id"], item["ossKey"])
        if action == "rewritten":
            rewritten += 1
        elif action == "skipped":
            skipped += 1
        else:
            failed += 1
        next_items.append(
            {
                "id": item["id"],
                "ossKey": new_key,
                "sortOrder": item["sortOrder"],
            }
        )
    row.auth_grid_images = next_items
    row.updated_at = now_cst_naive()
    await db.flush()
    payload = _homepage_settings_payload(row)
    payload["recompress_stats"] = {
        "rewritten": rewritten,
        "skipped": skipped,
        "failed": failed,
        "total": len(items),
    }
    return payload


async def set_login_modal_image(db: AsyncSession, oss_key: str) -> dict:
    """写入登录弹窗左侧图 OSS key。"""
    row = await _ensure_settings_row(db)
    row.login_modal_oss_key = str(oss_key or "").strip() or None
    row.updated_at = now_cst_naive()
    await db.flush()
    return _homepage_settings_payload(row)


async def clear_login_modal_image(db: AsyncSession) -> dict:
    """清空登录弹窗左侧图。"""
    return await set_login_modal_image(db, "")


async def set_legal_document(
    db: AsyncSession,
    doc_type: str,
    *,
    filename: str,
    data: bytes,
) -> dict:
    """上传并写入用户协议或隐私政策。"""
    from sqlalchemy.orm.attributes import flag_modified

    normalized_type = normalize_legal_doc_type(doc_type)
    if not normalized_type:
        raise ValueError("未知的文档类型")
    uploaded = upload_legal_document(normalized_type, filename, data)
    row = await _ensure_settings_row(db)
    docs = normalize_legal_documents(getattr(row, "legal_documents", None))
    docs[normalized_type] = build_legal_document_entry(uploaded)
    row.legal_documents = docs
    flag_modified(row, "legal_documents")
    row.updated_at = now_cst_naive()
    await db.flush()
    return _homepage_settings_payload(row)


async def clear_legal_document(db: AsyncSession, doc_type: str) -> dict:
    """清除已配置的用户协议或隐私政策。"""
    from sqlalchemy.orm.attributes import flag_modified

    normalized_type = normalize_legal_doc_type(doc_type)
    if not normalized_type:
        raise ValueError("未知的文档类型")
    row = await _ensure_settings_row(db)
    docs = normalize_legal_documents(getattr(row, "legal_documents", None))
    if normalized_type in docs:
        docs.pop(normalized_type, None)
    row.legal_documents = docs or None
    flag_modified(row, "legal_documents")
    row.updated_at = now_cst_naive()
    await db.flush()
    return _homepage_settings_payload(row)


async def get_legal_document_public(db: AsyncSession, doc_type: str) -> dict:
    """公开读取法务文档正文（Markdown）。"""
    normalized_type = normalize_legal_doc_type(doc_type)
    if not normalized_type:
        raise ValueError("未知的文档类型")
    row = await _ensure_settings_row(db)
    docs = normalize_legal_documents(getattr(row, "legal_documents", None))
    entry = docs.get(normalized_type)
    if not entry:
        return {
            "docType": normalized_type,
            "title": "",
            "markdown": "",
            "configured": False,
            "updatedAt": None,
        }
    markdown = load_legal_document_markdown(entry)
    return {
        "docType": normalized_type,
        "title": entry.get("title") or "",
        "markdown": markdown,
        "configured": bool(markdown.strip()),
        "updatedAt": entry.get("updatedAt") or None,
    }


async def get_storage_settings(db: AsyncSession) -> dict:
    """返回平台通用云存储配额（GiB）。"""
    from .storage_quota import DEFAULT_GENERAL_STORAGE_GB, get_default_storage_gb

    row = await _ensure_settings_row(db)
    gb = await get_default_storage_gb(db)
    return {
        "defaultStorageGb": gb,
        "envDefaultStorageGb": DEFAULT_GENERAL_STORAGE_GB,
        "updatedAt": row.updated_at,
    }


async def set_default_storage_gb_setting(db: AsyncSession, gb: int) -> dict:
    """设置平台通用云存储配额（GiB）。"""
    from .storage_quota import set_default_storage_gb

    await set_default_storage_gb(db, gb)
    return await get_storage_settings(db)


async def get_recharge_tiers_raw(db: AsyncSession) -> list | None:
    """读取 platform_settings 中的充值档位 JSON（未配置返回 None）。"""
    row = await _ensure_settings_row(db)
    raw = row.recharge_tiers
    if raw is None:
        return None
    if isinstance(raw, list):
        return raw
    return None


async def get_recharge_tiers_settings(db: AsyncSession) -> dict:
    """返回充值档位列表及更新时间。"""
    from .payment_tiers import load_recharge_tiers

    row = await _ensure_settings_row(db)
    tiers = await load_recharge_tiers(db)
    return {
        "items": tiers,
        "updatedAt": row.updated_at,
    }


async def set_recharge_tiers_settings(db: AsyncSession, tiers: list[dict]) -> dict:
    """保存充值档位配置（整表替换）。"""
    from .payment_tiers import normalize_recharge_tiers

    row = await _ensure_settings_row(db)
    row.recharge_tiers = normalize_recharge_tiers(tiers)
    row.updated_at = now_cst_naive()
    await db.flush()
    return await get_recharge_tiers_settings(db)


async def get_canvas_tool_pricing_raw(db: AsyncSession) -> dict | list | None:
    """读取画布工具算力 JSON（未配置返回 None）。"""
    row = await _ensure_settings_row(db)
    raw = row.canvas_tool_pricing
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return None


async def set_canvas_tool_pricing_raw(db: AsyncSession, payload: dict) -> None:
    """写入画布工具算力 JSON。"""
    row = await _ensure_settings_row(db)
    row.canvas_tool_pricing = payload
    row.updated_at = now_cst_naive()
    await db.flush()


async def get_agent_skill_pricing_raw(db: AsyncSession) -> dict | list | None:
    """读取 Agent 编排轨 S 定价 JSON。"""
    row = await _ensure_settings_row(db)
    raw = getattr(row, "agent_skill_pricing", None)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return None


async def set_agent_skill_pricing_raw(db: AsyncSession, payload: dict) -> None:
    """写入 Agent 编排轨 S 定价 JSON。"""
    from sqlalchemy.orm.attributes import flag_modified

    row = await _ensure_settings_row(db)
    row.agent_skill_pricing = payload
    flag_modified(row, "agent_skill_pricing")
    row.updated_at = now_cst_naive()
    await db.flush()


async def get_canvas_tool_models_raw(db: AsyncSession) -> dict | list | None:
    """读取画布/分镜各功能主副模型切换 JSON（未配置返回 None）。"""
    row = await _ensure_settings_row(db)
    raw = row.canvas_tool_models
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return None


async def set_canvas_tool_models_raw(db: AsyncSession, payload: dict) -> None:
    """写入画布/分镜各功能主副模型切换 JSON。"""
    from sqlalchemy.orm.attributes import flag_modified

    row = await _ensure_settings_row(db)
    row.canvas_tool_models = payload
    # MySQL JSON 列须显式标记，避免 ORM 未检测到变更而不落库
    flag_modified(row, "canvas_tool_models")
    row.updated_at = now_cst_naive()
    await db.flush()


async def get_model_ui_tags_raw(db: AsyncSession) -> dict | list | None:
    """读取画布模型 UI 标签库 JSON（未配置返回 None）。"""
    row = await _ensure_settings_row(db)
    raw = row.model_ui_tags
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return None


async def set_model_ui_tags_raw(db: AsyncSession, payload: dict) -> None:
    """写入画布模型 UI 标签库 JSON。"""
    row = await _ensure_settings_row(db)
    row.model_ui_tags = payload
    row.updated_at = now_cst_naive()
    await db.flush()


async def get_model_ui_series_raw(db: AsyncSession) -> dict | list | None:
    """读取画布模型系列展示顺序 JSON（未配置返回 None）。"""
    row = await _ensure_settings_row(db)
    raw = getattr(row, "model_ui_series", None)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    return None


async def set_model_ui_series_raw(db: AsyncSession, payload: dict) -> None:
    """写入画布模型系列展示顺序 JSON。"""
    row = await _ensure_settings_row(db)
    row.model_ui_series = payload
    row.updated_at = now_cst_naive()
    await db.flush()


# Skill 公开目录默认分类（不含「推荐」）
DEFAULT_SKILL_CATEGORIES: list[str] = [
    "通用技能",
    "短剧漫剧",
    "电商带货",
    "IP口播",
    "商业广告",
    "动漫游戏",
    "教育生活",
]


def normalize_skill_categories(raw: object | None) -> list[str]:
    """规范化 Skill 分类列表：去空、去重、保序。"""
    if not isinstance(raw, list):
        return list(DEFAULT_SKILL_CATEGORIES)
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item or "").strip()
        if not name or name == "推荐" or name in seen:
            continue
        seen.add(name)
        out.append(name[:64])
    return out or list(DEFAULT_SKILL_CATEGORIES)


async def get_skill_categories(db: AsyncSession) -> list[str]:
    """读取 Skill 分类配置；未配置则返回默认列表。"""
    row = await _ensure_settings_row(db)
    return normalize_skill_categories(getattr(row, "skill_categories", None))


async def set_skill_categories(db: AsyncSession, categories: list[str]) -> list[str]:
    """写入 Skill 分类配置。"""
    normalized = normalize_skill_categories(categories)
    row = await _ensure_settings_row(db)
    row.skill_categories = normalized
    row.updated_at = now_cst_naive()
    await db.flush()
    return normalized
