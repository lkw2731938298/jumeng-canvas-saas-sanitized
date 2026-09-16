"""Public site configuration (no auth)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.database import get_db
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...services.credit_activities import list_showcase_activities
from ...services.discover_page import get_discover_page_settings
from ...services.platform_settings import get_homepage_settings, get_legal_document_public
from ...services.site_footer import get_site_footer_settings

router = APIRouter()


class SiteHomepageOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    auth_grid_image_urls: list[str] = Field(default_factory=list, alias="authGridImageUrls")
    login_modal_image_url: str = Field("", alias="loginModalImageUrl")


class SiteLegalDocumentOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    doc_type: str = Field(..., alias="docType")
    title: str = ""
    markdown: str = ""
    configured: bool = False
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")


@router.get("/legal/{doc_type}", response_model=SiteLegalDocumentOut)
async def get_site_legal_document(doc_type: str, db: AsyncSession = Depends(get_db)):
    """公开读取用户协议或隐私政策正文（登录弹窗展示）。"""
    try:
        data = await get_legal_document_public(db, doc_type)
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    updated = data.get("updatedAt")
    parsed_updated: datetime | None = None
    if isinstance(updated, datetime):
        parsed_updated = updated
    elif isinstance(updated, str) and updated.strip():
        try:
            parsed_updated = datetime.fromisoformat(updated.strip().replace("Z", "+00:00"))
        except ValueError:
            parsed_updated = None
    return SiteLegalDocumentOut(
        docType=str(data.get("docType") or ""),
        title=str(data.get("title") or ""),
        markdown=str(data.get("markdown") or ""),
        configured=bool(data.get("configured")),
        updatedAt=parsed_updated,
    )


@router.get("/homepage", response_model=SiteHomepageOut)
async def get_site_homepage(db: AsyncSession = Depends(get_db)):
    data = await get_homepage_settings(db)
    urls = [
        str(item.get("imageUrl") or "").strip()
        for item in data.get("auth_grid_images") or []
        if str(item.get("imageUrl") or "").strip()
    ]
    return SiteHomepageOut(
        authGridImageUrls=urls,
        loginModalImageUrl=str(data.get("login_modal_image_url") or "").strip(),
    )


class SiteDiscoverOut(BaseModel):
    """公开发现页运营配置（登录后前端读取）。"""

    model_config = ConfigDict(populate_by_name=True)

    hero_title: str = Field(..., alias="heroTitle")
    hero_em: str = Field(..., alias="heroEm")
    creation_placeholder: str = Field(..., alias="creationPlaceholder")
    hero_videos: list[dict[str, Any]] = Field(default_factory=list, alias="heroVideos")
    prompt_suggestions: list[dict[str, Any]] = Field(default_factory=list, alias="promptSuggestions")
    story_feature: dict[str, Any] = Field(..., alias="storyFeature")
    highlight_features: list[dict[str, Any]] = Field(default_factory=list, alias="highlightFeatures")
    skill_machine: dict[str, Any] = Field(..., alias="skillMachine")
    gallery_filters: list[str] = Field(default_factory=list, alias="galleryFilters")
    gallery_items: list[dict[str, Any]] = Field(default_factory=list, alias="galleryItems")
    corner_popup: dict[str, Any] = Field(default_factory=dict, alias="cornerPopup")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")


@router.get("/discover", response_model=SiteDiscoverOut)
async def get_site_discover(db: AsyncSession = Depends(get_db)):
    """发现页公开配置；无后台配置时返回内置默认。"""
    data = await get_discover_page_settings(db)
    # 仅返回已上架作品
    items = [item for item in (data.get("galleryItems") or []) if item.get("isActive", True)]
    return SiteDiscoverOut(
        heroTitle=data["heroTitle"],
        heroEm=data["heroEm"],
        creationPlaceholder=data["creationPlaceholder"],
        heroVideos=data.get("heroVideos") or [],
        promptSuggestions=data.get("promptSuggestions") or [],
        storyFeature=data["storyFeature"],
        highlightFeatures=data.get("highlightFeatures") or [],
        skillMachine=data["skillMachine"],
        galleryFilters=data.get("galleryFilters") or [],
        galleryItems=items,
        cornerPopup=data.get("cornerPopup") or {},
        updatedAt=data.get("updatedAt"),
    )


class SiteActivityListOut(BaseModel):
    """活动页公开展示内容，不含用户领取状态。"""

    items: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/activities", response_model=SiteActivityListOut)
async def get_site_activities(db: AsyncSession = Depends(get_db)):
    """返回后台已上线或已结束的算力活动，草稿不公开。"""
    return SiteActivityListOut(items=await list_showcase_activities(db))


class SiteFooterOut(BaseModel):
    """公开首页 Footer 运营配置（关于我们 / 联系我们二维码 / 社交链接 / 友情链接 / 备案 / 顶栏教程）。"""

    model_config = ConfigDict(populate_by_name=True)

    brand_text: str = Field(..., alias="brandText")
    copyright: str
    tagline: str
    help_url: str = Field("", alias="helpUrl")
    about_us: dict[str, Any] = Field(..., alias="aboutUs")
    contact_us: dict[str, Any] = Field(..., alias="contactUs")
    social_links: list[dict[str, Any]] = Field(default_factory=list, alias="socialLinks")
    friend_links: list[dict[str, Any]] = Field(default_factory=list, alias="friendLinks")
    friend_links_label: str = Field("友情链接", alias="friendLinksLabel")
    icp_number: str = Field("", alias="icpNumber")
    icp_href: str = Field("", alias="icpHref")
    updated_at: Optional[datetime] = Field(None, alias="updatedAt")


@router.get("/footer", response_model=SiteFooterOut)
async def get_site_footer(db: AsyncSession = Depends(get_db)):
    """Footer 公开配置；无后台配置时返回内置默认。"""
    data = await get_site_footer_settings(db)
    return SiteFooterOut(
        brandText=data["brandText"],
        copyright=data["copyright"],
        tagline=data["tagline"],
        helpUrl=str(data.get("helpUrl") or ""),
        aboutUs=data["aboutUs"],
        contactUs=data["contactUs"],
        socialLinks=data.get("socialLinks") or [],
        friendLinks=data.get("friendLinks") or [],
        friendLinksLabel=str(data.get("friendLinksLabel") or "友情链接"),
        icpNumber=str(data.get("icpNumber") or ""),
        icpHref=str(data.get("icpHref") or ""),
        updatedAt=data.get("updatedAt"),
    )
