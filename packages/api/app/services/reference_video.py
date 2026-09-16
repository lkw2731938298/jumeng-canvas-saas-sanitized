"""将画布参考视频解析为多模态上游可用的预签名 URL 或关键帧回退。"""

from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..integrations.oss.canvas_storage import get_canvas_storage
from .reference_image import _extract_oss_key, _is_internal_reference_url
from .project_scope import oss_key_belongs_to_project

logger = logging.getLogger(__name__)

_VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")


def _is_video_key(oss_key: str) -> bool:
    return oss_key.lower().endswith(_VIDEO_EXTENSIONS)


def _suffix_for_key(oss_key: str) -> str:
    lower = oss_key.lower()
    for ext in _VIDEO_EXTENSIONS:
        if lower.endswith(ext):
            return ext
    return ".mp4"


def _video_content_type(oss_key: str) -> str:
    lower = oss_key.lower()
    if lower.endswith(".webm"):
        return "video/webm"
    if lower.endswith(".mov"):
        return "video/quicktime"
    if lower.endswith(".m4v"):
        return "video/x-m4v"
    return "video/mp4"


def extract_video_frame_data_uri(video_bytes: bytes, *, oss_key: str = "video.mp4") -> str:
    """从视频字节提取一帧 JPEG，生成视觉 API 可用的 data:image base64。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("服务器未安装 ffmpeg，无法从本地视频提取关键帧；请配置 OSS 后重试")

    if len(video_bytes) > 200 * 1024 * 1024:
        raise ValueError("参考视频超过 200MB，无法提取关键帧")

    suffix = _suffix_for_key(oss_key)
    video_path = ""
    frame_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as vf:
            vf.write(video_bytes)
            video_path = vf.name
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as jf:
            frame_path = jf.name

        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                video_path,
                "-vframes",
                "1",
                "-q:v",
                "2",
                frame_path,
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
            raise ValueError(f"视频关键帧提取失败: {err or 'ffmpeg error'}")

        frame_bytes = Path(frame_path).read_bytes()
        if not frame_bytes:
            raise ValueError("视频关键帧提取结果为空")
        if len(frame_bytes) > 10 * 1024 * 1024:
            raise ValueError("视频关键帧超过 10MB")

        encoded = base64.b64encode(frame_bytes).decode("ascii")
        logger.info("Resolved video reference to key-frame image (%s, %d bytes)", oss_key, len(frame_bytes))
        return f"data:image/jpeg;base64,{encoded}"
    finally:
        for path in (video_path, frame_path):
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass


def _audio_content_type(oss_key: str) -> str:
    lower = oss_key.lower()
    if lower.endswith(".wav"):
        return "audio/wav"
    if lower.endswith(".ogg"):
        return "audio/ogg"
    if lower.endswith(".m4a"):
        return "audio/mp4"
    if lower.endswith(".flac"):
        return "audio/flac"
    if lower.endswith(".aac"):
        return "audio/aac"
    return "audio/mpeg"


def resolve_audio_reference_url(
    url: str,
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> str:
    """将画布参考音频解析为预签名 OSS URL 或 data:audio base64（本地模式）。"""
    import base64

    from ..core.config import get_settings
    from ..integrations.oss.service import get_oss

    cleaned = url.strip()
    if not cleaned or not _is_internal_reference_url(cleaned):
        return cleaned

    oss_key = _extract_oss_key(cleaned)
    if not oss_key:
        raise ValueError("参考音频 URL 无法解析存储 key")

    if project_id and not oss_key_belongs_to_project(
        oss_key, project_id, storage_folder=storage_folder
    ):
        raise ValueError("参考音频不属于当前项目")

    settings = get_settings()
    storage = get_canvas_storage()
    content_type = _audio_content_type(oss_key)

    if not settings.canvas_storage_local_only:
        oss = get_oss()
        if oss.bucket:
            if not oss.exists(oss_key):
                storage.ensure_oss_object(oss_key, content_type=content_type)
            if oss.exists(oss_key):
                # 中文：音频参考统一走 public_url_for_key（cdn 即公共 URL）
                from .storage_urls import public_url_for_key

                url = public_url_for_key(oss_key)
                if url.startswith("http://") or url.startswith("https://"):
                    return url

    data = storage.get_bytes(oss_key)
    if not data:
        raise ValueError(f"参考音频未找到: {oss_key}")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("参考音频超过 20MB")

    mime = _audio_content_type(oss_key)
    encoded = base64.b64encode(data).decode("ascii")
    logger.info("Resolved internal audio reference to base64 (%s, %d bytes)", oss_key, len(data))
    return f"data:{mime};base64,{encoded}"


def resolve_lip_sync_media_urls(
    video_url: str,
    audio_url: str,
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> tuple[str, str]:
    """解析对口型所需的视频与音频 URL，须为上游可公网拉取的地址。"""
    from ..core.config import get_settings
    from .reference_image import _is_internal_reference_url

    settings = get_settings()
    v = video_url.strip()
    a = audio_url.strip()
    if settings.canvas_storage_local_only and (
        _is_internal_reference_url(v) or _is_internal_reference_url(a)
    ):
        raise ValueError(
            "对口型需要上游可公网拉取的完整视频/音频 URL；"
            "纯本地存储模式下请暂不对口型，或关闭 CANVAS_STORAGE_LOCAL_ONLY 并配置 OSS"
        )

    from .reference_image import resolve_reference_url

    return (
        resolve_reference_url(v, project_id=project_id, storage_folder=storage_folder),
        resolve_reference_url(a, project_id=project_id, storage_folder=storage_folder),
    )


def resolve_video_reference_for_multimodal(
    url: str,
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
    upstream_presign: bool = False,
) -> tuple[str | None, str | None]:
    """返回 (视频预签名 HTTPS URL, 关键帧 data URI)；优先 OSS 直链，否则回退关键帧。"""
    cleaned = url.strip()
    if not cleaned:
        return None, None

    if not _is_internal_reference_url(cleaned):
        return cleaned, None

    oss_key = _extract_oss_key(cleaned)
    if not oss_key:
        raise ValueError("参考视频 URL 无法解析存储 key")

    if project_id and not oss_key_belongs_to_project(
        oss_key, project_id, storage_folder=storage_folder
    ):
        raise ValueError("参考视频不属于当前项目")

    storage = get_canvas_storage()
    from ..core.config import get_settings
    from ..integrations.oss.service import get_oss

    oss = get_oss()
    if oss.bucket and not get_settings().canvas_storage_local_only:
        if oss.exists(oss_key) or storage.ensure_oss_object(
            oss_key, content_type=_video_content_type(oss_key)
        ):
            if upstream_presign:
                from .reference_image import upstream_fetchable_url_for_key

                signed = upstream_fetchable_url_for_key(oss_key)
                if signed:
                    return signed, None
            else:
                # 中文：视频参考统一走 public_url_for_key（cdn 即公共 URL），禁止硬编码 presign
                from .storage_urls import public_url_for_key

                pub = public_url_for_key(oss_key)
                if pub.startswith("http://") or pub.startswith("https://"):
                    return pub, None

    if not _is_video_key(oss_key):
        raise ValueError("参考文件不是支持的视频格式")

    data = storage.get_bytes(oss_key)
    if not data:
        raise ValueError(f"参考视频未找到: {oss_key}")

    logger.info(
        "OSS unavailable or backfill failed for %s; using key-frame fallback (%d bytes)",
        oss_key,
        len(data),
    )
    return None, extract_video_frame_data_uri(data, oss_key=oss_key)


def resolve_video_keyframe_for_reference(
    url: str,
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
) -> str:
    """从画布或外部视频 URL 提取参考图 data URI。"""
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("参考视频 URL 为空")

    _, frame_uri = resolve_video_reference_for_multimodal(
        cleaned, project_id=project_id, storage_folder=storage_folder
    )
    if frame_uri:
        return frame_uri

    oss_key = _extract_oss_key(cleaned) if _is_internal_reference_url(cleaned) else None
    if oss_key:
        if project_id and not oss_key_belongs_to_project(
            oss_key, project_id, storage_folder=storage_folder
        ):
            raise ValueError("参考视频不属于当前项目")
        storage = get_canvas_storage()
        data = storage.get_bytes(oss_key)
        if data:
            return extract_video_frame_data_uri(data, oss_key=oss_key)

    from ..upstream.fetch import download_bytes_sync

    data, _ = download_bytes_sync(cleaned)
    return extract_video_frame_data_uri(data, oss_key="reference.mp4")


def resolve_video_urls_for_upstream(
    urls: list[str],
    *,
    project_id: str | None = None,
    storage_folder: str | None = None,
    upstream_presign: bool = False,
) -> list[str]:
    """将参考视频解析为上游可拉取的 HTTPS URL；禁止回退关键帧（参考视频须传视频本身）。"""
    out: list[str] = []
    for raw in urls:
        cleaned = str(raw or "").strip()
        if not cleaned:
            continue
        video_https, frame_uri = resolve_video_reference_for_multimodal(
            cleaned,
            project_id=project_id,
            storage_folder=storage_folder,
            upstream_presign=upstream_presign,
        )
        if video_https:
            out.append(video_https)
            continue
        if frame_uri:
            raise ValueError(
                "参考视频无法解析为公网视频 URL（仅得到关键帧）；"
                "请确认 OSS/CDN 可访问或配置 CANVAS_STORAGE_LOCAL_ONLY=false"
            )
        raise ValueError(f"参考视频无法解析: {cleaned}")
    return out
