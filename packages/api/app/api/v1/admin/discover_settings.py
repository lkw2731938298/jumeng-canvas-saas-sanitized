"""管理端 — 发现页（/discover）运营内容配置。"""

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
from ....services.discover_media import upload_discover_image, upload_discover_video
from ....services.discover_page import get_discover_page_settings, set_discover_page_settings

router = APIRouter()


class HeroVideoIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    url: str = Field(..., min_length=1)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)


class PromptSuggestionIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=64)
    image_url: str = Field("", alias="imageUrl", max_length=1024)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)


class StoryFeatureIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str = Field(..., min_length=1, max_length=64)
    image_url: str = Field("", alias="imageUrl", max_length=1024)
    href: str = Field("/skills", max_length=256)


class HighlightFeatureIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=64)
    # 迷你技能卡小文案（副标题），发现页标题下方展示
    subtitle: str = Field("", max_length=64)
    tone: str = Field("purple", max_length=32)
    is_new: bool = Field(False, alias="isNew")
    href: str = Field("/skills", max_length=256)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)


class SkillMachineIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    label: str = Field(..., min_length=1, max_length=64)
    href: str = Field("/skills", max_length=256)
    image_url: str = Field("", alias="imageUrl", max_length=1024)


class GalleryItemIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=128)
    author: str = Field("匿名", max_length=64)
    avatar_url: str = Field("", alias="avatarUrl", max_length=1024)
    caption: str = Field("", max_length=256)
    image_url: str = Field(..., alias="imageUrl", min_length=1, max_length=1024)
    category: str = Field("全部", max_length=64)
    scope: str = Field("templates", max_length=32)
    sort_order: int = Field(0, alias="sortOrder", ge=0, le=9999)
    is_active: bool = Field(True, alias="isActive")


class CornerPopupIn(BaseModel):
    """发现页右上角运营弹窗。"""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = False
    text: str = Field("", max_length=2000)
    image_url: str = Field("", alias="imageUrl", max_length=1024)
    image_fill: bool = Field(False, alias="imageFill")
    aspect_ratio: str = Field("16:9", alias="aspectRatio", max_length=8)
    show_when_logged_in: bool = Field(True, alias="showWhenLoggedIn")
    show_when_logged_out: bool = Field(True, alias="showWhenLoggedOut")


class DiscoverPageUpdateIn(BaseModel):
    """整表保存发现页配置。"""

    model_config = ConfigDict(populate_by_name=True)

    hero_title: str = Field(..., alias="heroTitle", min_length=1, max_length=64)
    hero_em: str = Field(..., alias="heroEm", min_length=1, max_length=64)
    creation_placeholder: str = Field(..., alias="creationPlaceholder", max_length=256)
    hero_videos: list[HeroVideoIn] = Field(default_factory=list, alias="heroVideos")
    prompt_suggestions: list[PromptSuggestionIn] = Field(default_factory=list, alias="promptSuggestions")
    story_feature: StoryFeatureIn = Field(..., alias="storyFeature")
    highlight_features: list[HighlightFeatureIn] = Field(default_factory=list, alias="highlightFeatures")
    skill_machine: SkillMachineIn = Field(..., alias="skillMachine")
    gallery_filters: list[str] = Field(default_factory=list, alias="galleryFilters")
    gallery_items: list[GalleryItemIn] = Field(default_factory=list, alias="galleryItems")
    corner_popup: CornerPopupIn = Field(default_factory=CornerPopupIn, alias="cornerPopup")


class DiscoverPageOut(BaseModel):
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


class DiscoverImageUploadOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    oss_key: str = Field(..., alias="ossKey")
    image_url: str = Field(..., alias="imageUrl")


class DiscoverVideoUploadOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    oss_key: str = Field(..., alias="ossKey")
    video_url: str = Field(..., alias="videoUrl")


def _to_out(data: dict[str, Any]) -> DiscoverPageOut:
    return DiscoverPageOut(
        heroTitle=data["heroTitle"],
        heroEm=data["heroEm"],
        creationPlaceholder=data["creationPlaceholder"],
        heroVideos=data.get("heroVideos") or [],
        promptSuggestions=data.get("promptSuggestions") or [],
        storyFeature=data["storyFeature"],
        highlightFeatures=data.get("highlightFeatures") or [],
        skillMachine=data["skillMachine"],
        galleryFilters=data.get("galleryFilters") or [],
        galleryItems=data.get("galleryItems") or [],
        cornerPopup=data.get("cornerPopup") or {},
        updatedAt=data.get("updatedAt"),
    )


@router.post("/content/discover/image", response_model=DiscoverImageUploadOut)
async def upload_admin_discover_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """上传发现页运营图片；文件仅写 OSS，保存配置时再绑定 URL。"""
    allowed = await check_rate_limit(
        f"admin:discover-image:{current_user.id}",
        limit=30,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        uploaded = upload_discover_image(
            file.filename or "discover.jpg",
            raw,
            file.content_type,
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    return DiscoverImageUploadOut(**uploaded)


@router.post("/content/discover/video", response_model=DiscoverVideoUploadOut)
async def upload_admin_discover_video(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """上传发现页 Hero 背景视频；文件写 OSS，配置保存时绑定 URL。"""
    allowed = await check_rate_limit(
        f"admin:discover-video:{current_user.id}",
        limit=10,
        window_s=60,
    )
    if not allowed:
        fail(ErrorCode.RATE_LIMITED)

    raw = await file.read()
    try:
        uploaded = upload_discover_video(
            file.filename or "discover.mp4",
            raw,
            file.content_type,
        )
    except ValueError as exc:
        fail(ErrorCode.VALIDATION_ERROR, message=str(exc))
    return DiscoverVideoUploadOut(**uploaded)


@router.get("/content/discover", response_model=DiscoverPageOut)
async def get_admin_discover_page(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """读取发现页运营配置（空库时返回内置默认）。"""
    data = await get_discover_page_settings(db)
    return _to_out(data)


@router.put("/content/discover", response_model=DiscoverPageOut)
async def put_admin_discover_page(
    body: DiscoverPageUpdateIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(PERM_HOMEPAGE)),
):
    """整表保存发现页运营配置。"""
    raw = {
        "heroTitle": body.hero_title,
        "heroEm": body.hero_em,
        "creationPlaceholder": body.creation_placeholder,
        "heroVideos": [item.model_dump(by_alias=True) for item in body.hero_videos],
        "promptSuggestions": [item.model_dump(by_alias=True) for item in body.prompt_suggestions],
        "storyFeature": body.story_feature.model_dump(by_alias=True),
        "highlightFeatures": [item.model_dump(by_alias=True) for item in body.highlight_features],
        "skillMachine": body.skill_machine.model_dump(by_alias=True),
        "galleryFilters": body.gallery_filters,
        "galleryItems": [item.model_dump(by_alias=True) for item in body.gallery_items],
        "cornerPopup": body.corner_popup.model_dump(by_alias=True),
    }
    data = await set_discover_page_settings(db, raw)
    return _to_out(data)
