"""首页 Footer 运营内容：读写 ``platform_settings.site_footer``。

配置「关于我们」跳转、「联系我们」二维码（可多个）、社交平台跳转链接、发现页友情链接。
未配置时回退内置默认（与前端 discover 页静态文案对齐）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive
from .platform_settings import _ensure_settings_row
from .storage_urls import in_app_storage_url, normalize_browser_storage_url, oss_key_from_browser_url


def _new_id() -> str:
    return uuid.uuid4().hex


def default_site_footer() -> dict[str, Any]:
    """内置默认 Footer 配置。"""
    return {
        "brandText": "聚梦-专业视频创作平台",
        "copyright": "© 2026 JUMENG AI",
        "tagline": "为每一个未被看见的想象而生",
        # 顶栏「教程」按钮打开的链接；空则前端提示未配置
        "helpUrl": "",
        "aboutUs": {
            "label": "关于我们",
            "href": "",
        },
        "contactUs": {
            "label": "联系我们",
            "qrCodes": [],
        },
        "socialLinks": [
            {"id": _new_id(), "label": "小红书", "href": "", "sortOrder": 0},
            {"id": _new_id(), "label": "哔哩哔哩", "href": "", "sortOrder": 1},
            {"id": _new_id(), "label": "抖音", "href": "", "sortOrder": 2},
            {"id": _new_id(), "label": "视频号", "href": "", "sortOrder": 3},
        ],
        # 发现页底部友情链接；默认空，由后台配置后才展示
        "friendLinks": [],
        "friendLinksLabel": "友情链接",
        # ICP 备案号默认空；部署方在管理后台自行填写后才展示
        "icpNumber": "",
        "icpHref": "https://beian.miit.gov.cn/",
    }


def _normalize_help_url(raw: Any) -> str:
    """允许 https?/ 绝对链或站内相对路径；其余丢弃为空。"""
    text = _as_str(raw, "")
    if not text:
        return ""
    lower = text.lower()
    if lower.startswith("https://") or lower.startswith("http://"):
        return text[:1024]
    if text.startswith("/") and not text.startswith("//"):
        return text[:1024]
    return ""


def _as_str(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text or default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stabilize_footer_image_url(url: str) -> str:
    """落库用稳定代理 URL；外链（非本平台 OSS）原样保留。"""
    value = _as_str(url)
    if not value:
        return ""
    key = oss_key_from_browser_url(value)
    if key:
        return in_app_storage_url(key)
    return value


def _resign_footer_image_url(url: str) -> str:
    """读取时重签 OSS 图，避免库内过期预签名导致二维码 403。"""
    value = _as_str(url)
    if not value:
        return ""
    return normalize_browser_storage_url(value) or value


def resign_site_footer_media(payload: dict[str, Any]) -> dict[str, Any]:
    """公开/管理端返回前重签联系我们二维码等 OSS 图片。"""
    out = dict(payload)
    contact = dict(out.get("contactUs") or {})
    qrs: list[dict[str, Any]] = []
    for entry in contact.get("qrCodes") or []:
        if not isinstance(entry, dict):
            continue
        qrs.append(
            {
                **entry,
                "imageUrl": _resign_footer_image_url(str(entry.get("imageUrl") or "")),
            }
        )
    contact["qrCodes"] = qrs
    out["contactUs"] = contact
    return out


def normalize_site_footer(raw: Any) -> dict[str, Any]:
    """校验并规范化 Footer 配置；非法字段丢弃，缺失字段用默认值补齐。"""
    base = default_site_footer()
    if not isinstance(raw, dict) or not raw:
        return base

    about_raw = raw.get("aboutUs") or raw.get("about_us") or {}
    if not isinstance(about_raw, dict):
        about_raw = {}
    about_us = {
        "label": _as_str(about_raw.get("label"), base["aboutUs"]["label"]),
        "href": _as_str(about_raw.get("href"), ""),
    }

    contact_raw = raw.get("contactUs") or raw.get("contact_us") or {}
    if not isinstance(contact_raw, dict):
        contact_raw = {}
    qr_codes: list[dict[str, Any]] = []
    for i, entry in enumerate(contact_raw.get("qrCodes") or contact_raw.get("qr_codes") or []):
        if not isinstance(entry, dict):
            continue
        # 保存路径：预签名 → 稳定代理；读取路径另走 resign_site_footer_media
        image_url = _stabilize_footer_image_url(
            _as_str(entry.get("imageUrl") or entry.get("image_url"))
        )
        if not image_url:
            continue
        qr_codes.append(
            {
                "id": _as_str(entry.get("id")) or _new_id(),
                "label": _as_str(entry.get("label"), f"二维码{i + 1}"),
                "imageUrl": image_url,
                "sortOrder": _as_int(entry.get("sortOrder", entry.get("sort_order")), i),
            }
        )
    qr_codes.sort(key=lambda x: (x["sortOrder"], x["id"]))
    contact_us = {
        "label": _as_str(contact_raw.get("label"), base["contactUs"]["label"]),
        "qrCodes": qr_codes,
    }

    social_links: list[dict[str, Any]] = []
    for i, entry in enumerate(raw.get("socialLinks") or raw.get("social_links") or []):
        if not isinstance(entry, dict):
            continue
        label = _as_str(entry.get("label"))
        if not label:
            continue
        social_links.append(
            {
                "id": _as_str(entry.get("id")) or _new_id(),
                "label": label,
                "href": _as_str(entry.get("href"), ""),
                "sortOrder": _as_int(entry.get("sortOrder", entry.get("sort_order")), i),
            }
        )
    social_links.sort(key=lambda x: (x["sortOrder"], x["id"]))
    if not social_links:
        social_links = list(base["socialLinks"])

    # 友情链接：须有名称 + http(s) 才保留（空列表不展示）
    friend_links: list[dict[str, Any]] = []
    for i, entry in enumerate(raw.get("friendLinks") or raw.get("friend_links") or []):
        if not isinstance(entry, dict):
            continue
        label = _as_str(entry.get("label"))
        href = _normalize_help_url(entry.get("href"))
        if not label or not href:
            continue
        if not (href.lower().startswith("http://") or href.lower().startswith("https://")):
            continue
        friend_links.append(
            {
                "id": _as_str(entry.get("id")) or _new_id(),
                "label": label[:64],
                "href": href[:1024],
                "sortOrder": _as_int(entry.get("sortOrder", entry.get("sort_order")), i),
            }
        )
    friend_links.sort(key=lambda x: (x["sortOrder"], x["id"]))
    friend_label = _as_str(
        raw.get("friendLinksLabel") or raw.get("friend_links_label"),
        base["friendLinksLabel"],
    )[:32] or base["friendLinksLabel"]
    # 旧默认「友链>>>」统一改为「友情链接」
    if friend_label in ("友链>>>", "友链"):
        friend_label = base["friendLinksLabel"]

    # 备案号：键存在则尊重空串（可隐藏）；未配置过则用默认
    if "icpNumber" in raw or "icp_number" in raw:
        icp_number = _as_str(raw.get("icpNumber") or raw.get("icp_number"), "")[:64]
    else:
        icp_number = base["icpNumber"]
    if "icpHref" in raw or "icp_href" in raw:
        icp_href = _normalize_help_url(raw.get("icpHref") or raw.get("icp_href"))
    else:
        icp_href = base["icpHref"]
    if icp_number and not icp_href:
        icp_href = base["icpHref"]

    return {
        "brandText": _as_str(raw.get("brandText") or raw.get("brand_text"), base["brandText"]),
        "copyright": _as_str(raw.get("copyright"), base["copyright"]),
        "tagline": _as_str(raw.get("tagline"), base["tagline"]),
        "helpUrl": _normalize_help_url(raw.get("helpUrl") or raw.get("help_url")),
        "aboutUs": about_us,
        "contactUs": contact_us,
        "socialLinks": social_links,
        "friendLinks": friend_links,
        "friendLinksLabel": friend_label,
        "icpNumber": icp_number,
        "icpHref": icp_href,
    }


async def get_site_footer_settings(db: AsyncSession) -> dict[str, Any]:
    """读取 Footer 配置；库中为空时返回默认并带 updated_at。"""
    row = await _ensure_settings_row(db)
    raw = getattr(row, "site_footer", None)
    # normalize 会把历史预签名压成稳定 key；再 resign 供浏览器立即可用
    payload = resign_site_footer_media(normalize_site_footer(raw))
    return {
        **payload,
        "updatedAt": row.updated_at,
    }


async def set_site_footer_settings(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """整表保存 Footer 配置。"""
    row = await _ensure_settings_row(db)
    normalized = normalize_site_footer(payload)
    row.site_footer = {k: v for k, v in normalized.items()}
    row.updated_at = now_cst_naive()
    await db.flush()
    # 响应里重签，管理端保存后预览立即可用
    return {
        **resign_site_footer_media(normalized),
        "updatedAt": row.updated_at,
    }
