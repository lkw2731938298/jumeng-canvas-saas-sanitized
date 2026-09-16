#!/usr/bin/env python3
"""Compare row counts between PostgreSQL source and MySQL target."""

from __future__ import annotations

import os
import sys

import psycopg2
import pymysql

from app.scripts.migrate_pg_to_mysql import TABLES_IN_ORDER, _mysql_connect


def compare(pg_url: str, mysql_url: str) -> int:
    pg = psycopg2.connect(pg_url)
    my = _mysql_connect(mysql_url)
    mismatches = 0
    try:
        pgc = pg.cursor()
        myc = my.cursor()
        print(f"{'table':<28} {'PG':>8} {'MySQL':>8} {'diff':>8}")
        print("-" * 56)
        for table in TABLES_IN_ORDER:
            pgc.execute(f"SELECT COUNT(*) FROM {table}")
            pg_n = int(pgc.fetchone()[0])
            myc.execute(f"SELECT COUNT(*) FROM `{table}`")
            my_n = int(myc.fetchone()[0])
            diff = pg_n - my_n
            mark = " OK" if diff == 0 else " !! "
            if diff != 0:
                mismatches += 1
            print(f"{table:<28} {pg_n:>8} {my_n:>8} {diff:>8}{mark}")
    finally:
        pg.close()
        my.close()
    return mismatches


def main() -> int:
    os.environ.setdefault("JUMENG_CANVAS_USE_DOTENV", "1")
    from app.core.config import get_settings

    pg_url = os.getenv(
        "PG_SOURCE_DATABASE_URL",
        "postgresql://canvas:change-me@127.0.0.1:5433/jumeng_canvas",
    )
    mysql_url = os.getenv("DATABASE_URL") or get_settings().database_url
    if len(sys.argv) > 1:
        pg_url = sys.argv[1]
    if len(sys.argv) > 2:
        mysql_url = sys.argv[2]
    mismatches = compare(pg_url, mysql_url)
    if mismatches:
        print(f"\n{mismatches} table(s) mismatched")
        return 1
    print("\nAll tables match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
