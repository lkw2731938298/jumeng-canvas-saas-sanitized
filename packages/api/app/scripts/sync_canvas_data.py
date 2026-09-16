#!/usr/bin/env python3
"""Export / import canvas users & projects; sync login users from main site DB (MySQL)."""

from __future__ import annotations

from ..core.datetime_util import now_cst_naive
import argparse
import json
import logging
import sys
import uuid
from datetime import datetime
from typing import Any

from app.scripts.db_util import execute, fetch_all, fetch_one, mysql_connection, normalize_value

logger = logging.getLogger(__name__)

TABLES = ("users", "projects", "workflows", "project_assets")

UUID_COLUMNS: dict[str, set[str]] = {
    "users": {"id"},
    "projects": {"id", "owner_id"},
    "workflows": {"id", "project_id"},
    "project_assets": {"id", "project_id"},
}

DATETIME_COLUMNS: dict[str, set[str]] = {
    "users": {"created_at", "last_login_at"},
    "projects": {"created_at", "updated_at"},
    "workflows": {"created_at", "updated_at"},
    "project_assets": {"created_at"},
}


def normalize_phone(raw: str | None) -> str:
    return "".join(ch for ch in (raw or "") if ch.isdigit())


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(type(value))


def coerce_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for col in UUID_COLUMNS.get(table, set()):
        if out.get(col):
            out[col] = str(out[col])
    for col in DATETIME_COLUMNS.get(table, set()):
        val = out.get(col)
        if isinstance(val, str) and val:
            out[col] = datetime.fromisoformat(val.replace("Z", "+00:00")).replace(tzinfo=None)
    return out


def export_canvas(conn) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        rows = fetch_all(conn, f"SELECT * FROM `{table}` ORDER BY 1")
        out[table] = [dict(r) for r in rows]
    return out


def upsert_user_row(conn, row: dict[str, Any], *, dry_run: bool) -> str:
    row = coerce_row("users", row)
    if not row.get("phone"):
        return "skipped"

    existing_phone = fetch_one(conn, "SELECT id FROM users WHERE phone = %s", (row["phone"],))
    if existing_phone and str(existing_phone["id"]) != str(row["id"]):
        owned = fetch_one(conn, "SELECT COUNT(*) AS c FROM projects WHERE owner_id = %s", (existing_phone["id"],))
        if owned and int(owned["c"]) > 0:
            return "phone_conflict"
        if not dry_run:
            execute(conn, "DELETE FROM users WHERE id = %s", (existing_phone["id"],))

    columns = list(row.keys())
    col_list = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in columns if c != "id")
    sql = (
        f"INSERT INTO users ({col_list}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    if not dry_run:
        execute(conn, sql, tuple(normalize_value(row[c]) for c in columns))
    return "ok"


def upsert_rows(conn, table: str, rows: list[dict[str, Any]], *, dry_run: bool) -> int:
    if not rows:
        return 0
    if table == "users":
        count = 0
        for raw in rows:
            result = upsert_user_row(conn, raw, dry_run=dry_run)
            if result == "ok":
                count += 1
            elif result == "phone_conflict":
                logger.warning("skip user %s: phone already owned projects in target", raw.get("phone"))
        return count

    columns = list(rows[0].keys())
    col_list = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"`{c}` = VALUES(`{c}`)" for c in columns if c != "id")
    sql = (
        f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    count = 0
    for raw in rows:
        row = coerce_row(table, raw)
        if dry_run:
            count += 1
            continue
        execute(conn, sql, tuple(normalize_value(row[c]) for c in columns))
        count += 1
    return count


def import_canvas(conn, payload: dict[str, list[dict[str, Any]]], *, dry_run: bool) -> dict[str, int]:
    stats: dict[str, int] = {}
    for table in TABLES:
        stats[table] = upsert_rows(conn, table, payload.get(table, []), dry_run=dry_run)
    return stats


def sync_main_users(
    main_conn,
    canvas_conn,
    *,
    dry_run: bool,
    update_password: bool,
) -> dict[str, int]:
    rows = fetch_all(
        main_conn,
        """
        SELECT id, phone_raw, password_hash, is_admin, locked, status_core, remark,
               registered_at, last_at, last_login_ip
        FROM admin_users
        WHERE phone_raw IS NOT NULL AND phone_raw <> ''
        ORDER BY registered_at
        """,
    )
    created = updated = skipped = 0
    for row in rows:
        phone = normalize_phone(row["phone_raw"])
        if len(phone) != 11:
            skipped += 1
            continue
        source_id = str(row["id"]).strip()
        password_hash = (row["password_hash"] or "").strip() or None
        is_active = not bool(row["locked"]) and str(row["status_core"] or "") in ("", "正常")
        role = "admin" if bool(row["is_admin"]) else "user"
        display_name = (row["remark"] or "").strip() or f"用户{phone[-4:]}"

        existing = fetch_one(
            canvas_conn,
            "SELECT id, password_hash, source_user_id, role FROM users WHERE phone = %s OR source_user_id = %s LIMIT 1",
            (phone, source_id),
        )
        if existing:
            sets: list[str] = []
            params: list[Any] = []
            if str(existing["source_user_id"]) != source_id:
                sets.append("source_user_id = %s")
                params.append(source_id)
            if update_password and password_hash:
                sets.append("password_hash = %s")
                params.append(password_hash)
            if role == "admin" and existing["role"] != "admin":
                sets.append("role = %s")
                params.append("admin")
            sets.extend(
                [
                    "display_name = COALESCE(NULLIF(display_name, ''), %s)",
                    "is_active = %s",
                    "last_login_at = COALESCE(last_login_at, %s)",
                    "last_login_ip = COALESCE(NULLIF(last_login_ip, ''), %s)",
                ]
            )
            params.extend([display_name, int(is_active), row["last_at"], row["last_login_ip"] or ""])
            params.append(existing["id"])
            if sets and not dry_run:
                execute(
                    canvas_conn,
                    f"UPDATE users SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
            updated += 1
            continue

        if dry_run:
            created += 1
            continue

        user_id = str(uuid.uuid4())
        execute(
            canvas_conn,
            """
            INSERT INTO users (
                id, source_user_id, phone, password_hash, display_name, role,
                compute_power, is_active, created_at, last_login_at, last_login_ip
            ) VALUES (%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s)
            """,
            (
                user_id,
                source_id,
                phone,
                password_hash,
                display_name,
                role,
                int(is_active),
                row["registered_at"] or now_cst_naive(),
                row["last_at"],
                row["last_login_ip"] or "",
            ),
        )
        created += 1

    return {"created": created, "updated": updated, "skipped": skipped}


def cmd_export(args: argparse.Namespace) -> int:
    with mysql_connection(args.source_db) as conn:
        payload = export_canvas(conn)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, default=json_default)
    for table in TABLES:
        print(f"exported {table}: {len(payload.get(table, []))}")
    print(f"wrote {args.out}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    with open(args.infile, encoding="utf-8") as fh:
        payload = json.load(fh)
    with mysql_connection(args.target_db) as conn:
        stats = import_canvas(conn, payload, dry_run=args.dry_run)
    print("import:", stats, "(dry-run)" if args.dry_run else "")
    return 0


def cmd_sync_main(args: argparse.Namespace) -> int:
    with mysql_connection(args.main_db) as main_conn, mysql_connection(args.target_db) as canvas_conn:
        stats = sync_main_users(
            main_conn,
            canvas_conn,
            dry_run=args.dry_run,
            update_password=not args.keep_canvas_password,
        )
    print("main users:", stats, "(dry-run)" if args.dry_run else "")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Sync canvas users/projects (MySQL)")
    sub = p.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export", help="Export canvas tables to JSON")
    exp.add_argument("--source-db", required=True)
    exp.add_argument("--out", required=True)
    exp.set_defaults(func=cmd_export)

    imp = sub.add_parser("import", help="Import canvas tables from JSON")
    imp.add_argument("--target-db", required=True)
    imp.add_argument("--infile", required=True)
    imp.add_argument("--dry-run", action="store_true")
    imp.set_defaults(func=cmd_import)

    main = sub.add_parser("sync-main-users", help="Upsert users from main site admin_users")
    main.add_argument("--main-db", required=True)
    main.add_argument("--target-db", required=True)
    main.add_argument("--dry-run", action="store_true")
    main.add_argument(
        "--keep-canvas-password",
        action="store_true",
        help="Do not overwrite canvas password_hash from main site",
    )
    main.set_defaults(func=cmd_sync_main)
    return p


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
