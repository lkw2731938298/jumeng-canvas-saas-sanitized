#!/usr/bin/env python3
"""一次性将 MySQL 中 UTC naive 时间列 +8h 转为东八区墙钟（生产迁移前请备份）。

用法（RDS 内网或本机有 DATABASE_URL 时）:
  python canvas/scripts/migrate_timestamps_utc_to_cst.py --dry-run
  python canvas/scripts/migrate_timestamps_utc_to_cst.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# 允许从仓库根目录运行
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "packages", "api"))

TABLE_COLUMNS: dict[str, list[str]] = {
    "generation_jobs": [
        "created_at",
        "started_at",
        "completed_at",
        "anomaly_detected_at",
        "last_admin_action_at",
    ],
    "generation_job_admin_actions": ["created_at"],
    "users": ["created_at", "last_login_at"],
    "projects": ["created_at", "updated_at"],
    "workflows": ["created_at", "updated_at"],
    "project_assets": ["created_at"],
    "credit_lots": ["expires_at", "created_at", "updated_at"],
    "credit_reservations": ["created_at", "updated_at"],
    "credit_transactions": ["created_at"],
    "credit_activities": ["starts_at", "ends_at", "created_at", "updated_at"],
    "credit_activity_claims": ["claimed_at"],
    "user_subscriptions": ["current_period_start", "current_period_end", "created_at", "updated_at"],
    "subscription_grants": ["granted_at"],
    "subscription_plans": ["created_at", "updated_at"],
    "auth_events": ["created_at"],
}

# 东八区代码上线时刻（UTC 墙钟 naive）；仅迁移此时间之前写入的 UTC 数据，且 +8 后不会重复命中
UTC_CODE_DEPLOY_CUTOFF = "2026-07-03 03:39:00"


def _update_sql(table: str, col: str) -> str:
    return (
        f"UPDATE `{table}` SET `{col}` = DATE_ADD(`{col}`, INTERVAL 8 HOUR) "
        f"WHERE `{col}` IS NOT NULL AND `{col}` < '{UTC_CODE_DEPLOY_CUTOFF}';"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate UTC naive DATETIME columns to CST (+8h)")
    parser.add_argument("--apply", action="store_true", help="Execute UPDATE (default: dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL only")
    args = parser.parse_args()
    dry_run = not args.apply or args.dry_run

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
        database=parsed.path.lstrip("/"),
        charset="utf8mb4",
        autocommit=False,
    )

    statements: list[str] = []
    # 高争用表放最后
    table_order = sorted(TABLE_COLUMNS.keys(), key=lambda t: 1 if t == "generation_jobs" else 0)
    for table in table_order:
        for col in TABLE_COLUMNS[table]:
            statements.append(_update_sql(table, col))

    failures = 0
    with conn.cursor() as cur:
        cur.execute("SET SESSION innodb_lock_wait_timeout = 120")
        for sql in statements:
            print(sql)
            if dry_run:
                continue
            for attempt in range(1, 4):
                try:
                    cur.execute(sql)
                    conn.commit()
                    print(f"  -> {cur.rowcount} rows")
                    break
                except pymysql.err.OperationalError as exc:
                    conn.rollback()
                    if exc.args and exc.args[0] == 1205 and attempt < 3:
                        print(f"  lock wait, retry {attempt}/3...")
                        time.sleep(5 * attempt)
                        continue
                    print(f"  FAILED: {exc}", file=sys.stderr)
                    failures += 1
                    break
    conn.close()

    if not dry_run and failures:
        print(f"\nCompleted with {failures} failed statement(s).", file=sys.stderr)
        return 1
    print("\nDone." if not dry_run else "\nDry-run only. Re-run with --apply to execute.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
