"""管理端工作流发布列表与审核。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_HOMEPAGE
from ....core.deps import require_permission
from ....core.errors import ok
from ....models.database import get_db
from ....models.user import User
from ....services import workflow_publications as pub_svc

router = APIRouter(
    prefix="/workflow-publications", tags=["admin-workflow-publications"]
)

ReviewAction = Literal["approve", "reject", "unpublish"]


class ReviewBody(BaseModel):
    action: ReviewAction
    note: str | None = Field(None, max_length=500)
    category: str | None = Field(None, max_length=32)


class CategoryBody(BaseModel):
    category: str = Field(..., min_length=1, max_length=32)


@router.get("")
async def admin_list_publications(
    reviewStatus: str | None = Query(None, alias="reviewStatus"),
    displayStatus: str | None = Query(None, alias="displayStatus"),
    category: str | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100, alias="pageSize"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """工作流发布分页列表。"""
    data = await pub_svc.admin_list_publications(
        db,
        review_status=reviewStatus,
        display_status=displayStatus,
        category=category,
        q=q,
        page=page,
        page_size=pageSize,
    )
    return ok(data)


@router.patch("/{publication_id}/category")
async def admin_patch_publication_category(
    publication_id: str,
    body: CategoryBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """修改工作流发布分类。"""
    content = await pub_svc.admin_update_publication_category(
        db, publication_id, body.category
    )
    await db.commit()
    return ok(content)


@router.post("/{publication_id}/review")
async def admin_review_publication(
    publication_id: str,
    body: ReviewBody,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """审核工作流发布：通过 / 驳回 / 下架。"""
    content = await pub_svc.admin_review_publication(
        db,
        admin,
        publication_id,
        action=body.action,
        note=body.note,
        category=body.category,
    )
    await db.commit()
    return ok(content)
