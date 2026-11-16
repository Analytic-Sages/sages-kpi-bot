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
