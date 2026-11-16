import pytest

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import backup_postgres
from app.backup_postgres import connection_env
from app.restore_postgres import restore, restore_env


def test_backup_connection_credentials_are_passed_without_a_url_argument():
    env = connection_env("postgresql+psycopg://sageops_app:private%40pass@db.example:5433/sageops?sslmode=require")
    assert env["PGHOST"] == "db.example"
    assert env["PGPORT"] == "5433"
    assert env["PGDATABASE"] == "sageops"
    assert env["PGUSER"] == "sageops_app"
    assert env["PGPASSWORD"] == "private@pass"
    assert env["PGSSLMODE"] == "require"


def test_restore_refuses_the_source_database():
    source = "postgresql://sageops_app:source@db.example:5433/sageops"
    with pytest.raises(ValueError, match="configured source"):
        restore_env(source, "postgresql://sageops_app:target@db.example:5433/sageops")


def test_restore_targets_separate_database_and_calls_pg_restore(tmp_path, monkeypatch):
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"valid-test-fixture")
    calls = []

    def fake_run(command, *, env, check):
        calls.append((command, env, check))

    monkeypatch.setattr("app.restore_postgres.subprocess.run", fake_run)
    class EmptyDatabase:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *_args):
            class Result:
                def fetchone(self):
                    return (0,)

            return Result()

    monkeypatch.setattr("app.restore_postgres.psycopg.connect", lambda *_args: EmptyDatabase())
    restore(
        backup,
        "postgresql://sageops_app:source@source.example:5433/sageops",
        "postgresql://sageops_app:restore@restore.example:5433/sageops_restore",
    )
    assert calls[0][0][0] == "pg_restore"
    assert "--single-transaction" in calls[0][0]
    assert calls[0][1]["PGHOST"] == "restore.example"
    assert calls[0][1]["PGDATABASE"] == "sageops_restore"
    assert calls[0][2] is True


def test_restore_refuses_nonempty_target(tmp_path, monkeypatch):
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"valid-test-fixture")

    class NonEmptyDatabase:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *_args):
            class Result:
                def fetchone(self):
                    return (1,)

            return Result()

    monkeypatch.setattr("app.restore_postgres.psycopg.connect", lambda *_args: NonEmptyDatabase())
    with pytest.raises(ValueError, match="empty database"):
        restore(
            backup,
            "postgresql://sageops_app:source@source.example:5433/sageops",
            "postgresql://sageops_app:restore@restore.example:5433/sageops_restore",
        )


def test_backup_uploads_encrypted_dump_and_expires_old_objects(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://sageops_app:pw@db.example:5433/sageops")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "private-sageops-backups")
    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "30")

    class S3:
        def __init__(self):
            self.upload = None
            self.deleted = None

        def upload_file(self, path, bucket, key, *, ExtraArgs):
            self.upload = (Path(path).read_bytes(), bucket, key, ExtraArgs)

        def get_paginator(self, operation):
            assert operation == "list_objects_v2"
            return self

        def paginate(self, *, Bucket, Prefix):
            now = datetime.now(UTC)
            yield {"Contents": [
                {"Key": Prefix + "old.dump", "LastModified": now - timedelta(days=31)},
                {"Key": Prefix + "new.dump", "LastModified": now - timedelta(days=1)},
            ]}

        def delete_objects(self, **kwargs):
            self.deleted = kwargs

    s3 = S3()
    monkeypatch.setattr(backup_postgres.boto3, "client", lambda *args, **kwargs: s3)

    def fake_dump(command, *, env, check, stdout):
        assert command == ["pg_dump", "--format=custom", "--no-owner", "--no-acl"]
        assert env["PGDATABASE"] == "sageops"
        assert check is True
        stdout.write(b"postgres custom backup")

    monkeypatch.setattr(backup_postgres.subprocess, "run", fake_dump)
    backup_postgres.main()
    assert s3.upload[0] == b"postgres custom backup"
    assert s3.upload[1] == "private-sageops-backups"
    assert s3.upload[3]["ServerSideEncryption"] == "AES256"
    assert s3.deleted["Delete"]["Objects"] == [{"Key": "sageops/postgres/old.dump"}]
