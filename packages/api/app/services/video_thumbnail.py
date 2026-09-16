"""Extract a JPEG poster frame from video bytes (requires ffmpeg in PATH)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_video_thumbnail_jpeg(data: bytes, ext: str = "mp4") -> bytes | None:
    if not data or not shutil.which("ffmpeg"):
        return None

    safe_ext = (ext or "mp4").lstrip(".").lower() or "mp4"
    if safe_ext not in {"mp4", "webm", "mov", "mkv", "avi", "m4v"}:
        safe_ext = "mp4"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / f"input.{safe_ext}"
            out = Path(tmp) / "thumb.jpg"
            src.write_bytes(data)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(src),
                    "-ss",
                    "0",
                    "-vframes",
                    "1",
                    "-q:v",
                    "3",
                    str(out),
                ],
                capture_output=True,
                timeout=120,
                check=True,
            )
            if not out.is_file():
                return None
            thumb = out.read_bytes()
            return thumb if thumb else None
    except Exception as exc:
        logger.warning("Video thumbnail extraction failed: %s", exc)
        return None
