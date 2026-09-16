"""浏览器侧存储 URL 生成：开发代理、OSS 预签名或生产 CDN。"""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, quote, unquote, urlparse

from ..core.config import get_settings
from ..integrations.oss.service import _ensure_https_url, get_oss

logger = logging.getLogger(__name__)

_STORAGE_KEY_PREFIXES = ("canvas", "Ihuabu")


def _strip_legacy_web_base(path_or_url: str) -> str:
    """去掉历史 Web basePath 前缀 /canvas，便于 c 域空 basePath 解析旧链接。"""
    raw = str(path_or_url or "").strip()
    if raw.startswith("/canvas/"):
        return raw[len("/canvas") :]
    if raw.startswith("/canvas?"):
        return raw[len("/canvas") :]
    return raw


def in_app_storage_url(oss_key: str) -> str:
    """生成同源 BFF 代理 URL，供应用内预览（支持 Cookie 鉴权与本地回退）。"""
    if not oss_key:
        return ""
    return f"/api/storage/object?key={quote(oss_key, safe='')}"


def storage_object_url(oss_key: str) -> str:
    """根据当前配置返回存储对象的浏览器可访问 URL。"""
    return public_url_for_key(oss_key)


def public_url_for_key(oss_key: str, *, image_process: str | None = None) -> str:
    """按 storage_url_mode 将 OSS key 转为 CDN、预签名或代理 URL。

    image_process：可选阿里云图片处理串（如 image/resize,l_480/format,webp），
    仅 signed/cdn 生效；proxy 模式忽略（由本地压缩/前端降采样兜底）。
    """
    if not oss_key:
        return ""

    settings = get_settings()
    mode = (settings.storage_url_mode or "proxy").strip().lower()
    process = (image_process or "").strip() or None

    if mode == "cdn":
        # 未配 OSS_CDN_BASE_URL 时默认生产 CDN，避免空 base  silently 回退 proxy
        base = (settings.oss_cdn_base_url or "https://cdn.example.com").rstrip("/")
        if base:
            url = f"{base}/{oss_key.lstrip('/')}"
            if process:
                sep = "&" if "?" in url else "?"
                return f"{url}{sep}x-oss-process={quote(process, safe=',/')}"
            return url

    if mode == "signed":
        oss = get_oss()
        if oss.bucket:
            params = {"x-oss-process": process} if process else None
            signed = oss.presign_get(
                oss_key,
                settings.oss_signed_url_ttl_seconds,
                params=params,
            )
            if signed and signed.startswith("http"):
                return _ensure_https_url(signed)
        logger.warning(
            "STORAGE_URL_MODE=signed but presign failed for %s — falling back to proxy",
            oss_key,
        )

    return f"/api/storage/object?key={quote(oss_key, safe='')}"


def oss_key_from_browser_url(url: str) -> str | None:
    """从代理 URL、OSS 预签名 URL 或裸 key 字符串解析逻辑 OSS key。"""
    raw = _strip_legacy_web_base(str(url or "").strip())
    if not raw:
        return None

    settings = get_settings()
    configured = (settings.oss_object_prefix or "canvas").rstrip("/")
    prefixes = tuple(dict.fromkeys((configured, *_STORAGE_KEY_PREFIXES)))

    if raw.startswith("/api/storage/object") or "key=" in raw:
        query = raw.split("?", 1)[-1] if "?" in raw else ""
        keys = parse_qs(query).get("key") or []
        if keys:
            key = unquote(keys[0])
            if any(key.startswith(f"{p}/") for p in prefixes):
                return key

    if not raw.startswith("http://") and not raw.startswith("https://"):
        bare = raw.lstrip("/")
        if any(bare.startswith(f"{p}/") for p in prefixes):
            return bare
        return None

    parsed = urlparse(raw)
    path_key = unquote((parsed.path or "").lstrip("/"))
    if any(path_key.startswith(f"{p}/") for p in prefixes):
        return path_key
    return None


def normalize_browser_storage_url(url: str, *, oss_key: str | None = None) -> str:
    """将历史或混合格式的存储 URL 规范化为当前模式下的可访问地址。

    中文说明：预签名 URL 会过期。凡能解析出 OSS key 的（含过期签名、代理 URL、裸 key），
    一律按当前 STORAGE_URL_MODE 重新签发，禁止原样回传 DB 里陈旧的 Expires 链接。
    """
    if oss_key:
        return public_url_for_key(oss_key)
    if not url:
        return url

    # 应用内静态占位图（如音频默认封面）保持原路径
    if url.startswith("/uploads/"):
        return url

    normalized = _strip_legacy_web_base(url)
    normalized = (
        normalized.replace("/api/proxy/v1/storage/object", "/api/storage/object")
        .replace("/api/proxy/api/v1/storage/object", "/api/storage/object")
    )

    # 优先从任意形态解析 key 并重签（覆盖过期 OSS 预签名、CDN、代理）
    key = oss_key_from_browser_url(normalized)
    if key:
        return public_url_for_key(key)

    if normalized.startswith("/api/storage/object"):
        return normalized

    return normalized


def persistable_media_url(url: str | None) -> str | None:
    """写入 DB 的媒体 URL：能解析 key 则写当前模式公共地址，禁止签权串落库。

    中文：封面 / 发现页等长期字段只存 CDN（或 proxy 路径），不存带 Expires 的 OSS 直链。
    无法解析的外链（如 Unsplash）原样保留；无法解析的签权串丢弃。
    """
    raw = str(url or "").strip()
    if not raw:
        return None
    if raw.startswith("/uploads/"):
        return raw

    key = oss_key_from_browser_url(raw)
    if key:
        return public_url_for_key(key) or None

    lower = raw.lower()
    if "expires=" in lower and ("ossaccesskeyid=" in lower or "signature=" in lower):
        logger.warning("refuse to persist signed OSS URL without parsable key: %s...", raw[:80])
        return None

    if raw.startswith("https://") or raw.startswith("http://"):
        return raw
    return None


def _is_direct_oss_url(url: str) -> bool:
    """判断是否为直连 OSS/CDN URL（含预签名查询串）。供参考图等路径探测使用。"""
    if not url.startswith("http://") and not url.startswith("https://"):
        return False
    lower = url.lower()
    if "expires=" in lower and ("ossaccesskeyid=" in lower or "signature=" in lower):
        return True
    settings = get_settings()
    cdn = (settings.oss_cdn_base_url or "").rstrip("/").lower()
    if cdn and lower.startswith(cdn):
        return True
    return False
