#!/usr/bin/env python3
"""一次性：把库内签权/旧域名媒体 URL 洗成 https://cdn.example.com/{oss_key}。

清洗范围（不改 oss_key / video_oss_key 本身）：
- skills.cover_url
- credit_activities.cover_url
- platform_settings.discover_page（JSON 内 url/imageUrl/videoUrl/avatarUrl）
- users.avatar_url（仅本站 OSS/proxy/签权）
- models.cover_url
- projects.cover_url（有 cover_oss_key 则按 key 生成）
- project_assets.thumbnail_url（若误存签权串）

用法（在 packages/api 下，加载 api.env）：
  .venv/bin/python -m app.scripts.migrate_urls_to_cdn --dry-run
  .venv/bin/python -m app.scripts.migrate_urls_to_cdn
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from app.scripts.db_util import execute, fetch_all, fetch_one, mysql_connection

logger = logging.getLogger("migrate_urls_to_cdn")

DEFAULT_CDN_BASE = "https://cdn.example.com"
_STORAGE_PREFIXES = ("Ihuabu", "canvas")
_MEDIA_JSON_KEYS = ("url", "imageUrl", "videoUrl", "avatarUrl", "coverUrl", "thumbnailUrl")


def _cdn_base(raw: str) -> str:
    return (raw or DEFAULT_CDN_BASE).rstrip("/")


def _rewrite_prefix(key: str, target_prefix: str = "Ihuabu") -> str:
    """canvas/ → Ihuabu/ 与现网 OSS_OBJECT_PREFIX 一致。"""
    bare = key.lstrip("/")
    target = target_prefix.rstrip("/")
    for prefix in _STORAGE_PREFIXES:
        head = f"{prefix}/"
        if bare.startswith(head):
            return f"{target}/{bare[len(head):]}"
    return bare


def oss_key_from_url(url: str) -> str | None:
    """从代理 URL、OSS 预签名、CDN 或裸 key 解析逻辑 OSS key。"""
    raw = str(url or "").strip()
    if not raw:
        return None
    if raw.startswith("/canvas/"):
        raw = raw[len("/canvas") :]

    if raw.startswith("/api/storage/object") or "key=" in raw:
        query = raw.split("?", 1)[-1] if "?" in raw else ""
        keys = parse_qs(query).get("key") or []
        if keys:
            key = unquote(keys[0])
            if any(key.startswith(f"{p}/") for p in _STORAGE_PREFIXES):
                return key

    if not raw.startswith("http://") and not raw.startswith("https://"):
        bare = raw.lstrip("/")
        if any(bare.startswith(f"{p}/") for p in _STORAGE_PREFIXES):
            return bare
        return None

    parsed = urlparse(raw)
    path_key = unquote((parsed.path or "").lstrip("/"))
    if any(path_key.startswith(f"{p}/") for p in _STORAGE_PREFIXES):
        return path_key
    return None


def to_cdn_url(url_or_key: str, *, cdn_base: str, target_prefix: str = "Ihuabu") -> str | None:
    """能解析 key 则返回 CDN URL；已是目标 CDN 前缀则原样（幂等）。"""
    raw = str(url_or_key or "").strip()
    if not raw:
        return None

    base = _cdn_base(cdn_base)
    if raw.startswith(f"{base}/"):
        # 已是 CDN，但仍校正 canvas→Ihuabu
        key = oss_key_from_url(raw)
        if not key:
            return raw
        rewritten = _rewrite_prefix(key, target_prefix)
        new_url = f"{base}/{rewritten}"
        return new_url if new_url != raw else None  # None = 无需改

    if raw.startswith("/uploads/"):
        return None  # 静态占位不改

    key = oss_key_from_url(raw)
    if not key:
        return None
    rewritten = _rewrite_prefix(key, target_prefix)
    return f"{base}/{rewritten}"


def _update_scalar_column(
    conn,
    *,
    table: str,
    column: str,
    id_column: str,
    cdn_base: str,
    dry_run: bool,
    only_site_media: bool = False,
) -> dict[str, int]:
    """逐行清洗单列 URL。only_site_media=True 时跳过无法解析的外链。"""
    rows = fetch_all(
        conn,
        f"SELECT `{id_column}` AS id, `{column}` AS url FROM `{table}` "
        f"WHERE `{column}` IS NOT NULL AND `{column}` != ''",
    )
    updated = 0
    skipped = 0
    failed = 0
    for row in rows:
        old = str(row.get("url") or "")
        new = to_cdn_url(old, cdn_base=cdn_base)
        if new is None:
            if only_site_media:
                # 外链头像等：保留
                skipped += 1
            else:
                # 无法解析：记日志不瞎改
                if "expires=" in old.lower() or "aliyuncs.com" in old.lower() or "/api/storage/" in old:
                    logger.warning("%s.%s id=%s unparseable: %s...", table, column, row["id"], old[:100])
                    failed += 1
                else:
                    skipped += 1
            continue
        if new == old:
            skipped += 1
            continue
        updated += 1
        if dry_run:
            logger.info("[dry-run] %s.%s id=%s\n  %s\n→ %s", table, column, row["id"], old[:120], new)
            continue
        execute(
            conn,
            f"UPDATE `{table}` SET `{column}` = %s WHERE `{id_column}` = %s",
            (new, row["id"]),
        )
    return {"updated": updated, "skipped": skipped, "failed": failed, "total": len(rows)}


def _walk_json_urls(node: Any, *, cdn_base: str) -> tuple[Any, int]:
    """递归替换 dict/list 中媒体 URL 字段；返回 (新节点, 变更数)。"""
    changed = 0
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _MEDIA_JSON_KEYS and isinstance(v, str) and v.strip():
                new = to_cdn_url(v, cdn_base=cdn_base)
                if new and new != v:
                    out[k] = new
                    changed += 1
                else:
                    out[k] = v
            else:
                child, n = _walk_json_urls(v, cdn_base=cdn_base)
                out[k] = child
                changed += n
        return out, changed
    if isinstance(node, list):
        items = []
        for item in node:
            child, n = _walk_json_urls(item, cdn_base=cdn_base)
            items.append(child)
            changed += n
        return items, changed
    return node, 0


def migrate_discover_page(conn, *, cdn_base: str, dry_run: bool) -> dict[str, int]:
    row = fetch_one(conn, "SELECT id, discover_page FROM platform_settings LIMIT 1")
    if not row or row.get("discover_page") is None:
        return {"updated": 0, "skipped": 1, "failed": 0, "total": 0}
    raw = row["discover_page"]
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("discover_page JSON invalid")
            return {"updated": 0, "skipped": 0, "failed": 1, "total": 1}
    elif isinstance(raw, dict):
        payload = raw
    else:
        return {"updated": 0, "skipped": 1, "failed": 0, "total": 1}

    new_payload, n = _walk_json_urls(payload, cdn_base=cdn_base)
    if n <= 0:
        return {"updated": 0, "skipped": 1, "failed": 0, "total": 1}
    if dry_run:
        logger.info("[dry-run] platform_settings.discover_page %d url(s) would change", n)
        return {"updated": n, "skipped": 0, "failed": 0, "total": 1}
    execute(
        conn,
        "UPDATE platform_settings SET discover_page = %s WHERE id = %s",
        (json.dumps(new_payload, ensure_ascii=False), row["id"]),
    )
    return {"updated": n, "skipped": 0, "failed": 0, "total": 1}


def migrate_project_covers(conn, *, cdn_base: str, dry_run: bool) -> dict[str, int]:
    """有 cover_oss_key 按 key 生成；否则尝试从 cover_url 解析。"""
    rows = fetch_all(
        conn,
        "SELECT id, cover_url, cover_oss_key FROM projects "
        "WHERE (cover_url IS NOT NULL AND cover_url != '') OR "
        "(cover_oss_key IS NOT NULL AND cover_oss_key != '')",
    )
    updated = 0
    skipped = 0
    failed = 0
    base = _cdn_base(cdn_base)
    for row in rows:
        key = str(row.get("cover_oss_key") or "").strip()
        if not key:
            key = oss_key_from_url(str(row.get("cover_url") or "")) or ""
        if not key:
            skipped += 1
            continue
        new = f"{base}/{_rewrite_prefix(key)}"
        old = str(row.get("cover_url") or "")
        if old == new:
            skipped += 1
            continue
        updated += 1
        if dry_run:
            logger.info("[dry-run] projects.cover_url id=%s → %s", row["id"], new)
            continue
        execute(conn, "UPDATE projects SET cover_url = %s WHERE id = %s", (new, row["id"]))
    return {"updated": updated, "skipped": skipped, "failed": failed, "total": len(rows)}


def migrate_thumbnail_urls(conn, *, cdn_base: str, dry_run: bool) -> dict[str, int]:
    """thumbnail_url 若存的是签权/代理 URL 则洗成 CDN；已是裸 key 或 /uploads/ 跳过。"""
    rows = fetch_all(
        conn,
        "SELECT id, thumbnail_url AS url FROM project_assets "
        "WHERE thumbnail_url IS NOT NULL AND thumbnail_url != '' "
        "AND (thumbnail_url LIKE 'http%%' OR thumbnail_url LIKE '/api/storage/%%')",
    )
    updated = 0
    skipped = 0
    failed = 0
    base = _cdn_base(cdn_base)
    for row in rows:
        old = str(row.get("url") or "")
        # 已是目标 CDN 且前缀无需校正 → 跳过（勿记 failed）
        if old.startswith(f"{base}/"):
            key = oss_key_from_url(old)
            if key and _rewrite_prefix(key) == key.lstrip("/"):
                skipped += 1
                continue
        new = to_cdn_url(old, cdn_base=cdn_base)
        if not new:
            logger.warning("project_assets.thumbnail_url id=%s unparseable: %s...", row["id"], old[:100])
            failed += 1
            continue
        if new == old:
            skipped += 1
            continue
        updated += 1
        if dry_run:
            logger.info("[dry-run] project_assets.thumbnail_url id=%s → %s", row["id"], new)
            continue
        execute(
            conn,
            "UPDATE project_assets SET thumbnail_url = %s WHERE id = %s",
            (new, row["id"]),
        )
    return {"updated": updated, "skipped": skipped, "failed": failed, "total": len(rows)}


def run(conn, *, cdn_base: str, dry_run: bool) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    stats["skills.cover_url"] = _update_scalar_column(
        conn, table="skills", column="cover_url", id_column="id", cdn_base=cdn_base, dry_run=dry_run
    )
    stats["credit_activities.cover_url"] = _update_scalar_column(
        conn,
        table="credit_activities",
        column="cover_url",
        id_column="id",
        cdn_base=cdn_base,
        dry_run=dry_run,
    )
    stats["models.cover_url"] = _update_scalar_column(
        conn, table="models", column="cover_url", id_column="id", cdn_base=cdn_base, dry_run=dry_run
    )
    stats["users.avatar_url"] = _update_scalar_column(
        conn,
        table="users",
        column="avatar_url",
        id_column="id",
        cdn_base=cdn_base,
        dry_run=dry_run,
        only_site_media=True,
    )
    stats["projects.cover_url"] = migrate_project_covers(conn, cdn_base=cdn_base, dry_run=dry_run)
    stats["project_assets.thumbnail_url"] = migrate_thumbnail_urls(
        conn, cdn_base=cdn_base, dry_run=dry_run
    )
    stats["platform_settings.discover_page"] = migrate_discover_page(
        conn, cdn_base=cdn_base, dry_run=dry_run
    )
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Migrate stored media URLs to CDN public URLs")
    p.add_argument("--database-url", default=None, help="mysql+aiomysql://...（默认读 settings）")
    p.add_argument("--cdn-base", default=DEFAULT_CDN_BASE, help="CDN 根，默认 https://cdn.example.com")
    p.add_argument("--dry-run", action="store_true", help="只打印将要修改的行，不写库")
    args = p.parse_args()

    with mysql_connection(args.database_url) as conn:
        stats = run(conn, cdn_base=args.cdn_base, dry_run=args.dry_run)

    print("migrate_urls_to_cdn:", json.dumps(stats, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("(dry-run — no writes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
