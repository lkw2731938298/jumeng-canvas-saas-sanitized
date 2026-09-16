"""完整剪辑 · 多轨压平 + 空隙黑场 + ffmpeg concat，注册为新素材。"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

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
from .shot_detection import (
    MAX_USER_TRIM_DURATION_SEC,
    MAX_VIDEO_BYTES,
    MIN_USER_TRIM_DURATION_SEC,
    probe_duration_sec,
    require_ffmpeg,
)
from .storage_quota import assert_storage_quota_for_project
from .storage_urls import normalize_browser_storage_url
from .video_thumbnail import extract_video_thumbnail_jpeg
from .video_trim import _suffix_from_asset

logger = logging.getLogger(__name__)

MAX_COMPOSE_CLIPS = 20
MAX_COMPOSE_TOTAL_SEC = 600.0
MAX_VIDEO_TRACKS = 4
DEFAULT_GAP_WIDTH = 1280
DEFAULT_GAP_HEIGHT = 720


def _round_t(n: float) -> float:
    return round(float(n), 3)


def _clip_end(start: float, in_v: float, out_v: float) -> float:
    return start + max(0.0, out_v - in_v)


def _flatten_multitrack(
    clips: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """高轨优先压平为有序段：video | gap。"""
    if not clips:
        return []

    # 兼容旧请求：无 startSec/trackIndex → 底轨首尾相接
    packed: list[dict[str, Any]] = []
    cursor = 0.0
    legacy = all(
        raw.get("startSec") is None
        and raw.get("start_sec") is None
        for raw in clips
    )
    for raw in clips:
        in_v = float(raw["inSec"])
        out_v = float(raw["outSec"])
        dur = out_v - in_v
        pad_before = max(0.0, float(raw.get("padBeforeSec", raw.get("pad_before_sec")) or 0.0))
        pad_after = max(0.0, float(raw.get("padAfterSec", raw.get("pad_after_sec")) or 0.0))
        if legacy:
            start = cursor
            track = 0
            cursor += pad_before + dur + pad_after
        else:
            start = float(raw.get("startSec", raw.get("start_sec")) or 0.0)
            track = int(raw.get("trackIndex", raw.get("track_index")) or 0)
            track = max(0, min(MAX_VIDEO_TRACKS - 1, track))
            start = max(0.0, start)
        media_start = start + pad_before
        media_end = media_start + dur
        end_sec = media_end + pad_after
        packed.append(
            {
                "assetId": raw["assetId"],
                "inSec": in_v,
                "outSec": out_v,
                "startSec": start,
                "trackIndex": track,
                "mediaStart": media_start,
                "mediaEnd": media_end,
                "endSec": end_sec,
            }
        )

    total = max((c["endSec"] for c in packed), default=0.0)
    if total <= 0:
        return []

    edges: set[float] = {0.0, _round_t(total)}
    for c in packed:
        edges.add(_round_t(c["startSec"]))
        edges.add(_round_t(c["mediaStart"]))
        edges.add(_round_t(c["mediaEnd"]))
        edges.add(_round_t(c["endSec"]))
    points = sorted(edges)

    segments: list[dict[str, Any]] = []
    for i in range(len(points) - 1):
        a = points[i]
        b = points[i + 1]
        if b - a < 1e-4:
            continue
        mid = (a + b) / 2.0
        # 仅当落在「有画面」媒体窗内才算视频；片头/片尾 pad 视为空镜
        best: dict[str, Any] | None = None
        for c in packed:
            if c["mediaStart"] - 1e-6 <= mid < c["mediaEnd"] - 1e-9:
                if best is None or c["trackIndex"] > best["trackIndex"]:
                    best = c
        if best is None:
            if segments and segments[-1]["kind"] == "gap":
                segments[-1]["duration"] = _round_t(segments[-1]["duration"] + (b - a))
            else:
                segments.append({"kind": "gap", "startSec": a, "duration": _round_t(b - a)})
            continue
        src_in = best["inSec"] + (a - best["mediaStart"])
        src_out = best["inSec"] + (b - best["mediaStart"])
        if (
            segments
            and segments[-1]["kind"] == "video"
            and segments[-1]["assetId"] == best["assetId"]
            and abs(segments[-1]["outSec"] - src_in) < 1e-3
            and abs(segments[-1].get("_track", -1) - best["trackIndex"]) < 1e-9
            and abs(segments[-1].get("_clipStart", -1) - best["startSec"]) < 1e-3
        ):
            segments[-1]["outSec"] = src_out
            segments[-1]["duration"] = _round_t(segments[-1]["outSec"] - segments[-1]["inSec"])
        else:
            segments.append(
                {
                    "kind": "video",
                    "assetId": best["assetId"],
                    "inSec": src_in,
                    "outSec": src_out,
                    "startSec": a,
                    "duration": _round_t(b - a),
                    "_track": best["trackIndex"],
                    "_clipStart": best["startSec"],
                }
            )
    # 去掉内部字段
    for seg in segments:
        seg.pop("_track", None)
        seg.pop("_clipStart", None)
    return segments


def _concat_clip_files(ffmpeg: str, clip_paths: list[str], out_path: str) -> None:
    """concat demuxer + 重编码拼接，统一 yuv420p/aac。"""
    if not clip_paths:
        raise ValueError("没有可拼接的片段")
    total_est = max(30.0, len(clip_paths) * 60.0)
    timeout_sec = max(300, int(total_est * 4) + 120)
    list_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8",
        ) as lf:
            for p in clip_paths:
                escaped = p.replace("'", "'\\''")
                lf.write(f"file '{escaped}'\n")
            list_path = lf.name

        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_path,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "-pix_fmt",
                "yuv420p",
                out_path,
            ],
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            raise ValueError(f"视频拼接失败: {err or 'ffmpeg error'}")
        if not Path(out_path).is_file() or Path(out_path).stat().st_size <= 0:
            raise ValueError("拼接结果为空")
    finally:
        if list_path:
            try:
                Path(list_path).unlink(missing_ok=True)
            except OSError:
                pass


def _probe_video_size(ffprobe: str, path: str) -> tuple[int, int]:
    """探测宽高；失败则默认 1280x720。"""
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                path,
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if proc.returncode == 0:
            text = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
            if "x" in text:
                w_s, h_s = text.split("x", 1)
                w, h = int(w_s), int(h_s)
                if w > 0 and h > 0:
                    # 偶数，便于 yuv420p
                    return w - (w % 2), h - (h % 2)
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_GAP_WIDTH, DEFAULT_GAP_HEIGHT


def _make_black_gap(
    ffmpeg: str,
    out_path: str,
    *,
    duration_sec: float,
    width: int,
    height: int,
) -> None:
    """生成黑场 + 静音（统一分辨率，便于 concat）。"""
    dur = max(0.05, float(duration_sec))
    proc = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:d={dur:.3f}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            f"{dur:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-b:a",
            "128k",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            out_path,
        ],
        capture_output=True,
        timeout=max(60, int(dur * 4) + 30),
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
        raise ValueError(f"黑场生成失败: {err or 'ffmpeg error'}")
    if not Path(out_path).is_file() or Path(out_path).stat().st_size <= 0:
        raise ValueError("黑场结果为空")


def _extract_scaled_clip(
    ffmpeg: str,
    video_path: str,
    out_path: str,
    *,
    start_sec: float,
    end_sec: float,
    width: int,
    height: int,
) -> None:
    """裁切并缩放到统一分辨率 + 立体声 AAC（无音轨则补静音）。"""
    start = max(0.0, float(start_sec))
    end = max(start + 0.05, float(end_sec))
    dur = min(MAX_USER_TRIM_DURATION_SEC, end - start)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
    )
    timeout_sec = max(180, int(dur * 4) + 60)
    # 优先保留原音轨；失败再补静音
    cmd_with_audio = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        video_path,
        "-t",
        f"{dur:.3f}",
        "-vf",
        vf,
        "-af",
        "aresample=async=1:first_pts=0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        out_path,
    ]
    proc = subprocess.run(
        cmd_with_audio, capture_output=True, timeout=timeout_sec, check=False
    )
    if proc.returncode == 0 and Path(out_path).is_file() and Path(out_path).stat().st_size > 0:
        return

    cmd_silent = [
        ffmpeg,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        video_path,
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t",
        f"{dur:.3f}",
        "-vf",
        vf,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-b:a",
        "128k",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        "-movflags",
        "+faststart",
        out_path,
    ]
    proc2 = subprocess.run(
        cmd_silent, capture_output=True, timeout=timeout_sec, check=False
    )
    if proc2.returncode != 0 or not Path(out_path).is_file() or Path(out_path).stat().st_size <= 0:
        err = (proc2.stderr or proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise ValueError(f"片段归一化导出失败: {err or 'ffmpeg error'}")


def _mix_timeline_audio(
    ffmpeg: str,
    video_path: str,
    audio_parts: list[tuple[str, float, float, float]],
    out_path: str,
    *,
    total_dur: float,
) -> None:
    """将多段音频按 startSec 叠到成片上（adelay + amix）。

    audio_parts: (file_path, in_sec, out_sec, start_sec含pad前的媒体起点)
    """
    if not audio_parts:
        raise ValueError("无音轨")
    # 输入：0=视频，其后为各音频
    cmd: list[str] = [ffmpeg, "-y", "-i", video_path]
    for path, in_v, out_v, _start in audio_parts:
        cmd += ["-ss", f"{in_v:.3f}", "-t", f"{max(0.05, out_v - in_v):.3f}", "-i", path]

    n = len(audio_parts)
    filters: list[str] = []
    mix_labels: list[str] = ["[0:a]"]
    has_video_a = True
    # 若视频无音轨，只用外部音
    for i, (_p, _in, _out, start) in enumerate(audio_parts):
        delay_ms = max(0, int(round(start * 1000)))
        idx = i + 1
        filters.append(
            f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms}[a{i}]"
        )
        mix_labels.append(f"[a{i}]")

    # 尝试混入视频原音；失败时仅外部音（通过二次命令兜底）
    inputs_n = 1 + n
    mix_in = "".join(mix_labels)
    filters.append(
        f"{mix_in}amix=inputs={1 + n}:duration=longest:dropout_transition=0:normalize=0[aout]"
    )
    fc = ";".join(filters)
    cmd += [
        "-filter_complex",
        fc,
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-t",
        f"{max(0.05, total_dur):.3f}",
        "-movflags",
        "+faststart",
        out_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=max(180, int(total_dur * 4) + 60), check=False)
    if proc.returncode == 0 and Path(out_path).is_file() and Path(out_path).stat().st_size > 0:
        return

    # 兜底：视频可能无音轨，只混外部音
    filters2: list[str] = []
    labels2: list[str] = []
    cmd2: list[str] = [ffmpeg, "-y", "-i", video_path]
    for path, in_v, out_v, _start in audio_parts:
        cmd2 += ["-ss", f"{in_v:.3f}", "-t", f"{max(0.05, out_v - in_v):.3f}", "-i", path]
    for i, (_p, _in, _out, start) in enumerate(audio_parts):
        delay_ms = max(0, int(round(start * 1000)))
        idx = i + 1
        filters2.append(
            f"[{idx}:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms}[a{i}]"
        )
        labels2.append(f"[a{i}]")
    mix2 = "".join(labels2)
    filters2.append(f"{mix2}amix=inputs={n}:duration=longest:dropout_transition=0:normalize=0[aout]")
    cmd2 += [
        "-filter_complex",
        ";".join(filters2),
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-t",
        f"{max(0.05, total_dur):.3f}",
        "-shortest",
        "-movflags",
        "+faststart",
        out_path,
    ]
    proc2 = subprocess.run(
        cmd2, capture_output=True, timeout=max(180, int(total_dur * 4) + 60), check=False
    )
    if proc2.returncode != 0 or not Path(out_path).is_file():
        err = (proc2.stderr or proc.stderr or b"").decode("utf-8", errors="replace")[:500]
        raise ValueError(f"音轨混合失败: {err or 'ffmpeg error'}")
    _ = has_video_a
    _ = inputs_n


async def compose_project_video_assets(
    db: AsyncSession,
    *,
    project_id: str,
    clips: list[dict[str, Any]],
    audio_clips: list[dict[str, Any]] | None = None,
    title: str | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """多轨 trim + 压平 + 空隙黑场 + 可选音轨混音 + concat → OSS 新视频素材。

    on_progress: 可选 async (pct: float, message: str) -> None，供异步任务上报进度。
    """
    async def _progress(pct: float, message: str) -> None:
        if on_progress is None:
            return
        try:
            await on_progress(pct, message)
        except Exception:  # noqa: BLE001
            logger.debug("video_compose on_progress ignored", exc_info=True)

    await _progress(2, "校验时间线…")
    pid = require_entity_id(project_id)
    result = await db.execute(select(Project).filter(Project.id == pid))
    project = result.scalar_one_or_none()
    if not project:
        fail(ErrorCode.PROJECT_NOT_FOUND)

    if not isinstance(clips, list) or not clips:
        fail(ErrorCode.VALIDATION_ERROR, message="至少需要 1 个片段")
    if len(clips) > MAX_COMPOSE_CLIPS:
        fail(
            ErrorCode.VALIDATION_ERROR,
            message=f"最多拼接 {MAX_COMPOSE_CLIPS} 个片段",
        )

    normalized_input: list[dict[str, Any]] = []
    for i, raw in enumerate(clips):
        if not isinstance(raw, dict):
            fail(ErrorCode.VALIDATION_ERROR, message=f"第 {i + 1} 个片段格式无效")
        asset_id = str(
            raw.get("videoAssetId") or raw.get("video_asset_id") or ""
        ).strip()
        if not asset_id:
            fail(ErrorCode.VALIDATION_ERROR, message=f"第 {i + 1} 个片段缺少 videoAssetId")
        try:
            in_v = float(raw.get("inSec", raw.get("in_sec")))
            out_v = float(raw.get("outSec", raw.get("out_sec")))
        except (TypeError, ValueError):
            fail(ErrorCode.VALIDATION_ERROR, message=f"第 {i + 1} 个片段入出点无效")
        if in_v < 0 or out_v <= in_v:
            fail(ErrorCode.VALIDATION_ERROR, message=f"第 {i + 1} 个片段出点必须大于入点")
        clip_dur = out_v - in_v
        if clip_dur < MIN_USER_TRIM_DURATION_SEC:
            fail(
                ErrorCode.VALIDATION_ERROR,
                message=f"第 {i + 1} 个片段至少 {MIN_USER_TRIM_DURATION_SEC:g} 秒",
            )
        if clip_dur > MAX_USER_TRIM_DURATION_SEC + 0.05:
            fail(
                ErrorCode.VALIDATION_ERROR,
                message=f"第 {i + 1} 个片段最长 {MAX_USER_TRIM_DURATION_SEC:g} 秒",
            )
        start_raw = raw.get("startSec", raw.get("start_sec"))
        track_raw = raw.get("trackIndex", raw.get("track_index"))
        item: dict[str, Any] = {
            "assetId": asset_id,
            "inSec": in_v,
            "outSec": out_v,
            "padBeforeSec": max(
                0.0, float(raw.get("padBeforeSec", raw.get("pad_before_sec")) or 0.0)
            ),
            "padAfterSec": max(
                0.0, float(raw.get("padAfterSec", raw.get("pad_after_sec")) or 0.0)
            ),
        }
        if start_raw is not None:
            try:
                item["startSec"] = float(start_raw)
            except (TypeError, ValueError):
                fail(ErrorCode.VALIDATION_ERROR, message=f"第 {i + 1} 个片段 startSec 无效")
        if track_raw is not None:
            try:
                item["trackIndex"] = int(track_raw)
            except (TypeError, ValueError):
                fail(ErrorCode.VALIDATION_ERROR, message=f"第 {i + 1} 个片段 trackIndex 无效")
        normalized_input.append(item)

    flat = _flatten_multitrack(normalized_input)
    if not flat:
        fail(ErrorCode.VALIDATION_ERROR, message="压平后无有效片段")

    # 过滤过短 gap；过短 video 已在输入校验
    clean: list[dict[str, Any]] = []
    for seg in flat:
        if seg["kind"] == "gap":
            if seg["duration"] < 0.05:
                continue
            clean.append(seg)
        else:
            if seg["outSec"] - seg["inSec"] < MIN_USER_TRIM_DURATION_SEC - 0.02:
                # 压平切出来的碎段过短则跳过
                continue
            clean.append(seg)
    if not any(s["kind"] == "video" for s in clean):
        fail(ErrorCode.VALIDATION_ERROR, message="压平后无有效视频段")

    total_dur = sum(float(s["duration"]) for s in clean)
    if total_dur > MAX_COMPOSE_TOTAL_SEC + 0.05:
        fail(
            ErrorCode.VALIDATION_ERROR,
            message=f"拼接总时长不能超过 {MAX_COMPOSE_TOTAL_SEC:g} 秒",
        )

    # 可见视频段数量限制（gap 不计入用户片段配额，但限制总段数防止爆炸）
    video_seg_count = sum(1 for s in clean if s["kind"] == "video")
    if video_seg_count > MAX_COMPOSE_CLIPS * 3:
        fail(ErrorCode.VALIDATION_ERROR, message="压平后片段过多，请简化时间线")

    folder = project_storage_folder(project)
    storage = get_canvas_storage()
    ffmpeg, ffprobe = require_ffmpeg()

    temp_paths: list[str] = []
    segment_paths: list[str] = []
    out_path = ""
    source_cache: dict[str, str] = {}

    async def _ensure_source(asset_id: str) -> str:
        if asset_id in source_cache:
            return source_cache[asset_id]
        asset = await get_project_asset(db, project_id, asset_id, sync_oss=False)
        if not asset:
            fail(ErrorCode.ASSET_NOT_FOUND, message=f"源视频不存在: {asset_id}")
        cat = str(asset.get("category") or "")
        if cat not in ("video", "audio"):
            fail(ErrorCode.BAD_REQUEST, message="素材须为视频或音频")
        oss_key = str(asset.get("ossKey") or asset.get("oss_key") or "").strip()
        if not oss_key:
            fail(ErrorCode.BAD_REQUEST, message="素材缺少存储路径")
        try:
            media_bytes = await asyncio.to_thread(storage.get_bytes, oss_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("video_compose read failed asset=%s: %s", asset_id, exc)
            fail(ErrorCode.BAD_REQUEST, message="无法读取源素材")
        if not media_bytes:
            fail(ErrorCode.BAD_REQUEST, message="源素材内容为空")
        if len(media_bytes) > MAX_VIDEO_BYTES:
            fail(ErrorCode.BAD_REQUEST, message="源素材超过 200MB")
        suffix = _suffix_from_asset(asset)
        default_suf = ".mp3" if cat == "audio" else ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix or default_suf, delete=False) as vf:
            vf.write(media_bytes)
            video_path = vf.name
        temp_paths.append(video_path)
        source_cache[asset_id] = video_path
        return video_path

    try:
        await _progress(8, "准备素材…")
        # 先探测首个视频分辨率，避免片头黑场与后续片段尺寸不一致
        first_video_asset = next(
            (str(s["assetId"]) for s in clean if s["kind"] == "video"),
            "",
        )
        if first_video_asset:
            first_path = await _ensure_source(first_video_asset)
            target_w, target_h = await asyncio.to_thread(
                _probe_video_size, ffprobe, first_path
            )
        else:
            target_w, target_h = DEFAULT_GAP_WIDTH, DEFAULT_GAP_HEIGHT

        seg_total = max(1, len(clean))
        for idx, seg in enumerate(clean):
            # 切段阶段约占 10%→70%
            pct = 10 + (idx / seg_total) * 60
            await _progress(pct, f"处理片段 {idx + 1}/{seg_total}…")
            if seg["kind"] == "gap":
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as gf:
                    gap_path = gf.name
                temp_paths.append(gap_path)
                try:
                    await asyncio.to_thread(
                        _make_black_gap,
                        ffmpeg,
                        gap_path,
                        duration_sec=float(seg["duration"]),
                        width=target_w,
                        height=target_h,
                    )
                except ValueError as exc:
                    fail(ErrorCode.BAD_REQUEST, message=str(exc))
                segment_paths.append(gap_path)
                continue

            asset_id = str(seg["assetId"])
            in_v = float(seg["inSec"])
            out_v = float(seg["outSec"])
            video_path = await _ensure_source(asset_id)
            duration = await asyncio.to_thread(probe_duration_sec, video_path, ffprobe)
            if in_v >= duration - 0.05:
                fail(ErrorCode.VALIDATION_ERROR, message=f"第 {idx + 1} 段入点超出片长")
            end_clamped = min(out_v, duration)
            if end_clamped - in_v < 0.05:
                fail(ErrorCode.VALIDATION_ERROR, message=f"第 {idx + 1} 段有效时长过短")

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as sf:
                seg_path = sf.name
            temp_paths.append(seg_path)
            try:
                await asyncio.to_thread(
                    _extract_scaled_clip,
                    ffmpeg,
                    video_path,
                    seg_path,
                    start_sec=in_v,
                    end_sec=end_clamped,
                    width=target_w,
                    height=target_h,
                )
            except ValueError as exc:
                fail(ErrorCode.BAD_REQUEST, message=str(exc))
            segment_paths.append(seg_path)

        if len(segment_paths) == 1:
            probe_path = segment_paths[0]
            composed_bytes = Path(probe_path).read_bytes()
        else:
            await _progress(72, "拼接片段…")
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as of:
                out_path = of.name
            temp_paths.append(out_path)
            try:
                await asyncio.to_thread(
                    _concat_clip_files,
                    ffmpeg,
                    segment_paths,
                    out_path,
                )
            except ValueError as exc:
                fail(ErrorCode.BAD_REQUEST, message=str(exc))
            probe_path = out_path
            composed_bytes = Path(out_path).read_bytes()

        # 可选：叠独立音轨（媒体起点 = startSec + padBefore）
        if audio_clips:
            await _progress(82, "混合音轨…")
            audio_parts: list[tuple[str, float, float, float]] = []
            for ai, araw in enumerate(audio_clips):
                if not isinstance(araw, dict):
                    continue
                aid = str(
                    araw.get("audioAssetId") or araw.get("audio_asset_id") or ""
                ).strip()
                if not aid:
                    continue
                try:
                    ain = float(araw.get("inSec", araw.get("in_sec")))
                    aout = float(araw.get("outSec", araw.get("out_sec")))
                    astart = float(araw.get("startSec", araw.get("start_sec")) or 0.0)
                    apad = max(
                        0.0,
                        float(araw.get("padBeforeSec", araw.get("pad_before_sec")) or 0.0),
                    )
                except (TypeError, ValueError):
                    fail(ErrorCode.VALIDATION_ERROR, message=f"音轨第 {ai + 1} 段参数无效")
                if aout - ain < 0.05:
                    continue
                apath = await _ensure_source(aid)
                audio_parts.append((apath, ain, aout, astart + apad))
            if audio_parts:
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as mf:
                    mix_path = mf.name
                temp_paths.append(mix_path)
                try:
                    await asyncio.to_thread(
                        _mix_timeline_audio,
                        ffmpeg,
                        probe_path,
                        audio_parts,
                        mix_path,
                        total_dur=float(total_dur),
                    )
                    probe_path = mix_path
                    composed_bytes = Path(mix_path).read_bytes()
                except ValueError as exc:
                    fail(ErrorCode.BAD_REQUEST, message=str(exc))

        if len(composed_bytes) > MAX_VIDEO_BYTES:
            fail(ErrorCode.BAD_REQUEST, message="拼接结果超过 200MB")

        composed_dur = await asyncio.to_thread(probe_duration_sec, probe_path, ffprobe)
    finally:
        for p in temp_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass

    await _progress(90, "上传成片…")
    thumb_data = await asyncio.to_thread(extract_video_thumbnail_jpeg, composed_bytes, "mp4")
    total_size = len(composed_bytes) + (len(thumb_data) if thumb_data else 0)
    await assert_storage_quota_for_project(db, project_id, total_size)

    clip_id = new_asset_id()
    rel_path = f"assets/video/{clip_id}.mp4"
    try:
        stored = await asyncio.to_thread(
            storage.put,
            project_id,
            rel_path,
            composed_bytes,
            "video/mp4",
            storage_folder=folder,
        )
    except StorageWriteError as exc:
        fail(ErrorCode.STORAGE_WRITE_FAILED, message=f"拼接成片存储失败: {exc}")

    thumb_url = normalize_browser_storage_url(stored.file_url, oss_key=stored.oss_key)
    thumb_key = ""
    if thumb_data:
        thumb_rel = f"assets/image/{clip_id}_thumb.jpg"
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
            logger.warning("video_compose thumbnail upload failed for %s", clip_id)

    video_count = sum(1 for s in clean if s["kind"] == "video")
    default_title = "剪辑成片" if video_count > 1 else "剪辑"
    record = {
        "id": clip_id,
        "projectId": project_id,
        "title": (title or "").strip() or default_title,
        "category": "video",
        "subcategory": "剪辑",
        "fileUrl": stored.file_url,
        "thumbnailUrl": thumb_url,
        "fileType": "video/mp4",
        "fileSize": len(composed_bytes),
        "ossKey": stored.oss_key,
        "source": "video_compose",
        "createdAt": cst_iso_now(),
    }
    if thumb_key:
        record["thumbnailOssKey"] = thumb_key

    saved = await insert_project_asset(db, project_id, record, sync_oss=False)
    await invalidate_manifest_cache(project_id)

    await _progress(100, "拼接完成")
    return {
        "asset": saved,
        "durationSec": round(float(composed_dur) if composed_dur else total_dur, 3),
        "clipCount": video_count,
    }
