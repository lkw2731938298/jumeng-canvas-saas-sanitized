"""管理端 Skill 列表与发布审核。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_HOMEPAGE
from ....core.deps import require_permission
from ....core.error_codes import ErrorCode
from ....core.errors import fail, ok
from ....models.database import get_db
from ....models.skill import Skill
from ....models.user import User
from ....services import skills_catalog
from ....services.platform_settings import get_skill_categories, set_skill_categories
from ....services.skill_docs import load_skill_doc_resolved

router = APIRouter(prefix="/skills", tags=["admin-skills"])

ReviewAction = Literal["approve", "reject", "unpublish"]


class ReviewBody(BaseModel):
    action: ReviewAction
    note: str | None = Field(None, max_length=500)
    # 通过时可一并指定分类
    category: str | None = Field(None, max_length=64)


class CategoryBody(BaseModel):
    category: str = Field(..., min_length=1, max_length=64)


class MetaBody(BaseModel):
    """管理端改名称 / 封面 / 分类。"""

    title: str | None = Field(None, max_length=64)
    cover_url: str | None = Field(None, alias="coverUrl", max_length=1024)
    clear_cover: bool = Field(False, alias="clearCover")
    category: str | None = Field(None, max_length=64)


class CategoriesConfigBody(BaseModel):
    categories: list[str] = Field(default_factory=list)


@router.get("")
async def admin_list_skills(
    reviewStatus: str | None = Query(None, alias="reviewStatus"),
    displayStatus: str | None = Query(None, alias="displayStatus"),
    visibility: str | None = Query(None),
    category: str | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100, alias="pageSize"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """Skill 分页列表（含社区待审与展示态筛选）。"""
    data = await skills_catalog.admin_list_skills(
        db,
        review_status=reviewStatus,
        display_status=displayStatus,
        visibility=visibility,
        category=category,
        q=q,
        page=page,
        page_size=pageSize,
    )
    return ok(data)


@router.get("/categories")
async def admin_get_skill_categories(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """读取 Skill 分类配置。"""
    cats = await get_skill_categories(db)
    return ok({"categories": cats})


@router.put("/categories")
async def admin_put_skill_categories(
    body: CategoriesConfigBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """保存 Skill 分类配置。"""
    cats = await set_skill_categories(db, body.categories)
    await db.commit()
    return ok({"categories": cats})


@router.get("/{slug}/doc")
async def admin_get_skill_doc(
    slug: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """预览 SKILL.md（管理端，不强制用户可见性）。"""
    row = (
        await db.execute(select(Skill).where(Skill.slug == slug).limit(1))
    ).scalar_one_or_none()
    if row is None:
        fail(ErrorCode.SKILL_NOT_FOUND)
    doc = await load_skill_doc_resolved(db, slug, skill_row=row)
    md = ""
    if isinstance(doc, dict):
        md = str(doc.get("raw") or doc.get("markdown") or "").strip()
    if not md:
        md = str(getattr(row, "doc_markdown", None) or "").strip()
    if not md:
        md = f"# {row.title}\n\n{row.description or ''}\n"
    return ok(
        {
            "slug": slug,
            "title": row.title,
            "markdown": md,
            "reviewStatus": str(getattr(row, "review_status", None) or "none"),
            "visibility": row.visibility,
            "displayStatus": skills_catalog.skill_display_status(row),
            "category": row.category,
        }
    )


@router.patch("/{slug}")
async def admin_patch_skill_meta(
    slug: str,
    body: MetaBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """修改 Skill 名称 / 封面 / 分类（管理员可改平台种子，不回待审）。"""
    if (
        body.title is None
        and body.cover_url is None
        and not body.clear_cover
        and body.category is None
    ):
        fail(ErrorCode.VALIDATION_ERROR, message="请至少提供名称、封面或分类之一")
    content = await skills_catalog.admin_update_skill_meta(
        db,
        slug,
        title=body.title,
        cover_url=body.cover_url,
        clear_cover=bool(body.clear_cover),
        category=body.category,
    )
    await db.commit()
    return ok(content)


@router.patch("/{slug}/category")
async def admin_patch_skill_category(
    slug: str,
    body: CategoryBody,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """修改单条 Skill 分类。"""
    content = await skills_catalog.admin_update_skill_category(db, slug, body.category)
    await db.commit()
    return ok(content)


@router.post("/{slug}/review")
async def admin_review_skill(
    slug: str,
    body: ReviewBody,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission(PERM_HOMEPAGE)),
) -> dict[str, Any]:
    """审核 Skill：通过 / 驳回 / 下架。"""
    content = await skills_catalog.admin_review_skill(
        db,
        admin,
        slug,
        action=body.action,
        note=body.note,
        category=body.category,
    )
    await db.commit()
    return ok(content)
