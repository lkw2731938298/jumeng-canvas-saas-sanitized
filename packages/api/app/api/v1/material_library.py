"""用户侧 — 平台素材库只读列表（不可上传）。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...models.database import get_db
from ...models.user import User
from ...services import material_library as ml

router = APIRouter()

_CATEGORY_HINT = "style / effect / character / prompt"


class PromptLibraryCategoryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    sort_order: int = Field(0, alias="sortOrder")


class MaterialLibraryItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    category: str
    title: str
    media_type: str = Field(..., alias="mediaType")
    oss_key: str = Field(..., alias="ossKey")
    media_url: str = Field(..., alias="mediaUrl")
    prompt_text: str = Field("", alias="promptText")
    prompt_category_id: Optional[str] = Field(None, alias="promptCategoryId")
    prompt_category_name: str = Field("", alias="promptCategoryName")
    sort_order: int = Field(0, alias="sortOrder")


class MaterialLibraryListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[MaterialLibraryItemOut]
    prompt_categories: list[PromptLibraryCategoryOut] = Field(
        default_factory=list, alias="promptCategories"
    )


def _cat_out(data: dict[str, Any]) -> PromptLibraryCategoryOut:
    return PromptLibraryCategoryOut(
        id=data["id"],
        name=data["name"],
        sortOrder=data["sortOrder"],
    )


def _to_out(data: dict[str, Any]) -> MaterialLibraryItemOut:
    return MaterialLibraryItemOut(
        id=data["id"],
        category=data["category"],
        title=data["title"],
        mediaType=data["mediaType"],
        ossKey=data["ossKey"],
        mediaUrl=data["mediaUrl"],
        promptText=data.get("promptText") or "",
        promptCategoryId=data.get("promptCategoryId"),
        promptCategoryName=data.get("promptCategoryName") or "",
        sortOrder=data["sortOrder"],
    )


@router.get("", response_model=MaterialLibraryListOut)
async def list_material_library(
    category: Optional[str] = Query(None, description=_CATEGORY_HINT),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """登录用户只读浏览素材库（仅上架项）；上传仅限管理端。"""
    cat = None
    if category is not None:
        cat = ml.normalize_category(category)
        if not cat:
            fail(ErrorCode.VALIDATION_ERROR, message=f"类别须为 {_CATEGORY_HINT}")
    items = await ml.list_items(db, category=cat, include_inactive=False)
    prompt_cats: list[PromptLibraryCategoryOut] = []
    if cat == "prompt" or cat is None:
        prompt_cats = [
            _cat_out(i)
            for i in await ml.list_prompt_categories(db, include_inactive=False)
        ]
    return MaterialLibraryListOut(
        items=[_to_out(i) for i in items],
        promptCategories=prompt_cats,
    )
