#!/usr/bin/env python3
"""Print MySQL table row counts (uses DATABASE_URL env)."""

from __future__ import annotations

import os

from app.scripts.migrate_pg_to_mysql import TABLES_IN_ORDER, _mysql_connect


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL required")
    my = _mysql_connect(url)
    try:
        c = my.cursor()
        print(f"{'table':<28} {'rows':>8}")
        print("-" * 40)
        for table in TABLES_IN_ORDER:
            c.execute(f"SELECT COUNT(*) FROM `{table}`")
            n = int(c.fetchone()[0])
            print(f"{table:<28} {n:>8}")
    finally:
        my.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
