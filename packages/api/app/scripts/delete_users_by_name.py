#!/usr/bin/env python3
"""Delete users by display_name on a given MySQL database."""

from __future__ import annotations

import argparse
import sys

from app.scripts.db_util import delete_users_cascade, fetch_all, mysql_connection


def delete_users(conn, name: str, *, dry_run: bool) -> None:
    rows = fetch_all(
        conn,
        """
        SELECT u.id, u.phone, u.display_name, u.role,
               (SELECT COUNT(*) FROM projects p WHERE p.owner_id = u.id) AS projects
        FROM users u
        WHERE u.display_name = %s
        ORDER BY u.created_at
        """,
        (name,),
    )
    if not rows:
        print(f"No users with display_name={name!r}")
        return

    print(f"Found {len(rows)} user(s):")
    for r in rows:
        print(f"  {r['id']}  {r['phone'] or '-'}  projects={r['projects']}")

    safe = [r for r in rows if int(r["projects"] or 0) == 0]
    skipped = len(rows) - len(safe)
    if skipped:
        print(f"Skip {skipped} user(s) with projects")

    if not safe:
        return

    ids = [str(r["id"]) for r in safe]
    if dry_run:
        print(f"[dry-run] Would delete {len(ids)} user(s)")
        return

    deleted = delete_users_cascade(conn, ids)
    print(f"Deleted {deleted} user row(s)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True, help="mysql+aiomysql://... or mysql://...")
    p.add_argument("--name", default="自主注册")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    with mysql_connection(args.db) as conn:
        delete_users(conn, args.name, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
