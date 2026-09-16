#!/usr/bin/env python3
"""Repair generation_jobs.started_at/completed_at stored as UTC naive while created_at is CST.

Detects rows where started_at is ~8h earlier than created_at (Worker wrote UTC before CST rollout).
"""

from __future__ import annotations

import argparse
import os
import sys

DETECT_SQL = """
SELECT COUNT(*) AS cnt
FROM generation_jobs
WHERE started_at IS NOT NULL
  AND created_at IS NOT NULL
  AND started_at < created_at
  AND TIMESTAMPDIFF(HOUR, started_at, created_at) BETWEEN 7 AND 10
"""

REPAIR_SQL = """
UPDATE generation_jobs
SET
  started_at = DATE_ADD(started_at, INTERVAL 8 HOUR),
  completed_at = CASE
    WHEN completed_at IS NOT NULL THEN DATE_ADD(completed_at, INTERVAL 8 HOUR)
    ELSE NULL
  END
WHERE started_at IS NOT NULL
  AND created_at IS NOT NULL
  AND started_at < created_at
  AND TIMESTAMPDIFF(HOUR, started_at, created_at) BETWEEN 7 AND 10
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--job-id", help="Repair a single job id only")
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    try:
        import pymysql
        from urllib.parse import urlparse
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
        autocommit=False,
    )

    single_sql_detect = """
SELECT COUNT(*) AS cnt FROM generation_jobs
WHERE id = %s AND started_at IS NOT NULL AND created_at IS NOT NULL
  AND started_at < created_at
  AND TIMESTAMPDIFF(HOUR, started_at, created_at) BETWEEN 7 AND 10
"""
    single_sql_repair = """
UPDATE generation_jobs
SET started_at = DATE_ADD(started_at, INTERVAL 8 HOUR),
    completed_at = CASE WHEN completed_at IS NOT NULL THEN DATE_ADD(completed_at, INTERVAL 8 HOUR) ELSE NULL END
WHERE id = %s AND started_at IS NOT NULL AND created_at IS NOT NULL
  AND started_at < created_at
  AND TIMESTAMPDIFF(HOUR, started_at, created_at) BETWEEN 7 AND 10
"""

    with conn.cursor() as cur:
        if args.job_id:
            cur.execute(single_sql_detect, (args.job_id,))
            cnt = int(cur.fetchone()[0])
            print(f"job {args.job_id}: {cnt} row(s) match UTC-skew pattern")
            if args.apply and cnt:
                cur.execute(single_sql_repair, (args.job_id,))
                conn.commit()
                print(f"  -> repaired {cur.rowcount} row(s)")
        else:
            cur.execute(DETECT_SQL)
            cnt = int(cur.fetchone()[0])
            print(f"Matched rows: {cnt}")
            print(REPAIR_SQL.strip())
            if args.apply and cnt:
                cur.execute(REPAIR_SQL)
                conn.commit()
                print(f"  -> repaired {cur.rowcount} row(s)")

    conn.close()
    if not args.apply:
        print("\nDry-run. Re-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
