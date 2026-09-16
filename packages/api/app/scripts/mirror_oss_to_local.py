"""Mirror OSS canvas objects into data/oss-local (one-time migration to local-only mode)."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import oss2

from app.integrations.oss.canvas_storage import PROJECT_ROOT, get_canvas_storage
from app.integrations.oss.service import get_oss
from app.scripts.db_util import fetch_all, mysql_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _key_from_thumbnail_url(url: str | None) -> str | None:
    if not url or url.startswith("/uploads/"):
        return None
    if url.startswith("canvas/"):
        return url
    parsed = urlparse(url)
    if "key=" in url:
        keys = parse_qs(parsed.query).get("key") or []
        return keys[0] if keys else None
    path = (parsed.path or "").lstrip("/")
    if path.startswith("canvas/"):
        return path
    return None


def _collect_db_keys() -> set[str]:
    with mysql_connection() as conn:
        rows = fetch_all(conn, "SELECT oss_key, thumbnail_url FROM project_assets")
    keys: set[str] = set()
    for row in rows:
        if row["oss_key"]:
            keys.add(row["oss_key"])
        thumb = _key_from_thumbnail_url(row["thumbnail_url"])
        if thumb:
            keys.add(thumb)
    return keys


def _collect_workflow_keys() -> set[str]:
    with mysql_connection() as conn:
        rows = fetch_all(conn, "SELECT flow_json FROM workflows WHERE flow_json IS NOT NULL")
    keys: set[str] = set()
    for row in rows:
        raw = row["flow_json"] or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("_storage") == "oss":
            key = data.get("ossKey")
            if isinstance(key, str) and key:
                keys.add(key)
    return keys


def _collect_oss_keys() -> set[str]:
    oss = get_oss()
    if not oss.bucket:
        return set()
    keys: set[str] = set()
    for obj in oss2.ObjectIterator(oss.bucket, prefix="canvas/"):
        keys.add(obj.key)
    return keys


async def run() -> int:
    storage = get_canvas_storage()
    local_root = PROJECT_ROOT / "data" / "oss-local"

    db_keys = _collect_db_keys()
    oss_keys = _collect_oss_keys()
    wf_keys = _collect_workflow_keys()

    all_keys = db_keys | oss_keys | wf_keys
    logger.info(
        "Keys to check: %d (db=%d oss=%d workflows=%d)",
        len(all_keys),
        len(db_keys),
        len(oss_keys),
        len(wf_keys),
    )

    stats = {"already": 0, "mirrored": 0, "oss_miss": 0, "failed": 0}
    for key in sorted(all_keys):
        if (local_root / key).is_file():
            stats["already"] += 1
            continue
        if storage.ensure_local_object(key):
            stats["mirrored"] += 1
        elif key in oss_keys:
            stats["failed"] += 1
        else:
            stats["oss_miss"] += 1

    logger.info(
        "Done. already_local=%d mirrored=%d not_on_oss=%d mirror_failed=%d",
        stats["already"],
        stats["mirrored"],
        stats["oss_miss"],
        stats["failed"],
    )
    return 0 if stats["failed"] == 0 else 2


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
