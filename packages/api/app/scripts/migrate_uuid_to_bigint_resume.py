"""Resume a partially applied UUID→BIGINT migration (MySQL DDL auto-committed)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import pymysql

from .migrate_uuid_to_bigint import (
    DEV_USER_UUID,
    _assign_sequential_ids,
    _column_type,
    _dedupe_table_rows,
    _drop_index_if_exists,
    _finalize_project_members_unique,
    _prepare_referential_cleanup,
    _purge_unmapped_fk_rows,
    _rewrite_json,
    _table_exists,
)

logger = logging.getLogger(__name__)

_UUID_IN_TEXT = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_ASSET_UUID_IN_KEY = re.compile(
    r"/assets/[^/]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

_CHAR_PK_TABLES = [
    "subscription_plans",
    "models",
    "credit_activities",
    "project_members",
    "credit_lots",
    "user_subscriptions",
    "subscription_grants",
    "generation_jobs",
    "credit_reservations",
    "credit_transactions",
    "credit_activity_claims",
    "user_sessions",
    "auth_events",
    "generation_job_admin_actions",
]


def _hex_to_uuid(hex32: str) -> str:
    h = hex32.lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _has_column(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return cur.fetchone() is not None


def _rebuild_global_map(cur) -> dict[str, int]:
    global_map: dict[str, int] = {DEV_USER_UUID: 1}

    cur.execute(
        """
        SELECT DISTINCT ae.user_id, u.id
        FROM auth_events ae
        JOIN users u ON u.phone = ae.phone
        WHERE ae.user_id IS NOT NULL AND ae.phone IS NOT NULL
        """
    )
    for old_uid, new_uid in cur.fetchall():
        global_map[str(old_uid)] = int(new_uid)

    cur.execute("SELECT id, storage_folder FROM projects")
    for pid, folder in cur.fetchall():
        folder_s = str(folder or "").strip().lower()
        if len(folder_s) == 32:
            global_map[_hex_to_uuid(folder_s)] = int(pid)

    cur.execute("SELECT id, flow_json FROM workflows")
    for wid, flow_json in cur.fetchall():
        text = flow_json if isinstance(flow_json, str) else json.dumps(flow_json)
        for found in _UUID_IN_TEXT.findall(text):
            global_map[found] = int(wid)

    cur.execute("SELECT id, oss_key FROM project_assets")
    for aid, oss_key in cur.fetchall():
        match = _ASSET_UUID_IN_KEY.search(str(oss_key or ""))
        if match:
            global_map[match.group(1)] = int(aid)

    logger.info("Rebuilt %s UUID mappings from migrated tables", len(global_map))
    return global_map


def _extend_char_pk_maps(cur, global_map: dict[str, int]) -> None:
    for table in _CHAR_PK_TABLES:
        if not _table_exists(cur, table):
            continue
        id_type = _column_type(cur, table, "id")
        if id_type in ("bigint",):
            continue
        has_created = _column_type(cur, table, "created_at") is not None
        order = "created_at ASC, id ASC" if has_created else "id ASC"
        cur.execute(f"SELECT CAST(id AS CHAR(36)) FROM `{table}` ORDER BY {order}")
        ids = [r[0] for r in cur.fetchall()]
        table_map = _assign_sequential_ids(ids, start=1)
        global_map.update(table_map)
    logger.info("Extended map to %s total UUID mappings", len(global_map))


def _update_fk_new_col(cur, table: str, col: str, row_id: Any, new_fk: int) -> None:
    id_type = _column_type(cur, table, "id")
    if id_type in ("bigint",):
        cur.execute(f"UPDATE `{table}` SET `{col}_new` = %s WHERE id = %s", (new_fk, row_id))
    else:
        cur.execute(
            f"UPDATE `{table}` SET `{col}_new` = %s WHERE CAST(id AS CHAR(36)) = %s OR id = %s",
            (new_fk, str(row_id), row_id),
        )


def _drop_indexes_on_column(cur, table: str, column: str) -> None:
    cur.execute(f"SHOW INDEX FROM `{table}`")
    for row in cur.fetchall():
        key_name = str(row[2])
        column_name = str(row[4])
        if column_name == column and key_name.upper() != "PRIMARY":
            _drop_index_if_exists(cur, table, key_name)


def migrate_fk_table(
    cur,
    global_map: dict[str, int],
    table: str,
    *,
    pk: bool = True,
    fk_cols: list[str] | None = None,
) -> None:
    if not _table_exists(cur, table):
        return
    fk_cols = fk_cols or []
    id_type = _column_type(cur, table, "id")

    if pk and id_type not in ("bigint",):
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
        if _column_type(cur, table, col) in ("bigint",):
            continue
        new_col = f"{col}_new"
        if not _has_column(cur, table, new_col):
            cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{new_col}` BIGINT UNSIGNED NULL")
        cur.execute(f"SELECT CAST(`{col}` AS CHAR(36)), id FROM `{table}` WHERE `{col}` IS NOT NULL")
        for old_fk, row_id in cur.fetchall():
            new_fk = global_map.get(str(old_fk))
            if new_fk is None:
                logger.warning("Unmapped FK %s.%s=%s row=%s", table, col, old_fk, row_id)
                continue
            _update_fk_new_col(cur, table, col, row_id, new_fk)
        _drop_indexes_on_column(cur, table, col)
        try:
            cur.execute(f"ALTER TABLE `{table}` DROP COLUMN `{col}`")
        except (pymysql.err.OperationalError, pymysql.err.IntegrityError):
            _drop_indexes_on_column(cur, table, col)
            cur.execute(f"ALTER TABLE `{table}` DROP COLUMN `{col}`")
        cur.execute(f"ALTER TABLE `{table}` CHANGE COLUMN `{new_col}` `{col}` BIGINT UNSIGNED NULL")


def _prepare_credit_activity_claims(cur, global_map: dict[str, int]) -> None:
    if not _table_exists(cur, "credit_activity_claims"):
        return
    for idx in (
        "ix_credit_activity_claims_activity_user_seq",
        "ix_credit_activity_claims_activity_user",
        "ix_credit_activity_claims_activity_id",
        "ix_credit_activity_claims_user_id",
    ):
        _drop_index_if_exists(cur, "credit_activity_claims", idx)
    _purge_unmapped_fk_rows(cur, "credit_activity_claims", "activity_id", global_map)
    _purge_unmapped_fk_rows(cur, "credit_activity_claims", "user_id", global_map)
    if _has_column(cur, "credit_activity_claims", "activity_id_new"):
        if _column_type(cur, "credit_activity_claims", "activity_id") in ("char", "varchar"):
            cur.execute("ALTER TABLE credit_activity_claims DROP COLUMN activity_id")
        cur.execute(
            "ALTER TABLE credit_activity_claims CHANGE COLUMN activity_id_new activity_id "
            "BIGINT UNSIGNED NOT NULL"
        )


def _finalize_credit_activity_claims_indexes(cur) -> None:
    if not _table_exists(cur, "credit_activity_claims"):
        return
    _dedupe_table_rows(cur, "credit_activity_claims", ["activity_id", "user_id", "claim_seq"])
    ddl_list = [
        "CREATE INDEX ix_credit_activity_claims_activity_id ON credit_activity_claims (activity_id)",
        "CREATE INDEX ix_credit_activity_claims_user_id ON credit_activity_claims (user_id)",
        "CREATE INDEX ix_credit_activity_claims_activity_user ON credit_activity_claims (activity_id, user_id)",
        "CREATE UNIQUE INDEX ix_credit_activity_claims_activity_user_seq "
        "ON credit_activity_claims (activity_id, user_id, claim_seq)",
    ]
    for ddl in ddl_list:
        try:
            cur.execute(ddl)
        except pymysql.err.OperationalError as exc:
            logger.debug("Index DDL skipped: %s", exc)


def _resume_project_members(cur, global_map: dict[str, int]) -> None:
    if not _table_exists(cur, "project_members"):
        return
    if _column_type(cur, "project_members", "project_id") in ("bigint",) and _column_type(
        cur, "project_members", "user_id"
    ) in ("bigint",):
        return
    _drop_index_if_exists(cur, "project_members", "uq_project_members_project_user")

    if _has_column(cur, "project_members", "project_id_new"):
        if _column_type(cur, "project_members", "project_id") in ("char", "varchar"):
            cur.execute("ALTER TABLE project_members DROP COLUMN project_id")
        cur.execute(
            "ALTER TABLE project_members CHANGE COLUMN project_id_new project_id "
            "BIGINT UNSIGNED NOT NULL"
        )
    elif _column_type(cur, "project_members", "project_id") in ("char", "varchar"):
        migrate_fk_table(cur, global_map, "project_members", pk=False, fk_cols=["project_id"])

    fk_remaining = []
    if _column_type(cur, "project_members", "user_id") in ("char", "varchar"):
        fk_remaining.append("user_id")
    if _column_type(cur, "project_members", "invited_by") in ("char", "varchar"):
        fk_remaining.append("invited_by")
    if fk_remaining:
        migrate_fk_table(cur, global_map, "project_members", pk=False, fk_cols=fk_remaining)


def _rewrite_json_columns(cur, global_map: dict[str, int]) -> None:
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


def resume_migrate(conn) -> None:
    cur = conn.cursor()
    jobs_id_type = _column_type(cur, "generation_jobs", "id") if _table_exists(cur, "generation_jobs") else None
    if jobs_id_type in ("bigint",):
        logger.info("generation_jobs.id already BIGINT — resume skipped")
        return

    logger.info("Resuming partial UUID → BIGINT migration")
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")

    global_map = _rebuild_global_map(cur)
    _extend_char_pk_maps(cur, global_map)
    _prepare_referential_cleanup(cur, global_map)
    _resume_project_members(cur, global_map)
    _finalize_project_members_unique(cur)

    if _column_type(cur, "models", "id") in ("char", "varchar"):
        migrate_fk_table(cur, global_map, "models")
    if _column_type(cur, "credit_lots", "id") in ("char", "varchar"):
        _purge_unmapped_fk_rows(cur, "credit_lots", "user_id", global_map)
        migrate_fk_table(cur, global_map, "credit_lots", fk_cols=["user_id"])
    if _column_type(cur, "credit_activities", "id") in ("char", "varchar"):
        migrate_fk_table(cur, global_map, "credit_activities")
    if _table_exists(cur, "credit_activity_claims"):
        _prepare_credit_activity_claims(cur, global_map)
        fk_remaining = []
        if _column_type(cur, "credit_activity_claims", "user_id") in ("char", "varchar"):
            fk_remaining.append("user_id")
        if _column_type(cur, "credit_activity_claims", "lot_id") in ("char", "varchar"):
            fk_remaining.append("lot_id")
        if fk_remaining:
            migrate_fk_table(
                cur,
                global_map,
                "credit_activity_claims",
                pk=False,
                fk_cols=fk_remaining,
            )
        _finalize_credit_activity_claims_indexes(cur)
    if _column_type(cur, "subscription_plans", "id") in ("char", "varchar"):
        migrate_fk_table(cur, global_map, "subscription_plans")
    if _column_type(cur, "user_subscriptions", "id") in ("char", "varchar"):
        migrate_fk_table(cur, global_map, "user_subscriptions", fk_cols=["user_id", "plan_id"])
    if _column_type(cur, "subscription_grants", "id") in ("char", "varchar"):
        _drop_index_if_exists(cur, "subscription_grants", "ix_subscription_grants_period")
        migrate_fk_table(
            cur,
            global_map,
            "subscription_grants",
            fk_cols=["subscription_id", "user_id", "lot_id"],
        )
        try:
            cur.execute(
                "CREATE UNIQUE INDEX ix_subscription_grants_period "
                "ON subscription_grants (subscription_id, period_key)"
            )
        except pymysql.err.OperationalError:
            pass
    elif _table_exists(cur, "subscription_grants"):
        _drop_index_if_exists(cur, "subscription_grants", "ix_subscription_grants_period")
        fk_remaining = [
            col
            for col in ("subscription_id", "user_id", "lot_id")
            if _column_type(cur, "subscription_grants", col) in ("char", "varchar")
        ]
        if fk_remaining:
            migrate_fk_table(
                cur,
                global_map,
                "subscription_grants",
                pk=False,
                fk_cols=fk_remaining,
            )
        try:
            cur.execute(
                "CREATE UNIQUE INDEX ix_subscription_grants_period "
                "ON subscription_grants (subscription_id, period_key)"
            )
        except pymysql.err.OperationalError:
            pass
    if _column_type(cur, "generation_jobs", "id") in ("char", "varchar"):
        migrate_fk_table(
            cur,
            global_map,
            "generation_jobs",
            fk_cols=["user_id", "actor_user_id", "project_id", "workflow_id", "last_admin_operator_id"],
        )
    if _column_type(cur, "credit_reservations", "id") in ("char", "varchar"):
        migrate_fk_table(cur, global_map, "credit_reservations", fk_cols=["user_id", "job_id"])
    if _column_type(cur, "credit_transactions", "id") in ("char", "varchar"):
        migrate_fk_table(
            cur,
            global_map,
            "credit_transactions",
            fk_cols=["user_id", "operator_id", "lot_id", "job_id"],
        )
    if _column_type(cur, "auth_events", "id") in ("char", "varchar"):
        migrate_fk_table(cur, global_map, "auth_events", fk_cols=["user_id"])
    if _table_exists(cur, "generation_job_admin_actions") and _column_type(
        cur, "generation_job_admin_actions", "id"
    ) in ("char", "varchar"):
        migrate_fk_table(
            cur,
            global_map,
            "generation_job_admin_actions",
            fk_cols=["job_id", "operator_id"],
        )

    _rewrite_json_columns(cur, global_map)

    if _table_exists(cur, "model_runtime_slots"):
        try:
            cur.execute("ALTER TABLE model_runtime_slots MODIFY COLUMN job_id BIGINT UNSIGNED NOT NULL")
        except pymysql.err.OperationalError:
            if not _has_column(cur, "model_runtime_slots", "job_id_new"):
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

    if _table_exists(cur, "projects"):
        cur.execute("SELECT id FROM projects WHERE storage_folder IS NULL OR storage_folder = ''")
        for (pid,) in cur.fetchall():
            cur.execute(
                "UPDATE projects SET storage_folder = %s WHERE id = %s",
                (uuid.uuid4().hex, pid),
            )
        try:
            cur.execute("ALTER TABLE projects MODIFY COLUMN storage_folder VARCHAR(64) NOT NULL")
        except pymysql.err.OperationalError:
            pass

    if _table_exists(cur, "generation_jobs"):
        try:
            cur.execute("ALTER TABLE generation_jobs DROP COLUMN submit_no")
        except pymysql.err.OperationalError:
            pass

    cur.execute("UPDATE users SET role = 'admin' WHERE id = 1 AND role = 'user'")
    _ensure_auto_increment_columns(cur)
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()
    logger.info("Resume migration complete (%s UUID mappings)", len(global_map))


_AUTO_INCREMENT_TABLES = (
    "users",
    "projects",
    "workflows",
    "project_assets",
    "project_members",
    "models",
    "credit_lots",
    "credit_activities",
    "credit_activity_claims",
    "credit_reservations",
    "credit_transactions",
    "subscription_plans",
    "user_subscriptions",
    "subscription_grants",
    "auth_events",
    "user_sessions",
    "generation_job_admin_actions",
)


def _ensure_auto_increment_columns(cur) -> None:
    for table in _AUTO_INCREMENT_TABLES:
        if not _table_exists(cur, table):
            continue
        if _column_type(cur, table, "id") not in ("bigint",):
            continue
        cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM `{table}`")
        next_id = int(cur.fetchone()[0] or 0) + 1
        try:
            cur.execute(
                f"ALTER TABLE `{table}` MODIFY COLUMN id BIGINT UNSIGNED NOT NULL "
                f"AUTO_INCREMENT, AUTO_INCREMENT={next_id}"
            )
        except pymysql.err.OperationalError as exc:
            logger.debug("AUTO_INCREMENT skipped for %s: %s", table, exc)
