"""Restore a logical backup to a database distinct from the configured source."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url


def restore_env(source_url: str, target_url: str) -> dict[str, str]:
    source = make_url(source_url)
    target = make_url(target_url)
    if not target.drivername.startswith("postgresql") or not target.host or not target.database:
        raise ValueError("Restore target must be a PostgreSQL URL")
    if (source.host, source.port or 5432, source.database) == (
        target.host, target.port or 5432, target.database
    ):
        raise ValueError("Refusing to restore into the configured source database")
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": target.host,
            "PGPORT": str(target.port or 5432),
            "PGDATABASE": target.database,
            "PGUSER": target.username or "",
            "PGPASSWORD": target.password or "",
            "PGCONNECT_TIMEOUT": "15",
        }
    )
    sslmode = target.query.get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = str(sslmode)
    return env


def restore(backup: Path, source_url: str, target_url: str) -> None:
    if not backup.is_file() or backup.stat().st_size == 0:
        raise ValueError("Backup file does not exist or is empty")
    target = make_url(target_url)
    connect_url = target.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(connect_url) as connection:
        relation_count = connection.execute(
            """SELECT count(*) FROM pg_class AS c
               JOIN pg_namespace AS n ON n.oid = c.relnamespace
               WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
                 AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"""
        ).fetchone()[0]
    if relation_count:
        raise ValueError("Restore target must be an empty database")
    subprocess.run(
        [
            "pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
            "--single-transaction", "--exit-on-error", str(backup),
        ],
        env=restore_env(source_url, target_url),
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="Path to a SageOps pg_dump custom-format file")
    parser.add_argument(
        "--target-database-url", default=os.getenv("RESTORE_TARGET_DATABASE_URL"),
        help="Separate empty PostgreSQL database URL; never use the source or production database",
    )
    args = parser.parse_args()
    if not args.target_database_url:
        parser.error("--target-database-url or RESTORE_TARGET_DATABASE_URL is required")
    source_url = os.getenv("DATABASE_URL", "")
    restore(args.backup, source_url, args.target_database_url)


if __name__ == "__main__":
    main()
