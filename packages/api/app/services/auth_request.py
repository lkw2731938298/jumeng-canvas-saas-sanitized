"""从 HTTP 请求提取客户端 IP 与 User-Agent。"""

from __future__ import annotations

from fastapi import Request


def client_ip(request: Request | None) -> str:
    """解析客户端真实 IP（优先 X-Forwarded-For / X-Real-IP）。"""
    if request is None:
        return ""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    real_ip = (request.headers.get("x-real-ip") or "").strip()
    if real_ip:
        return real_ip[:64]
    return ((request.client.host if request.client else "") or "").strip()[:64]


def client_user_agent(request: Request | None) -> str:
    """解析客户端 User-Agent 请求头。"""
    if request is None:
        return ""
    return (request.headers.get("user-agent") or "").strip()[:512]
