"""用户 Access Key 管理（站内会话鉴权）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.errors import ok
from ...models.database import get_db
from ...models.user import User
from ...services import agent_access_keys

router = APIRouter()


class CreateKeyBody(BaseModel):
    name: str = Field("default", max_length=64)
    note: str | None = Field(None, max_length=256)

    model_config = {"populate_by_name": True}


@router.get("/keys")
async def list_keys(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户 Access Key（不含明文）。"""
    items = await agent_access_keys.list_user_agent_keys(db, current_user)
    return ok({"items": items})


@router.post("/keys")
async def create_key(
    body: CreateKeyBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建 Access Key；响应含 accessKey 明文（仅此一次）。"""
    content = await agent_access_keys.create_user_agent_key(
        db, current_user, name=body.name, note=body.note
    )
    await db.commit()
    return ok(content)


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """吊销 Access Key。"""
    content = await agent_access_keys.revoke_user_agent_key(db, current_user, key_id)
    await db.commit()
    return ok(content)
