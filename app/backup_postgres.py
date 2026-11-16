"""Create an encrypted logical PostgreSQL backup and enforce object retention."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
from sqlalchemy.engine import make_url

log = logging.getLogger("sageops.backup")


def connection_env(database_url: str) -> dict[str, str]:
    url = make_url(database_url)
    if not url.drivername.startswith("postgresql") or not url.host or not url.database:
        raise ValueError("Backup requires a PostgreSQL DATABASE_URL")
    env = os.environ.copy()
    env.update(
        {
            "PGHOST": url.host,
            "PGPORT": str(url.port or 5432),
            "PGDATABASE": url.database,
            "PGUSER": url.username or "",
            "PGPASSWORD": url.password or "",
            "PGCONNECT_TIMEOUT": "15",
        }
    )
    sslmode = url.query.get("sslmode")
    if sslmode:
        env["PGSSLMODE"] = str(sslmode)
    return env


def main() -> None:
    database_url = os.getenv("DATABASE_URL", "")
    bucket = os.getenv("BACKUP_S3_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("BACKUP_S3_BUCKET is required")
    retention_days = int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
    if retention_days < 7:
        raise ValueError("BACKUP_RETENTION_DAYS must be at least 7")

    now = datetime.now(UTC)
    prefix = os.getenv("BACKUP_S3_PREFIX", "sageops/postgres").strip("/")
    key = f"{prefix}/{now:%Y/%m/%d}/sageops-{now:%Y%m%dT%H%M%SZ}.dump"
    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
    with tempfile.TemporaryDirectory(prefix="sageops-backup-") as temp_dir:
        backup = Path(temp_dir) / "sageops.dump"
        subprocess.run(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl"],
            env=connection_env(database_url),
            check=True,
            stdout=backup.open("wb"),
        )
        if not backup.is_file() or backup.stat().st_size == 0:
            raise RuntimeError("pg_dump returned an empty backup")
        s3.upload_file(
            str(backup), bucket, key,
            ExtraArgs={"ServerSideEncryption": "AES256", "Metadata": {"created-at": now.isoformat()}},
        )
    cutoff = now - timedelta(days=retention_days)
    paginator = s3.get_paginator("list_objects_v2")
    stale = [
        {"Key": item["Key"]}
        for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/")
        for item in page.get("Contents", [])
        if item["LastModified"].astimezone(UTC) < cutoff
    ]
    for start in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": stale[start : start + 1000]})
    log.info("backup_uploaded key=%s deleted_old_backups=%d", key, len(stale))


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    main()
