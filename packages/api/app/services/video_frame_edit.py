"""视频画面编辑：时长门禁 + 智能抠像（本地 rembg）；主体消除 mode 仍保留兼容。

强制源片时长 ≤ MAX_VIDEO_FRAME_EDIT_SEC；超出须先剪辑。
后台可为各 canvasTool 配置主/副模型（本地能力用模型名结算专用算力）。
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.datetime_util import cst_iso_now
from ..core.entity_ids import require_entity_id
from ..core.error_codes import ErrorCode
from ..core.errors import fail
from ..integrations.oss.canvas_storage import StorageWriteError, get_canvas_storage, new_asset_id
from ..models.project import Project
from .asset_store import get_project_asset, insert_project_asset
from .cache import invalidate_manifest_cache
from .project_scope import project_storage_folder
from .shot_detection import MAX_VIDEO_BYTES, probe_duration_sec, require_ffmpeg
from .storage_quota import assert_storage_quota_for_project
from .storage_urls import normalize_browser_storage_url
from .video_thumbnail import extract_video_thumbnail_jpeg
from .video_trim import _suffix_from_asset

logger = logging.getLogger(__name__)

# 画面编辑强制上限（秒）；超出须先用「剪辑」缩短
MAX_VIDEO_FRAME_EDIT_SEC = 12.0
# 抽帧帧率（兼顾效果与耗时）
FRAME_EDIT_FPS = 8
# 本地画面编辑模式（不上游）
LOCAL_FRAME_EDIT_MODES = frozenset({"smart_matting", "subject_remove"})

FrameEditMode = Literal["smart_matting", "subject_remove", "subject_edit", "subject_replace"]

MODE_TO_CANVAS_TOOL = {
    "smart_matting": "video_smart_matting",
    "subject_remove": "video_subject_remove",
    "subject_edit": "video_subject_edit",
    "subject_replace": "video_subject_replace",
}


def canvas_tool_for_frame_edit_mode(mode: str) -> str:
    tool = MODE_TO_CANVAS_TOOL.get((mode or "").strip())
    if not tool:
        fail(ErrorCode.BAD_REQUEST, message=f"不支持的画面编辑模式：{mode}")
    return tool


def _require_rembg():
    try:
        from rembg import remove  # type: ignore
    except ImportError as exc:
        raise ValueError(
            "服务器未安装 rembg，无法进行画面编辑。请执行：pip install rembg"
        ) from exc
    return remove


def _process_frame_rgba(remove_fn, frame_bytes: bytes, *, mode: str) -> bytes:
    """单帧：抠像保留透明主体，或消除主体后填背景。"""
    import io

    from PIL import Image
    import numpy as np

    cut = remove_fn(frame_bytes)
    rgba = Image.open(io.BytesIO(cut)).convert("RGBA")
    if mode == "smart_matting":
        # 多数浏览器对 WebM 透明通道支持差，合成深色底便于画布上看到抠像结果
        bg = Image.new("RGBA", rgba.size, (26, 26, 32, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        buf = io.BytesIO()
        bg.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()

    # 主体消除：用原图非主体区域均值填回主体区域（轻量、无 opencv）
    orig = Image.open(io.BytesIO(frame_bytes)).convert("RGBA")
    arr = np.array(orig)
    alpha = np.array(rgba)[:, :, 3]
    mask = alpha > 16
    if not mask.any() or mask.all():
        buf = io.BytesIO()
        orig.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()
    bg = arr[~mask][:, :3].mean(axis=0)
    arr[mask, 0] = bg[0]
    arr[mask, 1] = bg[1]
    arr[mask, 2] = bg[2]
    arr[mask, 3] = 255
    out = Image.fromarray(arr, "RGBA").convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


def _encode_webm(
    ffmpeg: str,
    frame_dir: Path,
    out_path: Path,
    *,
    fps: float,
    with_alpha: bool,
) -> bytes:
    """PNG 序列编码为 WebM（透明用 VP9 yuva420p）。"""
    pattern = str(frame_dir / "out_%05d.png")
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        str(fps),
        "-i",
        pattern,
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "0",
        "-crf",
        "32",
        "-an",
        "-auto-alt-ref",
        "0",
    ]
    if with_alpha:
        cmd.extend(["-pix_fmt", "yuva420p"])
    else:
        cmd.extend(["-pix_fmt", "yuv420p"])
    cmd.append(str(out_path))
    proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise ValueError(f"WebM 编码失败: {err or 'ffmpeg error'}")
    data = out_path.read_bytes()
    if not data:
        raise ValueError("WebM 结果为空")
    if len(data) > MAX_VIDEO_BYTES:
        raise ValueError("结果视频超过 200MB")
    return data


def run_local_frame_edit_bytes(
    video_path: str,
    ffmpeg: str,
    *,
    mode: str,
    fps: float = FRAME_EDIT_FPS,
) -> bytes:
    """同步执行本地画面编辑，返回 webm bytes。"""
    remove_fn = _require_rembg()
    with tempfile.TemporaryDirectory(prefix="vfe_") as tmp:
        work = Path(tmp)
        frames_in = work / "in"
        frames_out = work / "out"
        frames_in.mkdir()
        frames_out.mkdir()
        # 抽到 in/
        pattern_in = str(frames_in / "frame_%05d.png")
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                video_path,
                "-vf",
                f"fps={fps}",
                "-vsync",
                "0",
                pattern_in,
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
            raise ValueError(f"抽帧失败: {err or 'ffmpeg error'}")
        frames = sorted(frames_in.glob("frame_*.png"))
        if not frames:
            raise ValueError("未抽出任何帧")
        if len(frames) > 200:
            raise ValueError("抽帧过多，请先剪辑缩短视频")

        for i, fp in enumerate(frames, start=1):
            raw = fp.read_bytes()
            processed = _process_frame_rgba(remove_fn, raw, mode=mode)
            (frames_out / f"out_{i:05d}.png").write_bytes(processed)

        out_webm = work / "result.webm"
        # 智能抠像已烘焙不透明底，无需 yuva；主体消除同为 RGB
        return _encode_webm(
            ffmpeg,
            frames_out,
            out_webm,
            fps=fps,
            with_alpha=False,
        )


async def edit_project_video_frames(
    db: AsyncSession,
    *,
    project_id: str,
    video_asset_id: str,
    mode: str,
    title: str | None = None,
) -> dict[str, Any]:
    """本地画面编辑：校验时长 → rembg 处理 → 注册 WebM 素材。"""
    mode_key = (mode or "").strip()
    if mode_key not in LOCAL_FRAME_EDIT_MODES:
        fail(
            ErrorCode.BAD_REQUEST,
            message="该模式需走上游视频模型生成，请使用对应提交接口",
        )

    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    asset = await get_project_asset(db, project_id, video_asset_id, sync_oss=False)
    if not asset:
        fail(ErrorCode.ASSET_NOT_FOUND, message="源视频素材不存在")
    if str(asset.get("category") or "") != "video":
        fail(ErrorCode.BAD_REQUEST, message="仅支持对视频素材进行画面编辑")

    oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
    if not oss_key:
        fail(ErrorCode.BAD_REQUEST, message="源视频缺少存储路径")

    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    try:
        video_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("video_frame_edit read failed: %s", exc)
        fail(ErrorCode.BAD_REQUEST, message="无法读取源视频，请重新上传")

    if not video_bytes:
        fail(ErrorCode.BAD_REQUEST, message="源视频内容为空")
    if len(video_bytes) > MAX_VIDEO_BYTES:
        fail(ErrorCode.BAD_REQUEST, message="源视频超过 200MB，无法编辑")

    suffix = _suffix_from_asset(asset)
    video_path = ""
    try:
        import os

        with tempfile.NamedTemporaryFile(suffix=suffix or ".mp4", delete=False) as vf:
            vf.write(video_bytes)
            vf.flush()
            os.fsync(vf.fileno())
            video_path = vf.name

        ffmpeg, ffprobe = require_ffmpeg()
        # 与上游门禁同一套稳健探测，避免 format.duration 虚高
        duration = await asyncio.to_thread(probe_frame_edit_duration_sec, video_path, ffprobe)
        if duration > MAX_VIDEO_FRAME_EDIT_SEC + 0.05:
            fail(
                ErrorCode.VALIDATION_ERROR,
                message=(
                    f"画面编辑仅支持 ≤{MAX_VIDEO_FRAME_EDIT_SEC:g} 秒的视频，"
                    f"当前约 {duration:.1f} 秒，请先使用「剪辑」缩短后再试"
                ),
            )

        webm_bytes = await asyncio.to_thread(
            run_local_frame_edit_bytes,
            video_path,
            ffmpeg,
            mode=mode_key,
            fps=FRAME_EDIT_FPS,
        )
    except ValueError as exc:
        fail(ErrorCode.BAD_REQUEST, message=str(exc))
    finally:
        if video_path:
            try:
                Path(video_path).unlink(missing_ok=True)
            except OSError:
                pass

    thumb_data = await asyncio.to_thread(extract_video_thumbnail_jpeg, webm_bytes, "webm")
    total_size = len(webm_bytes) + (len(thumb_data) if thumb_data else 0)
    await assert_storage_quota_for_project(db, project_id, total_size)

    asset_id = new_asset_id()
    rel = f"assets/video/{asset_id}.webm"
    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel,
            webm_bytes,
            "video/webm",
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"画面编辑结果存储失败: {exc}")

    thumb_url = normalize_browser_storage_url(stored.file_url, oss_key=stored.oss_key)
    thumb_key = ""
    if thumb_data:
        thumb_rel = f"assets/image/{asset_id}_thumb.jpg"
        try:
            stored_thumb = await asyncio.to_thread(
                storage.put,
                project_id,
                thumb_rel,
                thumb_data,
                "image/jpeg",
                storage_folder=folder,
            )
            thumb_url = normalize_browser_storage_url(
                stored_thumb.file_url, oss_key=stored_thumb.oss_key
            )
            thumb_key = stored_thumb.oss_key
        except StorageWriteError:
            logger.warning("video_frame_edit thumb failed for %s", asset_id)

    labels = {
        "smart_matting": "智能抠像",
        "subject_remove": "主体消除",
    }
    label = labels.get(mode_key, "画面编辑")
    source_title = str(asset.get("title") or "").strip()
    default_title = f"{source_title} · {label}" if source_title else label

    record = {
        "id": asset_id,
        "projectId": project_id,
        "title": (title or "").strip() or default_title,
        "category": "video",
        "subcategory": label,
        "fileUrl": stored.file_url,
        "thumbnailUrl": thumb_url,
        "fileType": "video/webm",
        "fileSize": len(webm_bytes),
        "ossKey": stored.oss_key,
        "source": f"video_frame_edit:{mode_key}",
        "createdAt": cst_iso_now(),
    }
    if thumb_key:
        record["thumbnailOssKey"] = thumb_key

    saved = await insert_project_asset(db, project_id, record, sync_oss=False)
    await invalidate_manifest_cache(project_id)
    return {
        "asset": saved,
        "mode": mode_key,
        "canvasTool": canvas_tool_for_frame_edit_mode(mode_key),
        "maxDurationSec": MAX_VIDEO_FRAME_EDIT_SEC,
        "sourceAssetId": video_asset_id,
        "outputFormat": "video/webm",
        "hasAlpha": False,
    }


def assert_duration_for_frame_edit(duration_sec: float) -> None:
    """时长门禁：超出则要求先剪辑（本地与上游提交共用）。"""
    if duration_sec > MAX_VIDEO_FRAME_EDIT_SEC + 0.05:
        fail(
            ErrorCode.VALIDATION_ERROR,
            message=(
                f"画面编辑仅支持 ≤{MAX_VIDEO_FRAME_EDIT_SEC:g} 秒的视频，"
                f"当前约 {duration_sec:.1f} 秒，请先使用「剪辑」缩短后再试"
            ),
        )


# 走上游 media/generate 的画面编辑工具（须与本地一样先过时长门禁）
UPSTREAM_FRAME_EDIT_TOOLS = frozenset(
    {"video_subject_edit", "video_subject_replace"}
)


def _ffprobe_duration_field(
    ffprobe: str,
    video_path: str,
    entries: str,
    *,
    select_video_stream: bool = False,
) -> float | None:
    """读 ffprobe 单个 duration 字段；无效则 None。"""
    cmd = [ffprobe, "-v", "error"]
    if select_video_stream:
        cmd.extend(["-select_streams", "v:0"])
    cmd.extend(
        [
            "-show_entries",
            entries,
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
    )
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        return None
    raw = (proc.stdout or b"").decode("utf-8", errors="replace").strip().splitlines()
    for line in raw:
        line = line.strip()
        if not line or line.upper() == "N/A":
            continue
        try:
            value = float(line)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def probe_frame_edit_duration_sec(video_path: str, ffprobe: str) -> float:
    """画面编辑专用时长探测：综合 format / 视频流，避免容器元数据虚高误判「太长」。

    部分转码/WebM 的 format.duration 会远大于真实画面时长；若流时长更短且合理则取较短值。
    """
    format_dur = _ffprobe_duration_field(ffprobe, video_path, "format=duration")
    stream_dur = _ffprobe_duration_field(
        ffprobe, video_path, "stream=duration", select_video_stream=True
    )
    # 兼容：个别文件需走通用探测
    if format_dur is None:
        try:
            format_dur = probe_duration_sec(video_path, ffprobe)
        except ValueError:
            format_dur = None

    candidates = [d for d in (format_dur, stream_dur) if isinstance(d, (int, float)) and d > 0]
    if not candidates:
        raise ValueError("无法读取视频时长")

    # 任一值荒谬（>10 分钟）视为坏元数据，优先另一候选
    sane = [d for d in candidates if d <= 600.0]
    pool = sane or candidates
    chosen = min(pool)

    if format_dur and stream_dur and format_dur > stream_dur * 1.25:
        # 容器时长虚高：以视频流为准（常见于坏 header / 未写完的 WebM）
        if stream_dur <= 600.0:
            chosen = stream_dur
            logger.info(
                "frame_edit duration prefer stream=%.3f over format=%.3f",
                stream_dur,
                format_dur,
            )

    if chosen > 600.0:
        raise ValueError(f"视频时长元数据异常: {chosen:.1f}s")
    return float(chosen)


def _probe_duration_from_bytes(data: bytes, *, suffix: str = ".mp4") -> float:
    """落临时文件后用 ffprobe 读时长（避免对签名 URL 直探失败）。"""
    import os

    _, ffprobe = require_ffmpeg()
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix or ".mp4", delete=False) as vf:
            vf.write(data)
            vf.flush()
            os.fsync(vf.fileno())
            path = vf.name
        return probe_frame_edit_duration_sec(path, ffprobe)
    finally:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


async def _download_url_bytes(url: str, *, max_bytes: int = MAX_VIDEO_BYTES) -> bytes:
    """下载源视频字节（仅本项目 OSS/CDN 签名域）；供时长探测。

    防 SSRF：HTTPS + 域名白名单 + 解析 IP 非私网；重定向逐跳复核。
    """
    import httpx

    from ..core.safe_outbound_url import (
        UnsafeOutboundUrlError,
        MAX_REDIRECTS,
        assert_safe_oss_media_url,
        resolve_redirect_url,
    )

    current = assert_safe_oss_media_url(url)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=15.0),
        follow_redirects=False,
    ) as client:
        resp = None
        for _ in range(MAX_REDIRECTS + 1):
            # 每次请求前再校验（防重定向 / DNS 变化落到私网）
            current = assert_safe_oss_media_url(current)
            resp = await client.get(current)
            if resp.is_redirect:
                loc = resp.headers.get("location") or ""
                current = resolve_redirect_url(current, loc)
                continue
            break
        else:
            raise UnsafeOutboundUrlError("重定向次数过多")

        assert resp is not None
        resp.raise_for_status()
        data = resp.content
    if not data:
        raise ValueError("源视频内容为空")
    if len(data) > max_bytes:
        raise ValueError("源视频超过 200MB，无法校验时长")
    return data


async def assert_frame_edit_source_duration(
    db: AsyncSession | None = None,
    *,
    project_id: str | None = None,
    video_asset_id: str | None = None,
    source_url: str | None = None,
) -> None:
    """画面编辑时长门禁：优先 OSS 内网读素材，其次下载 sourceUrl，再本地 ffprobe。

    禁止对签名 URL 直接 ffprobe（ECS 内网/URL 转义常导致误报「无法校验」）。
    HTTP 兜底仅允许本项目 OSS/CDN 域名，禁止私网与云元数据（防 SSRF）。
    """
    from ..core.safe_outbound_url import UnsafeOutboundUrlError
    from .storage_urls import oss_key_from_browser_url

    last_err: BaseException | None = None
    duration: float | None = None

    # 1) 权威：项目素材 → OSS 内网 get_bytes
    pid = (project_id or "").strip()
    aid = (video_asset_id or "").strip()
    if db is not None and pid and aid:
        try:
            asset = await get_project_asset(db, pid, aid, sync_oss=False)
            if asset and str(asset.get("category") or "") == "video":
                oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
                if oss_key:
                    storage = get_canvas_storage()
                    video_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
                    if video_bytes:
                        duration = await asyncio.to_thread(
                            _probe_duration_from_bytes,
                            video_bytes,
                            suffix=_suffix_from_asset(asset),
                        )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("frame_edit duration via OSS asset failed: %s", exc)

    # 2) 从浏览器 URL 解析 OSS key 再内网读
    url = (source_url or "").strip()
    if duration is None and url:
        try:
            key = oss_key_from_browser_url(url)
            if key:
                storage = get_canvas_storage()
                video_bytes = await asyncio.to_thread(storage.get_bytes, key)
                if video_bytes:
                    suf = Path(key).suffix or ".mp4"
                    duration = await asyncio.to_thread(
                        _probe_duration_from_bytes, video_bytes, suffix=suf
                    )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("frame_edit duration via oss key failed: %s", exc)

    # 3) HTTP 下载签名 URL 到本地再探（最后兜底 · 须过 SSRF 门禁）
    if duration is None and url:
        try:
            data = await _download_url_bytes(url)
            duration = await asyncio.to_thread(_probe_duration_from_bytes, data, suffix=".mp4")
        except UnsafeOutboundUrlError as exc:
            last_err = exc
            logger.warning("frame_edit duration rejected unsafe sourceUrl: %s", exc)
            fail(
                ErrorCode.VALIDATION_ERROR,
                message="源视频地址无效或不允许访问",
            )
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning("frame_edit duration via URL download failed: %s", exc)

    if duration is None:
        if not url and not aid:
            fail(
                ErrorCode.VALIDATION_ERROR,
                message=(
                    f"画面编辑须提供源视频，且时长 ≤{MAX_VIDEO_FRAME_EDIT_SEC:g} 秒；"
                    "请先剪辑后再试"
                ),
            )
        logger.warning("frame_edit duration probe exhausted last_err=%s", last_err)
        fail(
            ErrorCode.VALIDATION_ERROR,
            message=(
                "无法读取源视频时长（不是判定超长）。请确认素材可访问；"
                f"若浏览器里片长已 ≤{MAX_VIDEO_FRAME_EDIT_SEC:g} 秒，可先「剪辑」导出新片再试"
            ),
        )
    logger.info(
        "frame_edit duration ok=%.3fs asset=%s url_set=%s",
        duration,
        aid or "-",
        bool(url),
    )
    assert_duration_for_frame_edit(duration)


# 兼容旧名
async def assert_frame_edit_source_duration_from_url(source_url: str | None) -> None:
    await assert_frame_edit_source_duration(source_url=source_url)
