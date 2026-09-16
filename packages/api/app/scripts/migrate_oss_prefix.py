#!/usr/bin/env python3
"""Rewrite OSS keys in DB from legacy prefix to OSS_OBJECT_PREFIX (e.g. canvas -> Ihuabu)."""

from __future__ import annotations

import argparse
import sys

from app.scripts.db_util import execute, mysql_connection


def migrate(conn, old_prefix: str, new_prefix: str, *, dry_run: bool) -> dict[str, int]:
    old = old_prefix.rstrip("/")
    new = new_prefix.rstrip("/")
    if old == new:
        return {"skipped": 1}

    stats: dict[str, int] = {}
    old_projects = f"{old}/projects/"
    new_projects = f"{new}/projects/"

    statements = [
        (
            "project_assets.oss_key",
            f"UPDATE project_assets SET oss_key = REPLACE(oss_key, '{old}/', '{new}/') WHERE oss_key LIKE '{old}/%'",
        ),
        (
            "project_assets.thumbnail_url",
            f"UPDATE project_assets SET thumbnail_url = REPLACE(thumbnail_url, '{old}/', '{new}/') "
            f"WHERE thumbnail_url LIKE '%{old}/%'",
        ),
        (
            "projects.cover_url",
            f"UPDATE projects SET cover_url = REPLACE(cover_url, '{old}/', '{new}/') WHERE cover_url LIKE '%{old}/%'",
        ),
        (
            "workflows.flow_json",
            f"UPDATE workflows SET flow_json = REPLACE(flow_json, '{old_projects}', '{new_projects}') "
            f"WHERE flow_json LIKE '%{old_projects}%'",
        ),
        (
            "generation_jobs.output_assets",
            f"UPDATE generation_jobs SET output_assets = CAST(REPLACE(CAST(output_assets AS CHAR), '{old}/', '{new}/') AS JSON) "
            f"WHERE CAST(output_assets AS CHAR) LIKE '%{old}/%'",
        ),
    ]

    for label, sql in statements:
        if dry_run:
            print(f"[dry-run] {label}: {sql[:120]}...")
            stats[label] = 0
            continue
        affected = execute(conn, sql)
        stats[label] = int(affected or 0)
    return stats


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target-db", required=True)
    p.add_argument("--from-prefix", default="canvas")
    p.add_argument("--to-prefix", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    with mysql_connection(args.target_db) as conn:
        stats = migrate(conn, args.from_prefix, args.to_prefix, dry_run=args.dry_run)
    print("migrate-prefix:", stats, "(dry-run)" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
