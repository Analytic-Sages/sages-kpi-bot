# SageOps production readiness

Status reflects repository and authorized local-environment checks on 2026-11-16. “Partial” means code exists but production readiness has not been established by end-to-end evidence.

| Subsystem | Status | Evidence and remaining gap |
|---|---|---|
| PostgreSQL source of truth | **Local verified; production not configured** | PostgreSQL 17.10 at `localhost:5433`, database `sageops`, role `sageops_app`. Alembic head is `0003_ops_persistence`; 21 public tables exist. Local counts after import: 9 teams, 9 people, 126 KPIs, 1 task, including 10 ownerless team KPIs. No PostgreSQL 16 instance was modified. Production hosting, backups, restore drill, and monitoring are still absent. |
| Schema and migrations | **Implemented; local head verified** | Three migrations provide operations and persistence schema. Local migration state was checked with `alembic current`. Automated isolated PostgreSQL migration/rollback coverage is not established. |
| Workbook migration | **Reconciliation and local import complete** | Importer inspects operational sheets, validates references and duplicates, retains meaningful source columns, preserves unassigned team KPIs, refuses a non-empty target, and applies in one transaction. The workbook reconciliation had no blocking issues and identified 10 ownerless KPIs. The import was applied only to the authorized local database, not production. Historical KPI results, reviews, blockers, and reminders were absent in this workbook. |
| Authentication | **Partial** | Google OIDC admin login and signed session support remain. Production OIDC client, domain, approved-email allowlist, HTTPS, and real staff identity linking have not been validated. |
| Authorization and audit | **Partial** | Operations endpoints enforce staff/team scope, role permission checks, CSRF on writes, and audited mutations. Automated coverage does not yet prove the complete role-by-route matrix against PostgreSQL. |
| Teams and staff directory | **API implemented; UI incomplete** | Admin APIs support teams, roles/permissions, and staff records. The preserved dashboard does not yet expose all directory management workflows. |
| KPIs and results | **API implemented; UI incomplete** | PostgreSQL-backed KPI reads, definitions, ownerless team KPIs, result submissions, and achievement data are present. Full frontend submission/management workflow and E2E verification remain. |
| Tasks | **API implemented; UI incomplete** | PostgreSQL task reads and guarded create/update/status transitions are present. Frontend currently browses/filter records but does not offer assignment or status workflows. |
| Weekly reviews, blockers, approvals | **Partial** | Review, action, and blocker models/APIs exist with scope checks. Approval/confirmation coverage and complete user workflows have not been demonstrated end to end. |
| Telegram and notifications | **Partial** | Telegram webhook endpoint authenticates and deduplicates updates; staff commands include task lookup and completion behind a write gate. Notification persistence, attempt history, retry and interrupted-send recovery exist in the worker. Production bot setup, command coverage, scheduling, and delivery/retry tests remain incomplete. |
| Dashboard and reporting | **PostgreSQL reads connected; partial** | Existing visual dashboard now reads database-backed overview, team performance, audit, task, and KPI endpoints. Frontend JavaScript syntax passes. No browser-level authenticated workflow test has verified actual records through the UI. |
| Google Sheets runtime | **Removed from application path** | Sheets client and fallback modules/config were removed. The workbook is a one-time import source only. Google credentials may still be used for OIDC/optional analytics, not Sheets. |
| Health and observability | **Partial** | Liveness and PostgreSQL/Alembic readiness checks exist; structured logging is configured. Production monitoring, alerts, and deployed health behavior are unverified. |
| Deployment and recovery | **Not production ready** | No public deployment performed. Hosting environment, HTTPS/domain, separate worker service, database backup schedule, restore test, rollback image, and operational ownership remain unconfigured. |
| Tests | **Current suite passes; coverage gaps remain** | Full repository pytest suite passes with plugin autoload disabled; Ruff and JavaScript syntax checks pass. Dedicated production-like PostgreSQL, browser, migration rollback, notification/scheduler, and end-to-end operations tests remain needed. |

## Workbook reconciliation and local import

The workbook contains `START HERE`, `Dashboard`, `Config`, `Teams`, `Team Roster`, `People Directory`, `Job_Descriptions`, `KPI Register`, `Weekly KPI Log`, `Master Task Tracker`, `Weekly Review`, `Blocker Log Worksheet`, `Person Digest`, `Reminder Log`, `Bot Guide`, `Data Dictionary`, and `Lists`. Formula/presentation-oriented sheets are not treated as operational tables. Reconciliation found 9 teams, 9 people, 11 roles, 126 KPIs, and 1 task; no historical KPI result, review, blocker, or reminder records were present. Ten KPIs intentionally have no individual owner.

Import was applied to the local development database after validation. No production database exists in this environment and no production import or public deployment was performed.

## Verification record

- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin -q` — passed (88 tests); pytest emitted the installed `pytest-asyncio` default-loop-scope deprecation warning.
- `ruff check app scripts tests` — passed.
- `node --check app/static/admin/app.js` — passed.
- `alembic current` — `0003_ops_persistence (head)` on authorized local PostgreSQL 17.10.
- Read-only database inspection — 21 tables; 9 teams, 9 people, 126 KPIs, 1 task, 10 ownerless KPIs.
- `git diff --check` — run before final commit review.

## Release blockers

1. Complete the dashboard’s operational create/update and submission workflows for authorized users, including directory, KPI result, tasks, reviews, blockers, and notifications.
2. Complete and run database-backed authorization, Telegram, notification retry/scheduler, browser integration, and end-to-end workflow tests.
3. Configure production identity, secrets, HTTPS, hosting, a separate worker, PostgreSQL backup/restore, monitoring, and rollback; verify them in staging.
4. Obtain explicit approval before any production import, destructive production database operation, or public deployment.
