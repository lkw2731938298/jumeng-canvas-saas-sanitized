"""爆款复刻 · 镜头切分、关键帧与分段参考视频（ffmpeg）。

用场景变化检测切镜；切点过少时按固定间隔回退采样。
超长镜头会再按 SD2.0 参考视频上限（12s）切段。
产出：每镜 JPEG 关键帧 + 对应 mp4 片段（≤12s，供成片 r2v 参考）。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 单次拉片上限，避免超长视频撑爆上下文与算力
MAX_SHOTS = 24
MIN_SHOT_GAP_SEC = 1.0
# 成片最低 4s：过短镜并入前段，避免碎镜与不足 4s 的提交
MIN_SHOT_DURATION_SEC = 4.0
FALLBACK_INTERVAL_SEC = 4.0
SCENE_THRESHOLD = 0.26
SCENE_THRESHOLD_STRICT = 0.38
MAX_VIDEO_BYTES = 200 * 1024 * 1024
# Seedance 2.0 等多模态参考视频单段上限（秒）
MAX_REF_VIDEO_CLIP_SEC = 12.0
# 整片源视频上限（秒）：允许长于 12s，切镜后再分段作参考
MAX_VIRAL_REMAKE_SOURCE_DURATION_SEC = 180.0
# 兼容旧名：语义改为「单段参考上限」，勿再用于整片校验
MAX_VIRAL_REMAKE_DURATION_SEC = MAX_REF_VIDEO_CLIP_SEC
# 画布节点「剪辑」导出片段上限（秒）；与整片源上限对齐
MAX_USER_TRIM_DURATION_SEC = MAX_VIRAL_REMAKE_SOURCE_DURATION_SEC
MIN_USER_TRIM_DURATION_SEC = 0.3


@dataclass(frozen=True)
class ShotBoundary:
    """单个镜头边界（秒）。"""

    index: int
    start_sec: float
    end_sec: float


@dataclass(frozen=True)
class ShotSegment:
    """镜头片段：边界 + 关键帧 JPEG + 参考视频 clip（mp4 字节）。"""

    index: int
    start_sec: float
    end_sec: float
    jpeg_bytes: bytes
    clip_bytes: bytes


def require_ffmpeg() -> tuple[str, str]:
    """定位本机 ffmpeg / ffprobe，缺失则抛业务错误。"""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ValueError("服务器未安装 ffmpeg/ffprobe，无法进行镜头切分")
    return ffmpeg, ffprobe


# 兼容旧内部调用名
_require_ffmpeg = require_ffmpeg


def probe_duration_sec(video_path: str, ffprobe: str) -> float:
    """读取视频时长（秒）。"""
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
        raise ValueError(f"无法读取视频时长: {err or 'ffprobe error'}")
    raw = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    try:
        duration = float(raw)
    except ValueError as exc:
        raise ValueError(f"视频时长无效: {raw!r}") from exc
    if duration <= 0:
        raise ValueError("视频时长无效")
    return duration


def detect_scene_cuts(video_path: str, ffmpeg: str, *, threshold: float = SCENE_THRESHOLD) -> list[float]:
    """返回场景切换时间点（秒，不含 0）。"""
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            video_path,
            "-filter:v",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        timeout=300,
        check=False,
    )
    # showinfo 写在 stderr；非 0 仍可能有部分输出
    text = (proc.stderr or b"").decode("utf-8", errors="replace")
    times: list[float] = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", text):
        try:
            t = float(match.group(1))
        except ValueError:
            continue
        if t > 0.05:
            times.append(t)
    times.sort()
    return times


def _fallback_interval(duration: float) -> float:
    """短片更密采样，长片稍疏，避免镜数失控。"""
    if duration <= 12:
        return 2.0
    if duration <= 45:
        return FALLBACK_INTERVAL_SEC
    return 4.0


def build_shot_boundaries(duration: float, cut_times: list[float]) -> list[ShotBoundary]:
    """将切点合并为镜头区间；过密切点合并，过少则等距回退；过短镜并入前一镜。"""
    starts = [0.0]
    for t in cut_times:
        if t >= duration - 0.05:
            continue
        if t - starts[-1] >= MIN_SHOT_GAP_SEC:
            starts.append(t)

    if len(starts) < 2:
        # 场景切点不足：按时长自适应间隔采样
        starts = [0.0]
        interval = _fallback_interval(duration)
        t = interval
        while t < duration - 0.4 and len(starts) < MAX_SHOTS:
            starts.append(t)
            t += interval

    # 截断到上限（均匀抽稀）
    if len(starts) > MAX_SHOTS:
        idxs = [round(i * (len(starts) - 1) / (MAX_SHOTS - 1)) for i in range(MAX_SHOTS)]
        starts = [starts[i] for i in idxs]
        starts[0] = 0.0

    raw: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else duration
        if end - start < 0.2:
            continue
        raw.append((start, end))

    # 过短镜头并入前一段，减少碎镜
    merged: list[tuple[float, float]] = []
    for start, end in raw:
        if merged and (end - start) < MIN_SHOT_DURATION_SEC:
            prev_s, _ = merged[-1]
            merged[-1] = (prev_s, end)
        else:
            merged.append((start, end))

    boundaries = [
        ShotBoundary(index=i + 1, start_sec=s, end_sec=e) for i, (s, e) in enumerate(merged)
    ]
    if not boundaries:
        boundaries.append(ShotBoundary(index=1, start_sec=0.0, end_sec=max(duration, 1.0)))
    return boundaries


def split_boundaries_for_ref_limit(
    boundaries: list[ShotBoundary],
    *,
    max_clip_sec: float = MAX_REF_VIDEO_CLIP_SEC,
    max_shots: int = MAX_SHOTS,
) -> list[ShotBoundary]:
    """超长镜按参考视频上限再切段，保证每段 ≤ max_clip_sec（SD2.0）。"""
    pieces: list[tuple[float, float]] = []
    for b in boundaries:
        start = b.start_sec
        end = b.end_sec
        if end - start <= max_clip_sec + 0.05:
            pieces.append((start, end))
            continue
        t = start
        while t < end - 0.15:
            chunk_end = min(t + max_clip_sec, end)
            if chunk_end - t >= 0.3:
                pieces.append((t, chunk_end))
            t = chunk_end

    if len(pieces) > max_shots:
        # 镜数超限：均匀抽稀，避免拉片/成片爆炸
        idxs = [round(i * (len(pieces) - 1) / (max_shots - 1)) for i in range(max_shots)]
        pieces = [pieces[i] for i in idxs]

    # 尾段过短（<4s）并入前一段，保证成片时长可取 ≥4s
    merged_pieces: list[tuple[float, float]] = []
    for start, end in pieces:
        if merged_pieces and (end - start) < MIN_SHOT_DURATION_SEC:
            prev_s, _ = merged_pieces[-1]
            # 并入后仍不超过参考上限
            if end - prev_s <= max_clip_sec + 0.05:
                merged_pieces[-1] = (prev_s, end)
            else:
                merged_pieces.append((start, end))
        else:
            merged_pieces.append((start, end))

    return [
        ShotBoundary(index=i + 1, start_sec=s, end_sec=e)
        for i, (s, e) in enumerate(merged_pieces)
    ]


def extract_frame_at(video_path: str, ffmpeg: str, *, at_sec: float) -> bytes:
    """在指定秒数抽取一帧 JPEG。"""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as jf:
        frame_path = jf.name
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                f"{max(0.0, at_sec):.3f}",
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
            raise ValueError(f"关键帧提取失败@{at_sec:.2f}s: {err or 'ffmpeg error'}")
        data = Path(frame_path).read_bytes()
        if not data:
            raise ValueError(f"关键帧为空@{at_sec:.2f}s")
        if len(data) > 10 * 1024 * 1024:
            raise ValueError("关键帧超过 10MB")
        return data
    finally:
        try:
            Path(frame_path).unlink(missing_ok=True)
        except OSError:
            pass


def probe_video_size(video_path: str, ffprobe: str) -> tuple[int, int]:
    """读取视频画面宽高（像素）。"""
    # 末尾必须传入输入文件，否则 ffprobe 报 You have to specify one input file
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
            "csv=s=x:p=0",
            video_path,
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
        raise ValueError(f"无法读取视频尺寸: {err or 'ffprobe error'}")
    raw = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    parts = raw.split("x")
    if len(parts) != 2:
        raise ValueError(f"视频尺寸无效: {raw!r}")
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"视频尺寸无效: {raw!r}") from exc
    if w <= 0 or h <= 0:
        raise ValueError("视频尺寸无效")
    return w, h


def probe_has_audio_stream(video_path: str, ffprobe: str) -> bool:
    """是否存在音轨（用于音视频分离前校验）。"""
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            video_path,
        ],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        return False
    raw = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    return bool(raw)


def extract_audio_m4a_bytes(video_path: str, ffmpeg: str) -> bytes:
    """从视频抽出音轨为 m4a（AAC），无画面。"""
    with tempfile.NamedTemporaryFile(suffix=".m4a", delete=False) as af:
        out_path = af.name
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                video_path,
                "-vn",
                "-acodec",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                out_path,
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            raise ValueError(f"抽取音频失败: {err or 'ffmpeg error'}")
        data = Path(out_path).read_bytes()
        if not data:
            raise ValueError("抽取音频结果为空")
        if len(data) > MAX_VIDEO_BYTES:
            raise ValueError("抽取音频超过大小限制")
        return data
    finally:
        try:
            Path(out_path).unlink(missing_ok=True)
        except OSError:
            pass


def extract_silent_video_bytes(video_path: str, ffmpeg: str) -> bytes:
    """导出无声音频轨的视频（优先视频流 copy，失败则重编码）。"""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
        out_path = vf.name
    try:
        # 先尝试 copy：快且画质无损
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                video_path,
                "-an",
                "-c:v",
                "copy",
                "-movflags",
                "+faststart",
                out_path,
            ],
            capture_output=True,
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    video_path,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-movflags",
                    "+faststart",
                    "-pix_fmt",
                    "yuv420p",
                    out_path,
                ],
                capture_output=True,
                timeout=600,
                check=False,
            )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            raise ValueError(f"导出无声视频失败: {err or 'ffmpeg error'}")
        data = Path(out_path).read_bytes()
        if not data:
            raise ValueError("无声视频结果为空")
        if len(data) > MAX_VIDEO_BYTES:
            raise ValueError("无声视频超过 200MB")
        return data
    finally:
        try:
            Path(out_path).unlink(missing_ok=True)
        except OSError:
            pass


def crop_video_bytes(
    video_path: str,
    ffmpeg: str,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
) -> bytes:
    """按像素矩形裁剪整段视频为 mp4（重编码）。宽高须为正偶数。"""
    cw = max(2, int(w) // 2 * 2)
    ch = max(2, int(h) // 2 * 2)
    cx = max(0, int(x) // 2 * 2)
    cy = max(0, int(y) // 2 * 2)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as cf:
        out_path = cf.name
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                video_path,
                "-vf",
                f"crop={cw}:{ch}:{cx}:{cy}",
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
            timeout=600,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            raise ValueError(f"视频裁剪失败: {err or 'ffmpeg error'}")
        data = Path(out_path).read_bytes()
        if not data:
            raise ValueError("裁剪结果为空")
        if len(data) > MAX_VIDEO_BYTES:
            raise ValueError("裁剪视频超过 200MB")
        return data
    finally:
        try:
            Path(out_path).unlink(missing_ok=True)
        except OSError:
            pass


def extract_clip_bytes(
    video_path: str,
    ffmpeg: str,
    *,
    start_sec: float,
    end_sec: float,
    max_duration_sec: float | None = None,
    min_duration_sec: float = MIN_USER_TRIM_DURATION_SEC,
    strip_audio: bool = False,
) -> bytes:
    """导出 [start, end) 片段为 mp4（重编码，避免非关键帧 copy 失败）。

    默认时长上限为爆款参考片段 ``MAX_REF_VIDEO_CLIP_SEC``（12s）；
    画布用户剪辑可传入 ``MAX_USER_TRIM_DURATION_SEC``。
    ``strip_audio=True``：无音轨（出海运镜参考用，避免原片对白污染成片语音）。
    """
    start = max(0.0, float(start_sec))
    end = max(start, float(end_sec))
    cap = MAX_REF_VIDEO_CLIP_SEC if max_duration_sec is None else max(min_duration_sec, float(max_duration_sec))
    floor = max(0.05, float(min_duration_sec))
    dur = max(floor, min(end - start, cap))
    # 长片段重编码耗时随时长上升，超时按时长放宽
    timeout_sec = max(180, int(dur * 4) + 60)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as cf:
        clip_path = cf.name
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            video_path,
            "-t",
            f"{dur:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
        ]
        if strip_audio:
            cmd.extend(["-an"])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        cmd.extend(
            [
                "-movflags",
                "+faststart",
                "-pix_fmt",
                "yuv420p",
                clip_path,
            ]
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:500]
            raise ValueError(f"镜头片段导出失败@{start:.2f}s: {err or 'ffmpeg error'}")
        data = Path(clip_path).read_bytes()
        if not data:
            raise ValueError(f"镜头片段为空@{start:.2f}s")
        if len(data) > MAX_VIDEO_BYTES:
            raise ValueError("镜头片段超过 200MB")
        return data
    finally:
        try:
            Path(clip_path).unlink(missing_ok=True)
        except OSError:
            pass


def split_video_to_shot_segments(
    video_bytes: bytes, *, suffix: str = ".mp4", strip_audio: bool = False
) -> list[ShotSegment]:
    """对整段视频：切镜 →（超长镜再≤12s 分段）→ 每段关键帧 + 参考视频 clip。

    ``strip_audio``：出海本地化运镜参考去原片音轨，成片由模型按本地化台词重新生成语音。
    """
    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise ValueError("参考视频超过 200MB，无法拉片")
    if not video_bytes:
        raise ValueError("参考视频为空")

    ffmpeg, ffprobe = _require_ffmpeg()
    video_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix or ".mp4", delete=False) as vf:
            vf.write(video_bytes)
            video_path = vf.name

        duration = probe_duration_sec(video_path, ffprobe)
        if duration > MAX_VIRAL_REMAKE_SOURCE_DURATION_SEC + 0.05:
            raise ValueError(
                f"参考视频最长 {MAX_VIRAL_REMAKE_SOURCE_DURATION_SEC:g} 秒"
                f"（当前约 {duration:.1f} 秒），请裁剪后重试"
            )
        cuts = detect_scene_cuts(video_path, ffmpeg, threshold=SCENE_THRESHOLD)
        # 切点过密时用更严阈值重检，减少闪切噪点
        if len(cuts) > MAX_SHOTS * 2:
            cuts = detect_scene_cuts(video_path, ffmpeg, threshold=SCENE_THRESHOLD_STRICT)
        boundaries = build_shot_boundaries(duration, cuts)
        # SD2.0 参考视频单段上限：超长镜再切
        boundaries = split_boundaries_for_ref_limit(boundaries)
        logger.info(
            "shot_detection: duration=%.2fs cuts=%d shots=%d (clip≤%.0fs strip_audio=%s)",
            duration,
            len(cuts),
            len(boundaries),
            MAX_REF_VIDEO_CLIP_SEC,
            strip_audio,
        )

        segments: list[ShotSegment] = []
        for b in boundaries:
            # 取镜头前 15% 处，避免黑场/转场
            at = b.start_sec + max(0.05, (b.end_sec - b.start_sec) * 0.15)
            jpeg = extract_frame_at(video_path, ffmpeg, at_sec=at)
            clip = extract_clip_bytes(
                video_path,
                ffmpeg,
                start_sec=b.start_sec,
                end_sec=b.end_sec,
                strip_audio=strip_audio,
            )
            segments.append(
                ShotSegment(
                    index=b.index,
                    start_sec=b.start_sec,
                    end_sec=b.end_sec,
                    jpeg_bytes=jpeg,
                    clip_bytes=clip,
                )
            )
        return segments
    finally:
        if video_path:
            try:
                Path(video_path).unlink(missing_ok=True)
            except OSError:
                pass


def split_video_to_keyframes(video_bytes: bytes, *, suffix: str = ".mp4") -> list[ShotSegment]:
    """兼容旧入口：现返回含 clip 的 ShotSegment 列表。"""
    return split_video_to_shot_segments(video_bytes, suffix=suffix)
