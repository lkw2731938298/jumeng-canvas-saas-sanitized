"""Shared MySQL helpers for canvas API scripts."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

import pymysql
from pymysql.cursors import DictCursor


def parse_mysql_url(url: str) -> dict[str, Any]:
    from urllib.parse import unquote

    raw = (url or "").replace("mysql+aiomysql://", "").replace("mysql://", "")
    userinfo, hostpart = raw.split("@", 1)
    user, password = userinfo.split(":", 1)
    host_port, database = hostpart.split("/", 1)
    if "?" in database:
        database = database.split("?", 1)[0]
    if ":" in host_port:
        host, port_s = host_port.split(":", 1)
        port = int(port_s)
    else:
        host, port = host_port, 3306
    return {
        "host": host,
        "port": port,
        "user": unquote(user),
        # DATABASE_URL 中 ! 等特殊字符常为百分号编码，连接前须还原
        "password": unquote(password),
        "database": unquote(database),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
        "autocommit": False,
    }


@contextmanager
def mysql_connection(url: str | None = None) -> Iterator[pymysql.Connection]:
    if url is None:
        from app.core.config import get_settings

        url = get_settings().database_url
    conn = pymysql.connect(**parse_mysql_url(url))
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return value
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return int(value)
    return value


def fetch_all(conn: pymysql.Connection, sql: str, params: tuple | list | None = None) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return list(cur.fetchall())


def fetch_one(conn: pymysql.Connection, sql: str, params: tuple | list | None = None) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


def execute(conn: pymysql.Connection, sql: str, params: tuple | list | None = None) -> int:
    with conn.cursor() as cur:
        return cur.execute(sql, params or ())


def delete_users_cascade(conn: pymysql.Connection, user_ids: list[str]) -> int:
    if not user_ids:
        return 0
    placeholders = ", ".join(["%s"] * len(user_ids))
    tables = [
        "user_sessions",
        "credit_reservations",
        "credit_activity_claims",
        "subscription_grants",
        "user_subscriptions",
        "credit_lots",
        "credit_transactions",
        "generation_jobs",
        "auth_events",
    ]
    for table in tables:
        execute(conn, f"DELETE FROM `{table}` WHERE user_id IN ({placeholders})", user_ids)
    return execute(conn, f"DELETE FROM `users` WHERE id IN ({placeholders})", user_ids)
