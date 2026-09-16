#!/usr/bin/env python3
"""Dump test-server MySQL and save to local file via SSH."""
from __future__ import annotations

import sys
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent / "deploy"
sys.path.insert(0, str(DEPLOY_DIR))

from deploy_remote import connect_with_fallback, load_env_sh, resolve_ssh_password, run  # noqa: E402


def main() -> int:
    cfg = load_env_sh(DEPLOY_DIR / "env.test.sh")
    host = cfg["API_HOST"]
    port = int(cfg.get("API_SSH_PORT", "22"))
    user = cfg.get("API_SSH_USER", "root")
    pw = resolve_ssh_password(cfg, "api")
    mysql_pw = cfg.get("MYSQL_PASSWORD", "YOUR_MYSQL_PASSWORD")
    mysql_db = cfg.get("MYSQL_DATABASE", "jumeng_canvas")
    out = Path(__file__).resolve().parents[1] / "data" / "test-mysql-dump.sql"
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"==> SSH {user}@{host}:{port}")
    client, _ = connect_with_fallback(host, [port], user, pw)
    try:
        remote_sql = "/tmp/jumeng_canvas_test_dump.sql.gz"
        # Use gzip to shrink transfer; single-transaction for InnoDB consistency
        dump_cmd = (
            f"mysqldump -uroot -p'{mysql_pw}' --single-transaction --routines --triggers "
            f"--default-character-set=utf8mb4 --databases {mysql_db} "
            f"| gzip -c > {remote_sql} && ls -lh {remote_sql}"
        )
        print("==> mysqldump on remote...")
        code, out_text, err = run(client, dump_cmd)
        print((out_text or err or "").strip())
        if code != 0:
            print("DUMP FAILED", file=sys.stderr)
            return 1

        print(f"==> SFTP download -> {out}")
        sftp = client.open_sftp()
        try:
            sftp.get(remote_sql, str(out) + ".gz")
        finally:
            sftp.close()
        run(client, f"rm -f {remote_sql}")
    finally:
        client.close()

    gz = Path(str(out) + ".gz")
    print(f"==> downloaded {gz} size={gz.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
