"""管理端 — 首页 Footer 内容配置（关于我们 / 联系我们二维码 / 社交链接）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.admin_permissions import PERM_HOMEPAGE
from ....core.deps import require_permission
from ....core.error_codes import ErrorCode
from ....core.errors import fail
from ....models.database import get_db
from ....models.user import User
from ....services.cache import check_rate_limit
from ....services.discover_media import upload_footer_image
from ....services.site_footer import get_site_footer_settings, set_site_footer_settings

router = APIRouter()


class AboutUsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str = Field("关于我们", min_length=1, max_length=64)
    href: str = Field("", max_length=1024)


class ContactQrCodeIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    label: str = Field("", max_length=64)
    image_url: str = Field(..., alias="imageUrl", min_length=1, max_length=1024)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)


class ContactUsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str = Field("联系我们", min_length=1, max_length=64)
    qr_codes: list[ContactQrCodeIn] = Field(default_factory=list, alias="qrCodes")


class SocialLinkIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=64)
    href: str = Field("", max_length=1024)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)


class FriendLinkIn(BaseModel):
    """发现页底部友情链接。"""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=64)
    href: str = Field(..., min_length=1, max_length=1024)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)


class SiteFooterUpdateIn(BaseModel):
    """整表保存首页 Footer 配置。"""

    model_config = ConfigDict(populate_by_name=True)

    brand_text: str = Field("聚梦-专业视频创作平台", alias="brandText", max_length=128)
    copyright: str = Field("© 2026 JUMENG AI", max_length=128)
    tagline: str = Field("为每一个未被看见的想象而生", max_length=128)
    help_url: str = Field("", alias="helpUrl", max_length=1024)
    about_us: AboutUsIn = Field(..., alias="aboutUs")
    contact_us: ContactUsIn = Field(..., alias="contactUs")
    social_links: list[SocialLinkIn] = Field(default_factory=list, alias="socialLinks")
    friend_links: list[FriendLinkIn] = Field(default_factory=list, alias="friendLinks")
    friend_links_label: str = Field("友情链接", alias="friendLinksLabel", max_length=32)
    icp_number: str = Field("", alias="icpNumber", max_length=64)
    icp_href: str = Field("https://beian.miit.gov.cn/", alias="icpHref", max_length=1024)


class SiteFooterOut(BaseModel):
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


class FooterImageUploadOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    oss_key: str = Field(..., alias="ossKey")
    image_url: str = Field(..., alias="imageUrl")


def _to_out(data: dict[str, Any]) -> SiteFooterOut:
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


@router.post("/content/footer/image", response_model=FooterImageUploadOut)
async def upload_admin_footer_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """上传 Footer 二维码等到平台 OSS；保存配置时再绑定 URL。"""
    allowed = await check_rate_limit(
        f"admin:footer-image:{current_user.id}",
        limit=30,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        uploaded = upload_footer_image(
            file.filename or "footer.jpg",
            raw,
            file.content_type,
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    return FooterImageUploadOut(**uploaded)


@router.get("/content/footer", response_model=SiteFooterOut)
async def get_admin_site_footer(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """读取首页 Footer 运营配置（空库时返回内置默认）。"""
    data = await get_site_footer_settings(db)
    return _to_out(data)


@router.put("/content/footer", response_model=SiteFooterOut)
async def put_admin_site_footer(
    body: SiteFooterUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """整表保存首页 Footer 运营配置。"""
    raw = {
        "brandText": body.brand_text,
        "copyright": body.copyright,
        "tagline": body.tagline,
        "helpUrl": body.help_url,
        "aboutUs": body.about_us.model_dump(by_alias=True),
        "contactUs": body.contact_us.model_dump(by_alias=True),
        "socialLinks": [item.model_dump(by_alias=True) for item in body.social_links],
        "friendLinks": [item.model_dump(by_alias=True) for item in body.friend_links],
        "friendLinksLabel": body.friend_links_label,
        "icpNumber": body.icp_number,
        "icpHref": body.icp_href,
    }
    data = await set_site_footer_settings(db, raw)
    return _to_out(data)
