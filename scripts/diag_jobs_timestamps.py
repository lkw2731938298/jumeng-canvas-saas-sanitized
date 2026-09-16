#!/usr/bin/env python3
"""Diagnose generation_jobs created_at ordering (run on server with DATABASE_URL)."""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

QUERIES = [
    ("MAX_ID_CREATED", "SELECT MAX(id), MAX(created_at) FROM generation_jobs"),
    ("TOP_BY_ID", "SELECT id, status, created_at, completed_at FROM generation_jobs ORDER BY id DESC LIMIT 10"),
    ("TOP_BY_CREATED", "SELECT id, status, created_at, completed_at FROM generation_jobs ORDER BY created_at DESC LIMIT 10"),
    (
        "AFTER_0706_1650",
        "SELECT COUNT(*) FROM generation_jobs WHERE created_at > '2026-07-06 16:50:00'",
    ),
    (
        "ID_GE_10001",
        "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM generation_jobs WHERE id >= 10001",
    ),
    ("ADMIN_ACTIONS", "SELECT COUNT(*) FROM generation_job_admin_actions"),
    (
        "JOBS_WITH_ADMIN",
        "SELECT COUNT(*) FROM generation_jobs WHERE last_admin_action IS NOT NULL",
    ),
    (
        "COMPLETED_0706",
        "SELECT id, status, created_at, completed_at FROM generation_jobs "
        "WHERE DATE(completed_at) = '2026-07-06' ORDER BY completed_at DESC LIMIT 15",
    ),
    (
        "CREATED_0706_AFTER_14",
        "SELECT id, status, created_at, completed_at FROM generation_jobs "
        "WHERE created_at >= '2026-07-06 14:00:00' AND created_at < '2026-07-07 00:00:00' "
        "ORDER BY created_at DESC LIMIT 15",
    ),
]


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1
    try:
        import pymysql
    except ImportError:
        print("pip install pymysql", file=sys.stderr)
        return 1

    parsed = urlparse(database_url.replace("mysql+aiomysql://", "mysql://"))
    conn = pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=parsed.username,
        password=parsed.password,
        database=parsed.path.lstrip("/").split("?")[0],
        charset="utf8mb4",
    )
    cur = conn.cursor()
    for label, sql in QUERIES:
        print("===", label, "===")
        cur.execute(sql)
        for row in cur.fetchall():
            print(row)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
