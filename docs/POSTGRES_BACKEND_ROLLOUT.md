# SageOps PostgreSQL backend rollout

## Current implementation increment

This branch introduces the PostgreSQL persistence foundation and a protected, read-only normalized API under `/api/v1/operations`. It does not yet cut the existing admin frontend, Telegram bot, or scheduler over from Google Sheets.

The domain schema includes teams, roles, people, KPIs, KPI results, tasks, task details, task reminder state, weekly reviews, review actions, blockers, settings, notifications, change requests, append-only audit history, Telegram update deduplication, and job-run observability.

## Provisioning

1. Create a managed PostgreSQL database with automated backups and point-in-time recovery.
2. Set `DATABASE_URL` in the deployment secret/environment store. Use the provider's pooled connection string only if it supports the application's connection pattern; otherwise use its direct connection string for migrations.
3. Deploy this branch to a staging service, not the production service.
4. Run `alembic upgrade head` from the repository root.
5. Verify `GET /api/v1/operations/health` while signed in as an allowlisted admin.
6. Keep all existing Telegram, scheduled workflow, and write flags disabled.

## API

All endpoints require the existing authenticated admin session:

- `GET /api/v1/operations/health`
- `GET /api/v1/operations/teams`
- `GET /api/v1/operations/roles`
- `GET /api/v1/operations/people`
- `GET /api/v1/operations/tasks`
- `GET /api/v1/operations/kpis`
- `GET /api/v1/operations/kpi-results`

The current Control Centre API contract is deliberately unchanged in this increment. Its existing `/api/v1/admin/*` routes still read Google Sheets until the database migration, parity checks, and explicit cutover are completed.

## Safety and cutover rules

- Do not enable writes merely because the schema migrated.
- Do not run a production import until the workbook's actual headers, row counts, formulas, and data-quality report have been reviewed.
- Import is a one-time, repeatable migration operation with an explicit `--apply` flag; it must never sync edits bidirectionally.
- Keep the spreadsheet unchanged during shadow mode. Compare database-derived dashboard and reminder decisions with the existing workbook for at least seven days.
- Switch the frontend and bot to PostgreSQL only after the API contract tests, migration checks, data parity, and rollback plan pass.
- Once cut over, PostgreSQL is the sole writer. Google Sheets becomes a one-way export/report only.
- Audit rows are protected against UPDATE and DELETE by a PostgreSQL trigger. Production database credentials should also use a restricted application role; migrations should use a separate owner role.

## Remaining implementation gates

1. Add a validated workbook import and reconciliation report using the real workbook headers.
2. Port the existing admin dashboard endpoints to query PostgreSQL while preserving their JSON contracts.
3. Add the complete staff/team/task/KPI/review CRUD API with role-based authorization.
4. Implement the audited change-request state machine and transactional optimistic concurrency.
5. Port reminder, escalation, digest, weekly KPI and Saturday review rules to a database-backed domain service.
6. Add Telegram webhook ingestion, update deduplication, command handlers and notification dispatch.
7. Add a separately deployed worker process with PostgreSQL advisory locks and durable job-run records.
8. Complete shadow-mode parity and controlled staging acceptance before production cutover.
