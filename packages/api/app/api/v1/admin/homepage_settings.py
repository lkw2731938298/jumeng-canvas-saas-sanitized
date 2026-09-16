"""Admin homepage / content management settings."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.deps import require_permission
from ....core.admin_permissions import PERM_HOMEPAGE
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....services.cache import check_rate_limit
from ....services.homepage_background import upload_auth_grid_image, upload_login_modal_image
from ....services.platform_settings import (
    add_auth_grid_image,
    clear_auth_grid_images,
    clear_legal_document,
    clear_login_modal_image,
    get_homepage_settings,
    recompress_auth_grid_images,
    remove_auth_grid_image,
    set_legal_document,
    set_login_modal_image,
)

router = APIRouter()


class AuthGridImageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    oss_key: str = Field(..., alias="ossKey")
    image_url: str = Field(..., alias="imageUrl")
    sort_order: int = Field(..., alias="sortOrder")


class LegalDocumentMetaOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    configured: bool = False
    title: str = ""
    source_format: str = Field("", alias="sourceFormat")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")


class LegalDocumentsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_agreement: LegalDocumentMetaOut = Field(
        default_factory=LegalDocumentMetaOut, alias="userAgreement"
    )
    privacy_policy: LegalDocumentMetaOut = Field(
        default_factory=LegalDocumentMetaOut, alias="privacyPolicy"
    )


class AdminHomepageSettingsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    auth_grid_images: list[AuthGridImageOut] = Field(default_factory=list, alias="authGridImages")
    login_modal_oss_key: str = Field("", alias="loginModalOssKey")
    login_modal_image_url: str = Field("", alias="loginModalImageUrl")
    legal_documents: LegalDocumentsOut = Field(
        default_factory=LegalDocumentsOut, alias="legalDocuments"
    )
    updated_at: datetime = Field(..., alias="updatedAt")


class AuthGridImageUploadOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item: AuthGridImageOut
    auth_grid_images: list[AuthGridImageOut] = Field(..., alias="authGridImages")


def _legal_meta_out(raw: dict | None) -> LegalDocumentMetaOut:
    data = raw if isinstance(raw, dict) else {}
    updated = data.get("updatedAt")
    parsed_updated: datetime | None = None
    if isinstance(updated, datetime):
        parsed_updated = updated
    elif isinstance(updated, str) and updated.strip():
        try:
            parsed_updated = datetime.fromisoformat(updated.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed_updated = None
    return LegalDocumentMetaOut(
        configured=bool(data.get("configured")),
        title=str(data.get("title") or ""),
        sourceFormat=str(data.get("sourceFormat") or ""),
        updatedAt=parsed_updated,
    )


def _to_out(data: dict) -> AdminHomepageSettingsOut:
    legal_raw = data.get("legal_documents") or {}
    return AdminHomepageSettingsOut(
        authGridImages=[
            AuthGridImageOut(
                id=item["id"],
                ossKey=item["ossKey"],
                imageUrl=item["imageUrl"],
                sortOrder=item["sortOrder"],
            )
            for item in data.get("auth_grid_images") or []
        ],
        updatedAt=data["updated_at"],
        loginModalOssKey=str(data.get("login_modal_oss_key") or ""),
        loginModalImageUrl=str(data.get("login_modal_image_url") or ""),
        legalDocuments=LegalDocumentsOut(
            userAgreement=_legal_meta_out(legal_raw.get("userAgreement")),
            privacyPolicy=_legal_meta_out(legal_raw.get("privacyPolicy")),
        ),
    )


@router.get("/content/homepage", response_model=AdminHomepageSettingsOut)
async def get_admin_homepage_settings(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    data = await get_homepage_settings(db)
    return _to_out(data)


@router.post("/content/homepage/auth-grid-image", response_model=AuthGridImageUploadOut)
async def upload_admin_auth_grid_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    allowed = await check_rate_limit(
        f"admin:auth-grid-image:{current_user.id}",
        limit=30,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        uploaded = upload_auth_grid_image(file.filename or "grid.jpg", raw, file.content_type)
        data = await add_auth_grid_image(
            db,
            image_id=uploaded["id"],
            oss_key=uploaded["ossKey"],
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    await db.commit()
    out = _to_out(data)
    item = next((img for img in out.auth_grid_images if img.id == uploaded["id"]), None)
    if not item:
        fail(ErrorCode.INTERNAL_ERROR, message="上传成功但读取配置失败")
    return AuthGridImageUploadOut(item=item, authGridImages=out.auth_grid_images)


@router.delete("/content/homepage/auth-grid-images/{image_id}", response_model=AdminHomepageSettingsOut)
async def delete_admin_auth_grid_image(
    image_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    data = await remove_auth_grid_image(db, image_id)
    await db.commit()
    return _to_out(data)


@router.post("/content/homepage/auth-grid-images/clear", response_model=AdminHomepageSettingsOut)
async def clear_admin_auth_grid_images(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    data = await clear_auth_grid_images(db)
    await db.commit()
    return _to_out(data)


class AuthGridRecompressOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    auth_grid_images: list[AuthGridImageOut] = Field(..., alias="authGridImages")
    updated_at: datetime = Field(..., alias="updatedAt")
    rewritten: int = 0
    skipped: int = 0
    failed: int = 0
    total: int = 0


@router.post("/content/homepage/auth-grid-images/recompress", response_model=AuthGridRecompressOut)
async def recompress_admin_auth_grid_images(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """将已上传的登录背景大图批量重压为长边 ≤480 的 WebP。"""
    allowed = await check_rate_limit(
        f"admin:auth-grid-recompress:{current_user.id}",
        limit=3,
        window_s=300,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    data = await recompress_auth_grid_images(db)
    await db.commit()
    stats = data.get("recompress_stats") or {}
    out = _to_out(data)
    return AuthGridRecompressOut(
        authGridImages=out.auth_grid_images,
        updatedAt=out.updated_at,
        rewritten=int(stats.get("rewritten") or 0),
        skipped=int(stats.get("skipped") or 0),
        failed=int(stats.get("failed") or 0),
        total=int(stats.get("total") or 0),
    )


@router.post("/content/homepage/login-modal-image", response_model=AdminHomepageSettingsOut)
async def upload_admin_login_modal_image(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """上传登录弹窗左半边图片（覆盖上一张）。"""
    allowed = await check_rate_limit(
        f"admin:login-modal-image:{current_user.id}",
        limit=20,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        uploaded = upload_login_modal_image(file.filename or "login-modal.jpg", raw, file.content_type)
        data = await set_login_modal_image(db, uploaded["ossKey"])
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    await db.commit()
    return _to_out(data)


@router.delete("/content/homepage/login-modal-image", response_model=AdminHomepageSettingsOut)
async def delete_admin_login_modal_image(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """清空登录弹窗左侧图。"""
    data = await clear_login_modal_image(db)
    await db.commit()
    return _to_out(data)


@router.post("/content/homepage/legal/{doc_type}", response_model=AdminHomepageSettingsOut)
async def upload_admin_legal_document(
    doc_type: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """上传用户协议或隐私政策（.md / .docx）。"""
    allowed = await check_rate_limit(
        f"admin:legal-doc:{current_user.id}",
        limit=20,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        data = await set_legal_document(
            db,
            doc_type,
            filename=file.filename or "document.md",
            data=raw,
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))

    await db.commit()
    return _to_out(data)


@router.delete("/content/homepage/legal/{doc_type}", response_model=AdminHomepageSettingsOut)
async def delete_admin_legal_document(
    doc_type: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """清除已配置的用户协议或隐私政策。"""
    try:
        data = await clear_legal_document(db, doc_type)
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    await db.commit()
    return _to_out(data)
