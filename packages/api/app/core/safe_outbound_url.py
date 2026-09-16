"""出站 URL 安全校验：防 SSRF（仅允许本项目 OSS/CDN，禁止私网与云元数据）。

用于画面编辑片长探测等「按用户传入 URL 代下载」路径。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

from .config import get_settings

logger = logging.getLogger(__name__)

# 显式屏蔽的云元数据 / 链路本地主机名
_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
    }
)

MAX_REDIRECTS = 5


class UnsafeOutboundUrlError(ValueError):
    """出站 URL 未通过 SSRF 防护。"""


def _normalize_host(host: str | None) -> str:
    raw = (host or "").strip().lower().rstrip(".")
    if raw.startswith("[") and raw.endswith("]"):
        return raw[1:-1]
    return raw


def _strip_endpoint_host(endpoint: str) -> str:
    """把 OSS endpoint 配成纯主机名（去协议、路径、内网 -internal）。"""
    raw = (endpoint or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = _normalize_host(parsed.hostname)
    return host.replace("-internal", "")


def allowed_oss_media_hosts() -> set[str]:
    """本项目允许代下载的 OSS 公网 / CDN 主机名集合（小写）。"""
    settings = get_settings()
    hosts: set[str] = set()

    # 生产 CDN 固定域名：即使未配 OSS_CDN_BASE_URL 也允许（截帧 / 时长探测）
    hosts.add("cdn.example.com")

    cdn = (settings.oss_cdn_base_url or "").strip()
    if cdn:
        if "://" not in cdn:
            cdn = "https://" + cdn
        cdn_host = _normalize_host(urlparse(cdn).hostname)
        if cdn_host:
            hosts.add(cdn_host)

    bucket = (settings.oss_bucket or "").strip()
    endpoint_host = _strip_endpoint_host(settings.oss_endpoint or "")
    if endpoint_host:
        hosts.add(endpoint_host)
        if bucket:
            # 虚拟主机风格：bucket.oss-cn-xxx.aliyuncs.com
            hosts.add(f"{bucket.lower()}.{endpoint_host}")

    return {h for h in hosts if h}


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """是否为私网 / 环回 / 链路本地 / 未指定 / 组播等不可出站目标。"""
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_host_ips(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """解析主机 A/AAAA；解析失败视为不安全。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        raise UnsafeOutboundUrlError(f"无法解析主机名: {hostname}") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr in seen:
            continue
        seen.add(addr)
        try:
            ips.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not ips:
        raise UnsafeOutboundUrlError(f"主机名无有效 IP: {hostname}")
    return ips


def assert_safe_oss_media_url(url: str, *, require_https: bool = True) -> str:
    """校验 URL 可安全用于服务器代下载：HTTPS + OSS/CDN 白名单 + 解析 IP 非私网。

    返回规范化后的 URL 字符串；不通过则抛 ``UnsafeOutboundUrlError``。
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeOutboundUrlError("URL 为空")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if require_https:
        if scheme != "https":
            raise UnsafeOutboundUrlError("仅允许 HTTPS 出站")
    elif scheme not in ("http", "https"):
        raise UnsafeOutboundUrlError("仅允许 HTTP/HTTPS")

    if parsed.username is not None or parsed.password is not None:
        raise UnsafeOutboundUrlError("禁止带用户信息的 URL")

    host = _normalize_host(parsed.hostname)
    if not host:
        raise UnsafeOutboundUrlError("缺少主机名")

    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise UnsafeOutboundUrlError("主机名不允许")

    # 主机名本身是 IP 时，直接判私网 / 元数据
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if is_blocked_ip(literal_ip):
            raise UnsafeOutboundUrlError("禁止访问私网或链路本地地址")
        # 字面量公网 IP 也不在 OSS 白名单内，一律拒绝（防绕过域名白名单）
        raise UnsafeOutboundUrlError("禁止直接使用 IP 出站")

    allowed = allowed_oss_media_hosts()
    if not allowed:
        raise UnsafeOutboundUrlError("未配置 OSS/CDN 出站白名单")
    if host not in allowed:
        logger.warning("ssrf guard rejected host=%s allowed=%s", host, sorted(allowed))
        raise UnsafeOutboundUrlError("源视频域名不在允许范围内")

    for ip in _resolve_host_ips(host):
        if is_blocked_ip(ip):
            raise UnsafeOutboundUrlError("解析结果指向私网或元数据地址")

    return raw


def resolve_redirect_url(current: str, location: str) -> str:
    """拼接 Location，并再次走完整安全校验。"""
    loc = (location or "").strip()
    if not loc:
        raise UnsafeOutboundUrlError("重定向缺少 Location")
    nxt = urljoin(current, loc)
    return assert_safe_oss_media_url(nxt)
