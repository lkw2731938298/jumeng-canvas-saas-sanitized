#!/usr/bin/env python3
"""One-shot migration: UUID CHAR(36) primary keys → BIGINT UNSIGNED auto-increment.

Preserves OSS object paths via projects.storage_folder (legacy 32-char UUID hex folder).

Usage (from canvas/packages/api):
  python -m app.scripts.migrate_uuid_to_bigint --database-url mysql+pymysql://user:pass@host:3306/jumeng_canvas

Run BEFORE deploying API code that expects BIGINT IDs. Stop API/Worker during migration.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import uuid
from collections import defaultdict
from typing import Any

import pymysql

logger = logging.getLogger(__name__)

DEV_USER_UUID = "00000000-0000-0000-0000-000000000001"
USER_ID_START = 10001
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _parse_mysql_url(url: str) -> dict[str, Any]:
    raw = url.replace("mysql+aiomysql://", "").replace("mysql+pymysql://", "").replace("mysql://", "")
    userinfo, hostpart = raw.split("@", 1)
    user, password = userinfo.split(":", 1)
    host_port, database = hostpart.split("/", 1)
    database = database.split("?", 1)[0]
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 3306
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "autocommit": False,
    }


def _column_type(cur, table: str, column: str) -> str | None:
    cur.execute(
        """
        SELECT DATA_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return cur.fetchone() is not None


def _rewrite_json(value: Any, uuid_map: dict[str, int]) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in ("lotId", "lot_id") and isinstance(v, str) and v in uuid_map:
                out[k] = uuid_map[v]
            elif k in ("projectId", "workflowId", "userId", "assetId") and isinstance(v, str) and v in uuid_map:
                out[k] = uuid_map[v]
            else:
                out[k] = _rewrite_json(v, uuid_map)
        return out
    if isinstance(value, list):
        return [_rewrite_json(item, uuid_map) for item in value]
    if isinstance(value, str) and value in uuid_map:
        return uuid_map[value]
    return value


def _assign_user_ids(rows: list[tuple]) -> dict[str, int]:
    """rows: [(old_uuid, created_at), ...] ordered by created_at."""
    mapping: dict[str, int] = {}
    next_id = USER_ID_START
    for old_id, _created in rows:
        if old_id == DEV_USER_UUID:
            mapping[old_id] = 1
        else:
            mapping[old_id] = next_id
            next_id += 1
    return mapping


def _assign_sequential_ids(old_ids: list[str], *, start: int = 1) -> dict[str, int]:
    mapping: dict[str, int] = {}
    n = start
    for old_id in old_ids:
        mapping[old_id] = n
        n += 1
    return mapping


def _drop_index_if_exists(cur, table: str, index_name: str) -> None:
    try:
        cur.execute(f"ALTER TABLE `{table}` DROP INDEX `{index_name}`")
    except pymysql.err.OperationalError:
        pass


def _purge_unmapped_fk_rows(cur, table: str, column: str, uuid_map: dict[str, int]) -> int:
    if not _table_exists(cur, table):
        return 0
    deleted = 0
    cur.execute(
        f"SELECT CAST(id AS CHAR(36)), CAST(`{column}` AS CHAR(36)) "
        f"FROM `{table}` WHERE `{column}` IS NOT NULL"
    )
    for row_id, old_fk in cur.fetchall():
        if old_fk and old_fk not in uuid_map:
            cur.execute(f"DELETE FROM `{table}` WHERE id = %s", (row_id,))
            deleted += 1
    if deleted:
        logger.info("Deleted %s orphan row(s) from %s.%s", deleted, table, column)
    return deleted


def _dedupe_table_rows(cur, table: str, key_cols: list[str]) -> None:
    if not _table_exists(cur, table):
        return
    join = " AND ".join(f"t1.`{c}` = t2.`{c}`" for c in key_cols)
    cur.execute(
        f"DELETE t1 FROM `{table}` t1 "
        f"INNER JOIN `{table}` t2 ON {join} AND t1.id > t2.id"
    )


def _prepare_referential_cleanup(cur, uuid_map: dict[str, int]) -> None:
    for table, column in (
        ("user_sessions", "user_id"),
        ("workflows", "project_id"),
        ("project_assets", "project_id"),
    ):
        _purge_unmapped_fk_rows(cur, table, column, uuid_map)

    if not _table_exists(cur, "project_members"):
        return
    _dedupe_table_rows(cur, "project_members", ["project_id", "user_id"])
    _drop_index_if_exists(cur, "project_members", "uq_project_members_project_user")
    _purge_unmapped_fk_rows(cur, "project_members", "project_id", uuid_map)
    _purge_unmapped_fk_rows(cur, "project_members", "user_id", uuid_map)
    cur.execute(
        "SELECT CAST(id AS CHAR(36)), CAST(invited_by AS CHAR(36)), CAST(user_id AS CHAR(36)) "
        "FROM project_members"
    )
    for row_id, invited_by, user_id in cur.fetchall():
        if invited_by in uuid_map:
            continue
        if user_id in uuid_map:
            cur.execute("UPDATE project_members SET invited_by = %s WHERE id = %s", (user_id, row_id))
        else:
            cur.execute("DELETE FROM project_members WHERE id = %s", (row_id,))


def _finalize_project_members_unique(cur) -> None:
    if not _table_exists(cur, "project_members"):
        return
    _dedupe_table_rows(cur, "project_members", ["project_id", "user_id"])
    try:
        cur.execute(
            "ALTER TABLE project_members "
            "ADD CONSTRAINT uq_project_members_project_user UNIQUE (project_id, user_id)"
        )
    except pymysql.err.OperationalError as exc:
        logger.warning("Could not recreate project_members unique index: %s", exc)


def migrate(conn) -> None:
    cur = conn.cursor()
    users_id_type = _column_type(cur, "users", "id")
    jobs_id_type = (
        _column_type(cur, "generation_jobs", "id") if _table_exists(cur, "generation_jobs") else None
    )
    if users_id_type in ("bigint",) and jobs_id_type in ("char", "varchar"):
        from .migrate_uuid_to_bigint_resume import resume_migrate

        resume_migrate(conn)
        return
    if users_id_type in ("bigint",):
        logger.info("users.id already BIGINT — migration skipped")
        return
    if users_id_type not in ("char", "varchar"):
        raise RuntimeError(f"Unexpected users.id type: {users_id_type}")

    logger.info("Starting UUID → BIGINT migration")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")

    # --- Build user ID map ---
    cur.execute("SELECT CAST(id AS CHAR(36)), created_at FROM users ORDER BY created_at ASC, id ASC")
    user_rows = cur.fetchall()
    user_map = _assign_user_ids(user_rows)
    global_map: dict[str, int] = dict(user_map)

    def extend_map(table: str, start: int = 1) -> None:
        if not _table_exists(cur, table):
            return
        has_created = _column_type(cur, table, "created_at") is not None
        order = "created_at ASC, id ASC" if has_created else "id ASC"
        cur.execute(f"SELECT CAST(id AS CHAR(36)) FROM `{table}` ORDER BY {order}")
        ids = [r[0] for r in cur.fetchall()]
        table_map = _assign_sequential_ids(ids, start=start)
        global_map.update(table_map)

    extend_map("subscription_plans")
    extend_map("models")
    extend_map("credit_activities")
    extend_map("projects")
    extend_map("workflows")
    extend_map("project_assets")
    extend_map("project_members")
    extend_map("credit_lots")
    extend_map("user_subscriptions")
    extend_map("subscription_grants")
    extend_map("generation_jobs")
    extend_map("credit_reservations")
    extend_map("credit_transactions")
    extend_map("credit_activity_claims")
    extend_map("user_sessions")
    extend_map("auth_events")
    if _table_exists(cur, "generation_job_admin_actions"):
        extend_map("generation_job_admin_actions")

    # --- users ---
    cur.execute("ALTER TABLE users ADD COLUMN id_new BIGINT UNSIGNED NULL FIRST")
    for old, new in user_map.items():
        cur.execute("UPDATE users SET id_new = %s WHERE id = %s", (new, old))
    cur.execute("ALTER TABLE users DROP PRIMARY KEY")
    cur.execute("ALTER TABLE users DROP COLUMN id")
    cur.execute("ALTER TABLE users CHANGE COLUMN id_new id BIGINT UNSIGNED NOT NULL")
    cur.execute("ALTER TABLE users ADD PRIMARY KEY (id)")
    cur.execute(f"ALTER TABLE users AUTO_INCREMENT = {max(user_map.values()) + 1}")
    try:
        cur.execute("ALTER TABLE users DROP COLUMN user_no")
    except pymysql.err.OperationalError:
        pass
    try:
        cur.execute("DROP INDEX ix_users_user_no ON users")
    except pymysql.err.OperationalError:
        pass

    def migrate_fk_table(
        table: str,
        pk: bool = True,
        fk_cols: list[str] | None = None,
        extra_after: list[tuple[str, str]] | None = None,
    ) -> None:
        if not _table_exists(cur, table):
            return
        fk_cols = fk_cols or []
        if pk:
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN id_new BIGINT UNSIGNED NULL FIRST")
            cur.execute(f"SELECT CAST(id AS CHAR(36)) FROM `{table}`")
            for (old_id,) in cur.fetchall():
                new_id = global_map.get(old_id)
                if new_id is None:
                    raise RuntimeError(f"Missing id map for {table} {old_id}")
                cur.execute(f"UPDATE `{table}` SET id_new = %s WHERE id = %s", (new_id, old_id))
            cur.execute(f"ALTER TABLE `{table}` DROP PRIMARY KEY")
            cur.execute(f"ALTER TABLE `{table}` DROP COLUMN id")
            cur.execute(f"ALTER TABLE `{table}` CHANGE COLUMN id_new id BIGINT UNSIGNED NOT NULL")
            cur.execute(f"ALTER TABLE `{table}` ADD PRIMARY KEY (id)")

        for col in fk_cols:
            new_col = f"{col}_new"
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{new_col}` BIGINT UNSIGNED NULL")
            cur.execute(f"SELECT CAST(`{col}` AS CHAR(36)), id FROM `{table}` WHERE `{col}` IS NOT NULL")
            for old_fk, row_id in cur.fetchall():
                new_fk = global_map.get(old_fk)
                if new_fk is None:
                    logger.warning("Unmapped FK %s.%s=%s row=%s", table, col, old_fk, row_id)
                    continue
                cur.execute(
                    f"UPDATE `{table}` SET `{new_col}` = %s WHERE CAST(id AS CHAR(36)) = %s OR id = %s",
                    (new_fk, str(row_id), row_id),
                )
            try:
                cur.execute(f"ALTER TABLE `{table}` DROP COLUMN `{col}`")
            except pymysql.err.OperationalError:
                cur.execute(f"ALTER TABLE `{table}` MODIFY `{col}` BIGINT UNSIGNED NULL")
                cur.execute(f"UPDATE `{table}` SET `{col}` = `{new_col}`")
                cur.execute(f"ALTER TABLE `{table}` DROP COLUMN `{new_col}`")
                continue
            cur.execute(f"ALTER TABLE `{table}` CHANGE COLUMN `{new_col}` `{col}` BIGINT UNSIGNED NULL")

        if extra_after:
            for ddl, _ in extra_after:
                try:
                    cur.execute(ddl)
                except pymysql.err.OperationalError as exc:
                    logger.debug("DDL skipped: %s (%s)", ddl[:80], exc)

    # projects + storage_folder
    if _table_exists(cur, "projects"):
        try:
            cur.execute("ALTER TABLE projects ADD COLUMN storage_folder VARCHAR(64) NULL AFTER owner_id")
        except pymysql.err.OperationalError:
            pass
        cur.execute("SELECT CAST(id AS CHAR(36)) FROM projects")
        for (old_id,) in cur.fetchall():
            folder = old_id.replace("-", "")[:32]
            cur.execute("UPDATE projects SET storage_folder = %s WHERE CAST(id AS CHAR(36)) = %s", (folder, old_id))

    _prepare_referential_cleanup(cur, global_map)

    migrate_fk_table("user_sessions", fk_cols=["user_id"])
    migrate_fk_table("projects", fk_cols=["owner_id"])
    migrate_fk_table("workflows", fk_cols=["project_id"])
    migrate_fk_table("project_assets", fk_cols=["project_id"])
    migrate_fk_table("project_members", fk_cols=["project_id", "user_id", "invited_by"])
    _finalize_project_members_unique(cur)
    migrate_fk_table("models")
    migrate_fk_table("credit_lots", fk_cols=["user_id"])
    migrate_fk_table("credit_activities")
    migrate_fk_table("credit_activity_claims", fk_cols=["activity_id", "user_id", "lot_id"])
    migrate_fk_table("subscription_plans")
    migrate_fk_table("user_subscriptions", fk_cols=["user_id", "plan_id"])
    migrate_fk_table("subscription_grants", fk_cols=["subscription_id", "user_id", "lot_id"])
    migrate_fk_table(
        "generation_jobs",
        fk_cols=["user_id", "actor_user_id", "project_id", "workflow_id", "last_admin_operator_id"],
    )
    migrate_fk_table("credit_reservations", fk_cols=["user_id", "job_id"])
    migrate_fk_table("credit_transactions", fk_cols=["user_id", "operator_id", "lot_id", "job_id"])
    migrate_fk_table("auth_events", fk_cols=["user_id"])
    if _table_exists(cur, "generation_job_admin_actions"):
        migrate_fk_table("generation_job_admin_actions", fk_cols=["job_id", "operator_id"])

    # JSON + string reference rewrites
    if _table_exists(cur, "credit_reservations"):
        cur.execute("SELECT id, allocations FROM credit_reservations")
        for rid, allocations in cur.fetchall():
            if not allocations:
                continue
            payload = allocations if isinstance(allocations, (dict, list)) else json.loads(allocations)
            new_payload = _rewrite_json(payload, global_map)
            cur.execute(
                "UPDATE credit_reservations SET allocations = %s WHERE id = %s",
                (json.dumps(new_payload, ensure_ascii=False), rid),
            )

    if _table_exists(cur, "generation_jobs"):
        cur.execute(
            "SELECT id, asset_id, credit_reservation_id, input_params, output_assets, trace_json "
            "FROM generation_jobs"
        )
        for jid, asset_id, res_id, inp, out, trace in cur.fetchall():
            new_asset = str(global_map[asset_id]) if asset_id and asset_id in global_map else asset_id
            new_res = str(global_map[res_id]) if res_id and res_id in global_map else res_id
            new_inp = _rewrite_json(
                inp if isinstance(inp, dict) else (json.loads(inp) if inp else {}),
                global_map,
            )
            new_out = _rewrite_json(
                out if isinstance(out, list) else (json.loads(out) if out else []),
                global_map,
            )
            new_trace = _rewrite_json(
                trace if isinstance(trace, dict) else (json.loads(trace) if trace else {}),
                global_map,
            )
            cur.execute(
                """
                UPDATE generation_jobs
                SET asset_id = %s, credit_reservation_id = %s,
                    input_params = %s, output_assets = %s, trace_json = %s
                WHERE id = %s
                """,
                (
                    new_asset,
                    new_res,
                    json.dumps(new_inp, ensure_ascii=False),
                    json.dumps(new_out, ensure_ascii=False),
                    json.dumps(new_trace, ensure_ascii=False),
                    jid,
                ),
            )

    # model_runtime_slots
    if _table_exists(cur, "model_runtime_slots"):
        try:
            cur.execute("ALTER TABLE model_runtime_slots MODIFY COLUMN job_id BIGINT UNSIGNED NOT NULL")
        except pymysql.err.OperationalError:
            cur.execute("ALTER TABLE model_runtime_slots ADD COLUMN job_id_new BIGINT UNSIGNED NULL")
            cur.execute("SELECT job_id FROM model_runtime_slots")
            for (old_job,) in cur.fetchall():
                new_job = global_map.get(str(old_job))
                if new_job:
                    cur.execute(
                        "UPDATE model_runtime_slots SET job_id_new = %s WHERE job_id = %s",
                        (new_job, old_job),
                    )
            cur.execute("ALTER TABLE model_runtime_slots DROP COLUMN job_id")
            cur.execute(
                "ALTER TABLE model_runtime_slots CHANGE COLUMN job_id_new job_id BIGINT UNSIGNED NOT NULL"
            )

    # Backfill storage_folder for any project still missing it (new UUID hex, never numeric id)
    if _table_exists(cur, "projects"):
        cur.execute(
            "SELECT id FROM projects WHERE storage_folder IS NULL OR storage_folder = ''"
        )
        for (pid,) in cur.fetchall():
            cur.execute(
                "UPDATE projects SET storage_folder = %s WHERE id = %s",
                (uuid.uuid4().hex, pid),
            )
        cur.execute("ALTER TABLE projects MODIFY COLUMN storage_folder VARCHAR(64) NOT NULL")

    if _table_exists(cur, "generation_jobs"):
        try:
            cur.execute("ALTER TABLE generation_jobs DROP COLUMN submit_no")
        except pymysql.err.OperationalError:
            pass

    cur.execute("UPDATE users SET role = 'admin' WHERE id = 1 AND role = 'user'")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    logger.info("Migration complete (%s UUID mappings)", len(global_map))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Migrate canvas MySQL UUID PKs to BIGINT")
    parser.add_argument(
        "--database-url",
        default=None,
        help="mysql+pymysql://user:pass@host:port/jumeng_canvas (or DATABASE_URL env)",
    )
    args = parser.parse_args()
    import os

    url = args.database_url or os.environ.get("DATABASE_URL")
    if not url:
        logger.error("DATABASE_URL or --database-url required")
        return 1
    conn = pymysql.connect(**_parse_mysql_url(url))
    try:
        migrate(conn)
    except Exception:
        conn.rollback()
        logger.exception("Migration failed — rolled back")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
