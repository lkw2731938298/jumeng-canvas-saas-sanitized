import asyncio
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool

from ...core.config import get_settings
from ...core.deps import get_current_user
from ...core.error_codes import ErrorCode
from ...core.errors import fail
from ...integrations.oss.canvas_storage import get_canvas_storage
from ...models.database import get_db
from ...models.user import User
from ...services.project_access import require_project_access
from ...services.project_scope import (
    assert_safe_storage_key,
    resolve_project_id_from_oss_key,
)
from ...services.storage_quota import get_user_storage_snapshot
from ...services.storage_urls import public_url_for_key

router = APIRouter()

_CHUNK_SIZE = 65536
_LEGACY_PREFIXES = ("canvas", "Ihuabu")


def _allowed_storage_key(oss_key: str) -> bool:
    settings = get_settings()
    prefix = (settings.oss_object_prefix or "canvas").rstrip("/")
    if oss_key.startswith(f"{prefix}/"):
        return True
    return any(oss_key.startswith(f"{legacy}/") for legacy in _LEGACY_PREFIXES)


def _content_type_for_key(oss_key: str) -> str:
    lower = oss_key.lower()
    if lower.endswith(".json"):
        return "application/json; charset=utf-8"
    if lower.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".webm"):
        return "video/webm"
    if lower.endswith(".mp3"):
        return "audio/mpeg"
    if lower.endswith(".wav"):
        return "audio/wav"
    return "application/octet-stream"


def _parse_byte_range(range_header: str, total_size: int) -> tuple[int, int] | None:
    """解析单段 HTTP Range，返回包含首尾的字节区间。"""
    raw = (range_header or "").strip()
    if not raw.lower().startswith("bytes=") or "," in raw or total_size <= 0:
        return None
    value = raw[6:].strip()
    if "-" not in value:
        return None
    start_raw, end_raw = value.split("-", 1)
    try:
        if not start_raw:
            suffix = int(end_raw)
            if suffix <= 0:
                return None
            start = max(0, total_size - suffix)
            return start, total_size - 1
        start = int(start_raw)
        if start < 0 or start >= total_size:
            return None
        end = int(end_raw) if end_raw else total_size - 1
        end = min(end, total_size - 1)
        if end < start:
            return None
        return start, end
    except (TypeError, ValueError):
        return None


def _stream_cors_headers() -> dict[str, str]:
    """画布截帧 / video seek 所需的 CORS 与 Range 声明。"""
    return {
        "Cache-Control": "private, max-age=3600",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization, Range",
        # Chrome 无 Accept-Ranges 时无法 seek，截尾帧会落回首帧
        "Accept-Ranges": "bytes",
    }


async def _resolve_storage_object(
    db: AsyncSession,
    current_user: User,
    key: str,
) -> tuple[str, object]:
    try:
        oss_key = assert_safe_storage_key(unquote(key))
    except ValueError as exc:
        fail(ErrorCode.INVALID_STORAGE_KEY, message=str(exc))

    if not _allowed_storage_key(oss_key):
        fail(ErrorCode.ACCESS_DENIED)

    project_id = await resolve_project_id_from_oss_key(db, oss_key)
    if not project_id:
        fail(ErrorCode.ACCESS_DENIED)
    await require_project_access(db, current_user, project_id)

    storage = get_canvas_storage()
    resolved_key = await asyncio.to_thread(storage.resolve_existing_key, oss_key)
    if not resolved_key:
        fail(ErrorCode.OBJECT_NOT_FOUND)
    return resolved_key, storage


@router.get("/object")
async def get_storage_object(
    key: str,
    request: Request,
    stream: bool = Query(False, description="Stream bytes through API (canvas-safe, no OSS redirect)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resolved_key, storage = await _resolve_storage_object(db, current_user, key)

    settings = get_settings()
    mode = (settings.storage_url_mode or "proxy").strip().lower()
    # 非 stream：cdn/signed 可 302 到 CDN 或 OSS；stream=1 必须经本 API 代理（截帧 CORS + Range）
    if mode in ("signed", "cdn") and not stream:
        target = public_url_for_key(resolved_key)
        if target.startswith("http://") or target.startswith("https://"):
            return RedirectResponse(url=target, status_code=302)

    total_size = await asyncio.to_thread(storage.get_object_size, resolved_key)
    if total_size <= 0:
        # 兼容 size 探测失败但仍可读的对象：回退整读（仍声明 Accept-Ranges）
        data = await asyncio.to_thread(storage.get_bytes, resolved_key)
        if not data:
            fail(ErrorCode.OBJECT_NOT_FOUND)
        return Response(
            content=data,
            media_type=_content_type_for_key(resolved_key),
            headers={
                **_stream_cors_headers(),
                "Content-Length": str(len(data)),
            },
        )

    common_headers = _stream_cors_headers()
    media_type = _content_type_for_key(resolved_key)
    range_header = request.headers.get("range")
    if range_header:
        byte_range = _parse_byte_range(range_header, total_size)
        if not byte_range:
            return Response(
                status_code=416,
                headers={
                    **common_headers,
                    "Content-Range": f"bytes */{total_size}",
                },
            )
        start, end = byte_range
        content_length = end - start + 1
        return StreamingResponse(
            iterate_in_threadpool(
                storage.iter_byte_range(resolved_key, start, end, _CHUNK_SIZE)
            ),
            status_code=206,
            media_type=media_type,
            headers={
                **common_headers,
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Content-Length": str(content_length),
            },
        )

    return StreamingResponse(
        iterate_in_threadpool(storage.iter_chunks(resolved_key, _CHUNK_SIZE)),
        media_type=media_type,
        headers={
            **common_headers,
            "Content-Length": str(total_size),
        },
    )


@router.get("/sign-url")
async def get_storage_sign_url(
    key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resolved_key, _storage = await _resolve_storage_object(db, current_user, key)
    return {"url": public_url_for_key(resolved_key)}


class StorageQuotaOut(BaseModel):
    """用户云存储配额快照。"""

    model_config = ConfigDict(populate_by_name=True)

    used_bytes: int = Field(..., alias="usedBytes")
    quota_bytes: int = Field(..., alias="quotaBytes")
    general_gb: int = Field(..., alias="generalGb")
    member_gb: int = Field(..., alias="memberGb")
    is_member_active: bool = Field(..., alias="isMemberActive")
    member_expires_at: str | None = Field(None, alias="memberExpiresAt")
    used_gb: float = Field(..., alias="usedGb")
    quota_gb: float = Field(..., alias="quotaGb")


@router.get("/quota", response_model=StorageQuotaOut)
async def get_my_storage_quota(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询当前用户云存储占用与配额（按名下全部项目 OSS 素材汇总）。"""
    snapshot = await get_user_storage_snapshot(db, current_user.id)
    return StorageQuotaOut(**snapshot.to_dict())
