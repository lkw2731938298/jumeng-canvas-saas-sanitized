"""支付宝异步通知 HTTP 处理（无登录；验签+查单后入账）。

新路径：POST /api/v1/payments/alipay-notify
兼容旧路径：POST /api/v1/admin/recharge-orders/alipay-notify
"""

from __future__ import annotations

from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ..common.utils.file_log import write_file_log
from ..integrations.alipay import get_alipay_client
from .payment_orders import handle_alipay_notify


async def handle_alipay_notify_http(request: Request, db: AsyncSession) -> Response:
    """处理支付宝 server-to-server 表单通知，返回纯文本 success/failure。"""
    client = get_alipay_client()
    if client is None:
        return Response(content="failure", media_type="text/plain")

    raw = await request.body()
    # 原文：支付宝异步通知 HTTP body（application/x-www-form-urlencoded）
    write_file_log("payment", raw.decode("utf-8", errors="replace"), tag="NOTIFY")

    form_data = client.parse_notify_form(raw)
    result = await handle_alipay_notify(db, form_data)
    write_file_log("payment", result, tag="NOTIFY_RESULT")
    return Response(content=result, media_type="text/plain")
