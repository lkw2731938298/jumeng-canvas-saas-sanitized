"""Backfill OSS from local mirror for DB assets and any local canvas files."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.core.config import get_settings
from app.integrations.oss.canvas_storage import PROJECT_ROOT, get_canvas_storage
from app.integrations.oss.service import get_oss
from app.scripts.db_util import fetch_all, mysql_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _guess_content_type(path: Path, fallback: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or fallback


def _key_from_thumbnail_url(url: str | None, prefix: str) -> str | None:
    if not url or url.startswith("/uploads/"):
        return None
    heads = {prefix.rstrip("/"), "canvas"}
    for head in heads:
        if url.startswith(f"{head}/"):
            return url.replace(f"canvas/", f"{prefix.rstrip('/')}/", 1) if head == "canvas" and prefix != "canvas" else url
    parsed = urlparse(url)
    if parsed.path.endswith("/api/storage/object") or "key=" in url:
        qs = parse_qs(parsed.query)
        keys = qs.get("key") or []
        key = keys[0] if keys else None
        if key and key.startswith("canvas/") and prefix != "canvas":
            return key.replace("canvas/", f"{prefix.rstrip('/')}/", 1)
        return key
    path = (parsed.path or "").lstrip("/")
    for head in heads:
        if path.startswith(f"{head}/"):
            if head == "canvas" and prefix != "canvas":
                return path.replace("canvas/", f"{prefix.rstrip('/')}/", 1)
            return path
    return None


def _iter_local_mirror_files(prefix: str) -> list[tuple[str, Path]]:
    local_root = PROJECT_ROOT / "data" / "oss-local"
    items: list[tuple[str, Path]] = []
    target = prefix.rstrip("/")
    heads = [target]
    if target != "canvas":
        heads.append("canvas")
    seen: set[str] = set()
    for head in heads:
        base = local_root / head
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(base)).replace("\\", "/")
            oss_key = f"{target}/{rel}" if head == "canvas" and target != "canvas" else f"{head}/{rel}"
            if oss_key in seen:
                continue
            seen.add(oss_key)
            items.append((oss_key, path))
    return items


def _load_asset_rows() -> list[dict]:
    with mysql_connection() as conn:
        return fetch_all(
            conn,
            """
            SELECT CAST(id AS CHAR(36)) AS id, oss_key, file_type, thumbnail_url
            FROM project_assets
            ORDER BY created_at
            """,
        )


def backfill_key(storage, oss, oss_key: str, content_type: str) -> str:
    if oss.exists(oss_key):
        return "already"
    if storage.ensure_oss_object(oss_key, content_type=content_type):
        return "backfilled"
    settings = get_settings()
    prefix = (settings.oss_object_prefix or "canvas").rstrip("/")
    legacy_key = oss_key.replace(f"{prefix}/", "canvas/", 1) if prefix != "canvas" else oss_key
    for candidate in {oss_key, legacy_key}:
        local_path = PROJECT_ROOT / "data" / "oss-local" / candidate
        if local_path.is_file():
            break
    else:
        return "no_local"
    if oss.exists(oss_key):
        return "already"
    data = local_path.read_bytes()
    oss.bucket.put_object(oss_key, data, headers={"Content-Type": content_type})
    return "backfilled" if oss.exists(oss_key) else "failed"


async def run() -> int:
    settings = get_settings()
    storage = get_canvas_storage()
    oss = get_oss()
    prefix = (settings.oss_object_prefix or "canvas").rstrip("/")
    if get_settings().canvas_storage_local_only:
        logger.info("CANVAS_STORAGE_LOCAL_ONLY=true — skipping OSS backfill.")
        return 0
    if not oss.bucket:
        logger.error("OSS is not configured; cannot backfill.")
        return 1

    stats = {
        "already": 0,
        "backfilled": 0,
        "no_local": 0,
        "failed": 0,
    }

    def bump(result: str) -> None:
        stats[result] = stats.get(result, 0) + 1

    rows = _load_asset_rows()
    logger.info("Scanning %d project_assets rows…", len(rows))

    seen: set[str] = set()
    for row in rows:
        oss_key = row["oss_key"]
        seen.add(oss_key)
        bump(backfill_key(storage, oss, oss_key, row["file_type"] or "application/octet-stream"))

        thumb_key = _key_from_thumbnail_url(row["thumbnail_url"], prefix)
        if thumb_key and thumb_key not in seen:
            seen.add(thumb_key)
            bump(
                backfill_key(
                    storage,
                    oss,
                    thumb_key,
                    _guess_content_type(Path(thumb_key), "image/jpeg"),
                )
            )

    logger.info("Scanning local mirror files not in DB (prefix=%s)…", prefix)
    for oss_key, path in _iter_local_mirror_files(prefix):
        if oss_key in seen:
            continue
        seen.add(oss_key)
        bump(backfill_key(storage, oss, oss_key, _guess_content_type(path)))

    logger.info(
        "Done. already=%d backfilled=%d no_local=%d failed=%d",
        stats["already"],
        stats["backfilled"],
        stats["no_local"],
        stats["failed"],
    )
    return 0 if stats["failed"] == 0 else 2


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
