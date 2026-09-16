"""Public error code reference API."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...core.error_codes import list_error_codes

router = APIRouter()


class ErrorCodeOut(BaseModel):
    code: str
    message_zh: str = Field(..., alias="messageZh")
    category: str
    http_status: int | None = Field(None, alias="httpStatus")

    model_config = {"populate_by_name": True}


@router.get("/error-codes", response_model=list[ErrorCodeOut])
async def get_error_codes():
    """错误编码表：code → 中文说明（供前端与管理端查阅）。"""
    return list_error_codes()
