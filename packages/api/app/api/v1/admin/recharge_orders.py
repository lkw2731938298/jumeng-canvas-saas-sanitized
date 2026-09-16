"""管理端充值/支付订单；旧版支付宝回调路径仅作兼容。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_RECHARGE_ORDERS
from ....core.entity_ids import parse_entity_id
from ....models.database import get_db
from ....models.user import User
from ....schemas.admin import AdminRechargeOrderListOut, AdminRechargeOrderOut
from ....services.alipay_notify_http import handle_alipay_notify_http
from ....services.recharge_order_query import list_recharge_orders

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=AdminRechargeOrderListOut)
async def list_admin_recharge_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    user_id: Optional[str] = Query(None, alias="userId"),
    order_type: Optional[str] = Query(None, alias="orderType"),
    status: Optional[str] = Query(None),
    payment_channel: Optional[str] = Query(None, alias="paymentChannel"),
    out_trade_no: Optional[str] = Query(None, alias="outTradeNo"),
    created_from: Optional[datetime] = Query(None, alias="createdFrom"),
    created_to: Optional[datetime] = Query(None, alias="createdTo"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_RECHARGE_ORDERS)),
):
    """分页查询全站充值/支付订单（算力充值、会员开通）。"""
    user_id_int: int | None = None
    user_filter: str | None = None
    if user_id:
        trimmed = user_id.strip()
        parsed = parse_entity_id(trimmed)
        if parsed is not None:
            user_id_int = parsed
        else:
            user_filter = trimmed

    items_raw, total = await list_recharge_orders(
        db,
        user_id=user_id_int,
        user_filter=user_filter,
        order_type=order_type.strip() if order_type else None,
        status=status.strip() if status else None,
        payment_channel=payment_channel.strip() if payment_channel else None,
        out_trade_no=out_trade_no.strip() if out_trade_no else None,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
    )
    items = [AdminRechargeOrderOut(**row) for row in items_raw]
    return AdminRechargeOrderListOut(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/alipay-notify")
async def alipay_notify_legacy(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """兼容旧回调路径；权威地址为 POST /api/v1/payments/alipay-notify。"""
    return await handle_alipay_notify_http(request, db)
