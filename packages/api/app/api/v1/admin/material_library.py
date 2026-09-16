"""管理端 — 平台素材库（风格 / 特效 / 角色 / 提示词）CRUD 与上传。"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_MATERIAL_LIBRARY
from ....core.deps import require_permission
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.material_library import (
    PROMPT_LIBRARY_CATEGORY_NAME_MAX_LEN,
    PROMPT_LIBRARY_TEXT_MAX_LEN,
)
from ....models.user import User
from ....services import material_library as ml
from ....services.cache import check_rate_limit

router = APIRouter()

_CATEGORY_HINT = "style / effect / character / prompt"


class PromptLibraryCategoryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    sort_order: int = Field(0, alias="sortOrder")
    is_active: bool = Field(True, alias="isActive")
    created_at: Optional[str] = Field(None, alias="createdAt")
    updated_at: Optional[str] = Field(None, alias="updatedAt")


class PromptLibraryCategoryListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[PromptLibraryCategoryOut]


class PromptLibraryCategoryCreateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=PROMPT_LIBRARY_CATEGORY_NAME_MAX_LEN)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=99999)


class PromptLibraryCategoryUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Optional[str] = Field(None, min_length=1, max_length=PROMPT_LIBRARY_CATEGORY_NAME_MAX_LEN)
    sort_order: Optional[int] = Field(None, alias="sortOrder", ge=0, le=99999)
    is_active: Optional[bool] = Field(None, alias="isActive")


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
    is_active: bool = Field(True, alias="isActive")
    created_by: Optional[str] = Field(None, alias="createdBy")
    created_at: Optional[str] = Field(None, alias="createdAt")
    updated_at: Optional[str] = Field(None, alias="updatedAt")


class MaterialLibraryListOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: list[MaterialLibraryItemOut]
    prompt_categories: list[PromptLibraryCategoryOut] = Field(
        default_factory=list, alias="promptCategories"
    )


class MaterialLibraryUpdateIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = Field(None, min_length=1, max_length=128)
    sort_order: Optional[int] = Field(None, alias="sortOrder", ge=0, le=99999)
    is_active: Optional[bool] = Field(None, alias="isActive")
    prompt_text: Optional[str] = Field(None, alias="promptText", max_length=PROMPT_LIBRARY_TEXT_MAX_LEN)
    # 空字符串表示改为未分类；省略则不改
    prompt_category_id: Optional[str] = Field(None, alias="promptCategoryId")


def _cat_out(data: dict[str, Any]) -> PromptLibraryCategoryOut:
    return PromptLibraryCategoryOut(
        id=data["id"],
        name=data["name"],
        sortOrder=data["sortOrder"],
        isActive=data["isActive"],
        createdAt=data.get("createdAt"),
        updatedAt=data.get("updatedAt"),
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
        isActive=data["isActive"],
        createdBy=data.get("createdBy"),
        createdAt=data.get("createdAt"),
        updatedAt=data.get("updatedAt"),
    )


@router.get(
    "/content/material-library/prompt-categories",
    response_model=PromptLibraryCategoryListOut,
)
async def list_admin_prompt_library_categories(
    include_inactive: bool = Query(True, alias="includeInactive"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """管理端列出提示词库分类（默认可含下架）。"""
    items = await ml.list_prompt_categories(db, include_inactive=include_inactive)
    return PromptLibraryCategoryListOut(items=[_cat_out(i) for i in items])


@router.post(
    "/content/material-library/prompt-categories",
    response_model=PromptLibraryCategoryOut,
)
async def create_admin_prompt_library_category(
    body: PromptLibraryCategoryCreateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """新建提示词库分类。"""
    try:
        row = await ml.create_prompt_category(db, name=body.name, sort_order=body.sort_order)
    except ValueError as exc:
        fail(ErrorCode.CONFLICT if "已存在" in str(exc) else ErrorCode.VALIDATION_ERROR, message=str(exc))
    return _cat_out(ml.prompt_category_to_dict(row))


@router.patch(
    "/content/material-library/prompt-categories/{category_id}",
    response_model=PromptLibraryCategoryOut,
)
async def update_admin_prompt_library_category(
    category_id: int,
    body: PromptLibraryCategoryUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """更新提示词库分类名称、排序或上下架。"""
    row = await ml.get_prompt_category(db, category_id)
    if not row:
        fail(ErrorCode.NOT_FOUND, message="分类不存在")
    try:
        row = await ml.update_prompt_category(
            db,
            row,
            name=body.name,
            sort_order=body.sort_order,
            is_active=body.is_active,
        )
    except ValueError as exc:
        fail(ErrorCode.CONFLICT if "已存在" in str(exc) else ErrorCode.VALIDATION_ERROR, message=str(exc))
    return _cat_out(ml.prompt_category_to_dict(row))


@router.delete("/content/material-library/prompt-categories/{category_id}")
async def delete_admin_prompt_library_category(
    category_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """删除分类；其下提示词变为未分类。"""
    row = await ml.get_prompt_category(db, category_id)
    if not row:
        fail(ErrorCode.NOT_FOUND, message="分类不存在")
    await ml.delete_prompt_category(db, row)
    return {"deleted": True}


@router.get("/content/material-library", response_model=MaterialLibraryListOut)
async def list_admin_material_library(
    category: Optional[str] = Query(None),
    include_inactive: bool = Query(False, alias="includeInactive"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """管理端列出素材库条目（可含下架项）。"""
    cat = None
    if category is not None:
        cat = ml.normalize_category(category)
        if not cat:
            fail(ErrorCode.VALIDATION_ERROR, message=f"类别须为 {_CATEGORY_HINT}")
    items = await ml.list_items(db, category=cat, include_inactive=include_inactive)
    prompt_cats: list[PromptLibraryCategoryOut] = []
    if cat == "prompt" or cat is None:
        prompt_cats = [
            _cat_out(i)
            for i in await ml.list_prompt_categories(db, include_inactive=True)
        ]
    return MaterialLibraryListOut(
        items=[_to_out(i) for i in items],
        promptCategories=prompt_cats,
    )


@router.post("/content/material-library", response_model=MaterialLibraryItemOut)
async def create_admin_material_library_item(
    category: str = Form(...),
    title: str = Form(...),
    sort_order: int = Form(0),
    prompt_text: str = Form(""),
    prompt_category_id: str = Form(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """管理员上传素材并创建库条目（用户不可调用）。提示词库须附带提示词正文。"""
    cat = ml.normalize_category(category)
    if not cat:
        fail(ErrorCode.VALIDATION_ERROR, message=f"类别须为 {_CATEGORY_HINT}")
    title_clean = (title or "").strip()
    if not title_clean:
        fail(ErrorCode.VALIDATION_ERROR, message="标题不能为空")

    prompt_clean = ml.normalize_prompt_text(prompt_text)
    if cat == "prompt" and not prompt_clean:
        fail(ErrorCode.VALIDATION_ERROR, message="提示词库须填写提示词正文")

    cat_id: int | None = None
    if cat == "prompt":
        try:
            cat_id = await ml.resolve_prompt_category_id(db, prompt_category_id)
        except ValueError as exc:
            fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    allowed = await check_rate_limit(
        f"admin:material-library:{current_user.id}",
        limit=30,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        uploaded = ml.upload_material_media(
            cat,
            file.filename or "material.bin",
            raw,
            file.content_type,
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    row = await ml.create_item(
        db,
        category=cat,
        title=title_clean,
        oss_key=uploaded["ossKey"],
        media_type=uploaded["mediaType"],
        created_by=int(current_user.id),
        sort_order=sort_order,
        prompt_text=prompt_clean,
        prompt_category_id=cat_id,
    )
    return _to_out(await ml.item_to_public_dict(db, row))


@router.patch("/content/material-library/{item_id}", response_model=MaterialLibraryItemOut)
async def update_admin_material_library_item(
    item_id: int,
    body: MaterialLibraryUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """更新标题、排序、上下架、提示词正文或所属分类。"""
    row = await ml.get_item(db, item_id)
    if not row:
        fail(ErrorCode.NOT_FOUND, message="素材不存在")
    if body.prompt_text is not None and row.category == "prompt":
        if not ml.normalize_prompt_text(body.prompt_text):
            fail(ErrorCode.VALIDATION_ERROR, message="提示词正文不能为空")
    dumped = body.model_dump(exclude_unset=True)
    clear_cat = False
    next_cat_id: int | None = None
    if "prompt_category_id" in dumped:
        raw = dumped.get("prompt_category_id")
        if raw is None or str(raw).strip() == "":
            clear_cat = True
        else:
            try:
                next_cat_id = await ml.resolve_prompt_category_id(db, raw)
            except ValueError as exc:
                fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
            if next_cat_id is None:
                clear_cat = True
    row = await ml.update_item(
        db,
        row,
        title=body.title,
        sort_order=body.sort_order,
        is_active=body.is_active,
        prompt_text=body.prompt_text,
        prompt_category_id=next_cat_id,
        clear_prompt_category=clear_cat,
    )
    return _to_out(await ml.item_to_public_dict(db, row))


@router.delete("/content/material-library/{item_id}")
async def delete_admin_material_library_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_MATERIAL_LIBRARY)),
):
    """删除素材库条目元数据。"""
    row = await ml.get_item(db, item_id)
    if not row:
        fail(ErrorCode.NOT_FOUND, message="素材不存在")
    await ml.delete_item(db, row)
    return {"deleted": True}
