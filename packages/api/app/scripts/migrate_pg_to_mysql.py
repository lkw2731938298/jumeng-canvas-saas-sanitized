#!/usr/bin/env python3
"""Migrate canvas data from PostgreSQL to MySQL (one-shot)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pymysql
import psycopg2

TABLES_IN_ORDER = [
    "users",
    "user_sessions",
    "platform_settings",
    "subscription_plans",
    "projects",
    "workflows",
    "project_assets",
    "models",
    "credit_lots",
    "credit_reservations",
    "credit_transactions",
    "credit_activities",
    "credit_activity_claims",
    "user_subscriptions",
    "subscription_grants",
    "generation_jobs",
    "auth_events",
    "sms_login_codes",
    "model_runtime_slots",
]


def _normalize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (datetime, date)):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, bool):
        return int(value)
    return value


def _row_to_dict(columns: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    return {col: _normalize(val) for col, val in zip(columns, row, strict=True)}


def _pg_connect(url: str):
    return psycopg2.connect(url)


def _mysql_connect(url: str):
    # mysql+aiomysql://user:pass@host:port/db
    raw = url.replace("mysql+aiomysql://", "").replace("mysql://", "")
    userinfo, hostpart = raw.split("@", 1)
    user, password = userinfo.split(":", 1)
    host_port, database = hostpart.split("/", 1)
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 3306
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
    )


def migrate(*, pg_url: str, mysql_url: str, truncate: bool) -> None:
    pg = _pg_connect(pg_url)
    my = _mysql_connect(mysql_url)
    try:
        pg_cur = pg.cursor()
        my_cur = my.cursor()
        if truncate:
            my_cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in reversed(TABLES_IN_ORDER):
                my_cur.execute(f"TRUNCATE TABLE `{table}`")
            my_cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            my.commit()

        for table in TABLES_IN_ORDER:
            pg_cur.execute(f"SELECT * FROM {table}")
            rows = pg_cur.fetchall()
            if not rows:
                print(f"[skip] {table}: 0 rows")
                continue
            columns = [desc[0] for desc in pg_cur.description]
            placeholders = ", ".join(["%s"] * len(columns))
            col_sql = ", ".join(f"`{c}`" for c in columns)
            sql = f"INSERT INTO `{table}` ({col_sql}) VALUES ({placeholders})"
            payload = [
                tuple(_row_to_dict(columns, row)[c] for c in columns)
                for row in rows
            ]
            my_cur.executemany(sql, payload)
            my.commit()
            print(f"[ok] {table}: {len(rows)} rows")
    finally:
        pg.close()
        my.close()


def main() -> None:
    os.environ.setdefault("JUMENG_CANVAS_USE_DOTENV", "1")
    try:
        from app.core.config import get_settings

        default_mysql = get_settings().database_url
    except Exception:
        default_mysql = os.getenv("DATABASE_URL", "")

    parser = argparse.ArgumentParser(description="Migrate canvas PostgreSQL data to MySQL")
    parser.add_argument(
        "--pg-url",
        default=os.getenv(
            "PG_SOURCE_DATABASE_URL",
            "postgresql://canvas:change-me@127.0.0.1:5433/jumeng_canvas",
        ),
        help="PostgreSQL source URL",
    )
    parser.add_argument(
        "--mysql-url",
        default=default_mysql,
        help="MySQL target URL (mysql+aiomysql://...)",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate target tables before import",
    )
    args = parser.parse_args()
    if not args.mysql_url:
        print("DATABASE_URL / --mysql-url is required", file=sys.stderr)
        sys.exit(1)
    migrate(pg_url=args.pg_url, mysql_url=args.mysql_url, truncate=args.truncate)


if __name__ == "__main__":
    main()
