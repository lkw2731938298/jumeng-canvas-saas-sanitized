"""用户 CosyVoice 复刻音色：列表 / 创建 / 收藏 / 删除。"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import now_cst_naive, to_cst_iso
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..common.utils.redis_mark_lock import MARK_LOCK_VALUE, mark_claim, mark_exists, mark_seal
from ..integrations.upstream.dashscope import CLONE_TARGET_MODEL, cosyvoice_create_voice
from ..integrations.upstream.errors import UpstreamError
from ..models.user_voice_clone import UserVoiceClone

_PREFIX_RE = re.compile(r"[^a-z0-9]+")
_VOICE_CLONE_LOCK_TTL = 120


def _voice_clone_lock_key(user_id: int) -> str:
    return f"lock:voice:clone:{user_id}"


def _sanitize_prefix(raw: str) -> str:
    s = _PREFIX_RE.sub("", (raw or "").lower())[:10]
    if len(s) >= 2:
        return s
    return "jm" + uuid.uuid4().hex[:8]


def _row_out(row: UserVoiceClone) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "voice_id": row.voice_id,
        "display_name": row.display_name,
        "target_model": row.target_model,
        "language": row.language or "zh",
        "gender": row.gender,
        "favorite": bool(row.favorite),
        "source_audio_url": row.source_audio_url,
        "source_asset_id": row.source_asset_id,
        "created_at": to_cst_iso(row.created_at) if row.created_at else None,
    }


async def list_user_voice_clones(
    db: AsyncSession,
    *,
    user_id: int,
    favorite_only: bool = False,
) -> list[dict[str, Any]]:
    """按用户列出复刻音色（新→旧）。"""
    stmt = select(UserVoiceClone).where(UserVoiceClone.user_id == user_id)
    if favorite_only:
        stmt = stmt.where(UserVoiceClone.favorite.is_(True))
    stmt = stmt.order_by(UserVoiceClone.created_at.desc())
    rows = (await db.execute(stmt)).scalars().all()
    return [_row_out(r) for r in rows]


async def create_user_voice_clone(
    db: AsyncSession,
    *,
    user_id: int,
    audio_url: str,
    display_name: str,
    prefix: str | None = None,
    language: str = "zh",
    gender: str | None = None,
    source_asset_id: str | None = None,
) -> dict[str, Any]:
    """调用百炼 create_voice 并落库；同用户并发用 Redis 锁兜底。"""
    url = (audio_url or "").strip()
    name = (display_name or "").strip()[:64]
    if not url:
        fail(ErrorCode.VALIDATION_ERROR, message="参考音频 URL 不能为空")
    if not name:
        fail(ErrorCode.VALIDATION_ERROR, message="音色名称不能为空")

    lock_key = _voice_clone_lock_key(user_id)
    if await mark_exists(lock_key):
        fail(ErrorCode.RATE_LIMITED, message="声音复刻处理中，请稍后再试")
    if not await mark_claim(lock_key, ttl_sec=_VOICE_CLONE_LOCK_TTL, value=MARK_LOCK_VALUE):
        fail(ErrorCode.RATE_LIMITED, message="声音复刻处理中，请稍后再试")

    try:
        try:
            voice_id = await cosyvoice_create_voice(
                audio_url=url,
                prefix=_sanitize_prefix(prefix or name),
                language_hints=[(language or "zh").strip().lower()[:8] or "zh"],
                target_model=CLONE_TARGET_MODEL,
            )
        except UpstreamError as exc:
            fail(
                ErrorCode.UPSTREAM_ERROR,
                message=str(exc)[:300] or "百炼声音复刻失败",
            )

        row = UserVoiceClone(
            user_id=user_id,
            voice_id=voice_id,
            display_name=name,
            target_model=CLONE_TARGET_MODEL,
            source_audio_url=url[:2000],
            source_asset_id=(source_asset_id or "").strip()[:64] or None,
            language=(language or "zh").strip()[:16] or "zh",
            gender=(gender or "").strip()[:8] or None,
            favorite=False,
            created_at=now_cst_naive(),
            updated_at=now_cst_naive(),
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            fail(ErrorCode.VALIDATION_ERROR, message="该复刻音色已存在")

        await mark_seal(lock_key, ttl_sec=_VOICE_CLONE_LOCK_TTL, value=MARK_LOCK_VALUE)
        return _row_out(row)
    except Exception:
        # 失败不 seal，锁自然过期；业务异常已 fail()
        raise


async def register_existing_voice_clone(
    db: AsyncSession,
    *,
    user_id: int,
    voice_id: str,
    display_name: str,
    source_audio_url: str | None = None,
    source_asset_id: str | None = None,
    language: str = "zh",
    gender: str | None = None,
    target_model: str | None = None,
) -> dict[str, Any]:
    """将已创建的百炼 voice_id 写入「我的音色」（不再次调用上游 create_voice）。

    用于节点模型 ``cosyvoice_clone`` 生成成功后的落库；同用户同 voice_id 幂等返回。
    """
    vid = (voice_id or "").strip()
    name = (display_name or "").strip()[:64] or "复刻音色"
    if not vid:
        fail(ErrorCode.VALIDATION_ERROR, message="voice_id 不能为空")

    existing = (
        await db.execute(
            select(UserVoiceClone).where(
                UserVoiceClone.user_id == user_id,
                UserVoiceClone.voice_id == vid,
            )
        )
    ).scalar_one_or_none()
    if existing:
        return _row_out(existing)

    try:
        # 并发双写时用 savepoint，避免污染外层任务事务
        async with db.begin_nested():
            row = UserVoiceClone(
                user_id=user_id,
                voice_id=vid,
                display_name=name,
                target_model=(target_model or CLONE_TARGET_MODEL).strip() or CLONE_TARGET_MODEL,
                source_audio_url=(source_audio_url or "").strip()[:2000] or None,
                source_asset_id=(source_asset_id or "").strip()[:64] or None,
                language=(language or "zh").strip()[:16] or "zh",
                gender=(gender or "").strip()[:8] or None,
                favorite=False,
                created_at=now_cst_naive(),
                updated_at=now_cst_naive(),
            )
            db.add(row)
            await db.flush()
            return _row_out(row)
    except IntegrityError:
        existing = (
            await db.execute(
                select(UserVoiceClone).where(
                    UserVoiceClone.user_id == user_id,
                    UserVoiceClone.voice_id == vid,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return _row_out(existing)
        fail(ErrorCode.VALIDATION_ERROR, message="该复刻音色已存在")
    # 理论上不可达；满足类型检查
    fail(ErrorCode.VALIDATION_ERROR, message="复刻音色落库失败")


async def set_voice_clone_favorite(
    db: AsyncSession,
    *,
    user_id: int,
    clone_id: int,
    favorite: bool,
) -> dict[str, Any]:
    """切换收藏。"""
    row = await db.get(UserVoiceClone, clone_id)
    if not row or int(row.user_id) != int(user_id):
        fail(ErrorCode.NOT_FOUND, message="音色不存在")
    row.favorite = bool(favorite)
    row.updated_at = now_cst_naive()
    await db.flush()
    return _row_out(row)


async def delete_user_voice_clone(
    db: AsyncSession,
    *,
    user_id: int,
    clone_id: int,
) -> None:
    """删除本地记录（不调用百炼删除接口）。"""
    row = await db.get(UserVoiceClone, clone_id)
    if not row or int(row.user_id) != int(user_id):
        fail(ErrorCode.NOT_FOUND, message="音色不存在")
    await db.delete(row)
    await db.flush()
