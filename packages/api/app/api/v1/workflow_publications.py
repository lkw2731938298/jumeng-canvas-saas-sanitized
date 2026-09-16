"""工作流发布 API：发布、列表、详情、点赞、只读预览、全量复制。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user, get_current_user_optional
from ...models.database import get_db
from ...models.user import User
from ...services import workflow_publications as pub_svc

router = APIRouter()

ScopeKey = Literal["public", "mine", "used"]
# 广场排序：最新=发布时间/更新时间；最热=点赞量
SortKey = Literal["latest", "hot"]


class PublishIn(BaseModel):
    projectId: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=20)
    description: str = Field(..., min_length=1, max_length=100)
    category: str = Field(..., min_length=1, max_length=32)
    videoAssetId: str = Field(..., min_length=1)
    coverAssetId: str = Field(..., min_length=1)
    isPublic: bool = False


@router.post("")
async def publish_workflow(
    body: PublishIn,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """发布或更新工作流到作品广场。"""
    data = await pub_svc.upsert_publication(
        db,
        current_user,
        project_id=body.projectId,
        title=body.title,
        description=body.description,
        category=body.category,
        video_asset_id=body.videoAssetId,
        cover_asset_id=body.coverAssetId,
        is_public=body.isPublic,
    )
    await db.commit()
    return data


@router.delete("/{publication_id}")
async def delete_workflow_publication(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """作者删除自己的发布（从「我的发布」移除，可重新发布同项目）。"""
    data = await pub_svc.delete_my_publication(db, current_user, publication_id)
    await db.commit()
    return data


@router.get("")
async def list_workflow_publications(
    scope: ScopeKey = Query(default="public"),
    category: str | None = Query(default=None),
    sort: SortKey = Query(default="latest"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """列出发布条目：public / mine / used；sort=latest|hot。"""
    return await pub_svc.list_publications(
        db,
        current_user,
        scope=scope,
        category=category,
        sort=sort,
        page=page,
        page_size=pageSize,
    )


@router.get("/{publication_id}")
async def get_workflow_publication(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """发布详情。"""
    return await pub_svc.get_publication_detail(db, current_user, publication_id)


@router.post("/{publication_id}/like")
async def like_workflow_publication(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """点赞切换。"""
    data = await pub_svc.toggle_publication_like(db, current_user, publication_id)
    await db.commit()
    return data


@router.get("/{publication_id}/preview")
async def preview_workflow_publication(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """只读预览：节点布局与素材 URL。"""
    return await pub_svc.get_publication_preview(db, current_user, publication_id)


@router.post("/{publication_id}/copy")
async def copy_workflow_publication(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """将公开（或本人）工作流完整复制为新项目。"""
    data = await pub_svc.copy_publication_to_new_project(db, current_user, publication_id)
    await db.commit()
    return data
