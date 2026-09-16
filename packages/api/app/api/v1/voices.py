"""画布音色：系统预置库 + 用户百炼复刻音色。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...models.database import get_db
from ...models.user import User
from ...services.cosyvoice_library_voices import library_voices
from ...services.user_voice_clones import (
    create_user_voice_clone,
    delete_user_voice_clone,
    list_user_voice_clones,
    set_voice_clone_favorite,
)

router = APIRouter()


class VoiceCloneCreateIn(BaseModel):
    """声音复刻请求：参考音频公网 URL + 展示名。"""

    model_config = ConfigDict(populate_by_name=True)

    audio_url: Optional[str] = Field(None, alias="audioUrl")
    display_name: str = Field(..., alias="displayName")
    prefix: Optional[str] = None
    language: str = "zh"
    gender: Optional[str] = None
    source_asset_id: Optional[str] = Field(None, alias="sourceAssetId")
    project_id: Optional[str] = Field(None, alias="projectId")


class VoiceFavoriteIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    favorite: bool = True


async def _resolve_clone_audio_url(
    db: AsyncSession,
    *,
    user: User,
    audio_url: str | None,
    source_asset_id: str | None,
    project_id: str | None,
) -> tuple[str, str | None]:
    """优先用项目资产签出 OSS HTTPS URL，供百炼拉取参考音。"""
    asset_id = (source_asset_id or "").strip()
    pid = (project_id or "").strip()
    if asset_id and pid:
        from ...services.asset_store import get_project_asset
        from ...services.project_access import require_project_access
        from ...services.storage_urls import public_url_for_key

        await require_project_access(db, user, pid)
        asset = await get_project_asset(db, pid, asset_id, sync_oss=False)
        if not asset:
            fail(ErrorCode.ASSET_NOT_FOUND, message="参考音频素材不存在")
        # get_project_asset 可能返回 ORM 或 dict
        if isinstance(asset, dict):
            oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
        else:
            oss_key = str(getattr(asset, "oss_key", None) or getattr(asset, "ossKey", None) or "").strip()
        if oss_key:
            url = public_url_for_key(oss_key)
            if url and url.startswith("http"):
                return url, asset_id
        fail(ErrorCode.VALIDATION_ERROR, message="参考音频无法生成可访问链接，请重试上传")
    raw = (audio_url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw, asset_id or None
    fail(ErrorCode.VALIDATION_ERROR, message="请提供可访问的参考音频（建议先上传项目素材）")


@router.get("")
async def list_voices(
    tab: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """音色列表：library 系统库（v3-flash 官方全部预置）；mine / favorite 为用户复刻音色。"""
    t = (tab or "all").strip().lower()
    library = library_voices()
    mine_raw = await list_user_voice_clones(db, user_id=int(user.id))
    mine = [
        {
            "id": f"clone-{m['id']}",
            "name": m["display_name"],
            "voice_id": m["voice_id"],
            "lang": "中文(普通话)" if (m.get("language") or "zh").startswith("zh") else (m.get("language") or ""),
            "gender": m.get("gender") or "",
            "kind": "cloned",
            "tts_model": m.get("target_model") or "cosyvoice-v3.5-plus",
            "clone_id": m["id"],
            "favorite": bool(m.get("favorite")),
        }
        for m in mine_raw
    ]
    if t == "library":
        return {"library": library, "mine": [], "voices": library}
    if t == "mine":
        return {"library": [], "mine": mine, "voices": mine}
    if t in ("favorite", "favourites"):
        fav = [m for m in mine if m.get("favorite")]
        return {"library": [], "mine": fav, "voices": fav}
    return {"library": library, "mine": mine, "voices": library + mine}


@router.post("/clone")
async def clone_voice(
    body: VoiceCloneCreateIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """百炼声音复刻：参考音 URL → voice_id，写入「我的音色」。"""
    audio_url, asset_id = await _resolve_clone_audio_url(
        db,
        user=user,
        audio_url=body.audio_url,
        source_asset_id=body.source_asset_id,
        project_id=body.project_id,
    )
    row = await create_user_voice_clone(
        db,
        user_id=int(user.id),
        audio_url=audio_url,
        display_name=body.display_name,
        prefix=body.prefix,
        language=body.language or "zh",
        gender=body.gender,
        source_asset_id=asset_id,
    )
    await db.commit()
    return {
        "id": f"clone-{row['id']}",
        "name": row["display_name"],
        "voice_id": row["voice_id"],
        "lang": "中文(普通话)",
        "gender": row.get("gender") or "",
        "kind": "cloned",
        "tts_model": row.get("target_model") or "cosyvoice-v3.5-plus",
        "clone_id": row["id"],
        "favorite": False,
    }


@router.patch("/mine/{clone_id}/favorite")
async def patch_voice_favorite(
    clone_id: int,
    body: VoiceFavoriteIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """收藏 / 取消收藏我的复刻音色。"""
    row = await set_voice_clone_favorite(
        db, user_id=int(user.id), clone_id=clone_id, favorite=body.favorite
    )
    await db.commit()
    return row


@router.delete("/mine/{clone_id}")
async def remove_voice(
    clone_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除我的复刻音色本地记录。"""
    await delete_user_voice_clone(db, user_id=int(user.id), clone_id=clone_id)
    await db.commit()
    return {"ok": True}
