# SageOps

SageOps is Analytic Sages’ staff operations platform. PostgreSQL is the operational source of truth. The Excel workbook is used only by the one-time importer; Google Sheets is not a runtime dependency.

## Local setup

Use Python 3.11 or newer and PostgreSQL 17. The default local connection in `.env.example` targets `localhost:5433`, database `sageops`, role `sageops_app`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Set `DATABASE_URL` and authentication settings through the local ignored `.env` or your hosting secret manager. Keep database writes and notification delivery disabled until authentication and operational workflows have been validated. Never commit `.env`, database passwords, OAuth secrets, or Telegram tokens.

## Runtime services

- Web API and admin control centre: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- Background notifications/reminders: `python -m app.operations_worker`
- Database migrations: `alembic upgrade head`
- PostgreSQL readiness: `GET /health/ready`; liveness: `GET /health/live`
- Admin UI: `/control/`

The web process does not poll Telegram or run an in-process scheduler. Telegram updates use the authenticated webhook endpoint. Operational state, audit events, task/KPI records and delivery attempts are stored in PostgreSQL.

`TELEGRAM_TEST_MODE=true` simulates delivery and records it as `Simulated` without opening a network connection to Telegram. Live delivery requires explicitly setting it to `false`, disabling `DRY_RUN`, enabling notifications and configuring the bot token. Worker reminder scans use a PostgreSQL advisory lock; delivery rows use `FOR UPDATE SKIP LOCKED`, unique dedupe keys, bounded retries and stale-send recovery.

## Backups and recovery

The staging Render Blueprint provisions PostgreSQL 17, separate web and worker services, and a daily backup job. The backup job writes encrypted custom-format `pg_dump` files to a private S3 bucket and removes objects older than `BACKUP_RETENTION_DAYS` (30 days by default). Configure the S3 bucket, region, and a restricted bucket-scoped access key through the hosting secret manager. Enable bucket versioning and block public access. Paid Render Postgres also provides provider-managed point-in-time recovery; its recovery window depends on the Render workspace plan.

To test a backup, download a dump from the private bucket and restore it only into a separate empty database:

```bash
DATABASE_URL='postgresql://source-user:...@source-host/source-db' \
RESTORE_TARGET_DATABASE_URL='postgresql://restore-user:...@restore-host/restore-db' \
python -m app.restore_postgres /secure/path/sageops.dump
```

The restore command refuses the same host, port and database as the configured source. Validate the restored Alembic revision, row counts, health endpoint and representative dashboard records before using that instance. Recovery readiness is not verified until this command succeeds against a separate hosted database.

For schema rollback, review the target revision's Alembic `downgrade()` before running `alembic downgrade <revision>`; prefer a forward corrective migration when data could be lost. For an application release rollback, redeploy the provider's previous successful deploy artifact, then verify `/health/ready`. Database restores and migrations against production require explicit approval.

## Workbook migration

Create a reconciliation report before applying an import:

```bash
python scripts/import_sageops_workbook.py \
  'Analytic Sages Team KPI Growth Tracker BotReady.xlsx' \
  --report /tmp/sageops-reconciliation.json
```

Review every warning and preserved source field. Team-level KPIs may have no individual owner. The importer refuses a non-empty operational target and applies the approved import in one transaction with `--apply`. Do not run it against a production database without explicit approval.

## Verification

```bash
ruff check app scripts tests
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin -q
node --check app/static/admin/app.js
```

## Production status

The repository is not cleared for public production exposure. Provider credentials, hosting resources, TLS/domain configuration, backup/restore drills, and production authentication allowlisting must be configured and verified in the target environment. Review [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md) for the current subsystem evidence and gaps.
