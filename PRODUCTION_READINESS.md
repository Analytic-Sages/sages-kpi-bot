# SageOps production readiness

Status reflects repository and authorized local-environment checks on 2026-11-16. “Partial” means code exists but production readiness has not been established by end-to-end evidence.

| Subsystem | Status | Evidence and remaining gap |
|---|---|---|
| PostgreSQL source of truth | **Local verified; production not configured** | PostgreSQL 17.10 at `localhost:5433`, database `sageops`, role `sageops_app`. Alembic head is `0004_blocker_ownership`; 21 public tables exist. Local counts after import: 9 teams, 9 people, 126 KPIs, 1 task, including 10 ownerless team KPIs. No PostgreSQL 16 instance was modified. Production hosting, backups, restore drill, and monitoring are still absent. |
| Schema and migrations | **Implemented; local head verified** | Four migrations provide operations and persistence schema, including blocker ownership. Local migration state was checked with `alembic current`. Automated isolated PostgreSQL migration/rollback coverage is not established. |
| Workbook migration | **Reconciliation and local import complete** | Importer inspects operational sheets, validates references and duplicates, retains meaningful source columns, preserves unassigned team KPIs, refuses a non-empty target, and applies in one transaction. The workbook reconciliation had no blocking issues and identified 10 ownerless KPIs. The import was applied only to the authorized local database, not production. Historical KPI results, reviews, blockers, and reminders were absent in this workbook. |
| Authentication | **Implemented; production setup incomplete** | Google OIDC admin login and signed staff/admin sessions are supported. Staff sessions use team-scoped operations APIs. Production OIDC client, domain, allowlist, HTTPS, and live identity linking still need validation. |
| Authorization and audit | **Implemented; tests added** | Operations endpoints enforce team scope, role grants, CSRF on writes, and audited mutations. SQLite API integration tests cover permitted and denied workflows; production PostgreSQL role matrix coverage remains. |
| Teams and staff directory | **Dashboard workflows implemented** | Admins can create/edit teams, assign leads, create/edit/activate staff, assign teams/reporting managers, and create/edit role permissions. Staff profiles show current tasks and owned KPIs. |
| KPIs and results | **Dashboard workflows implemented** | KPI definitions support individual or ownerless team KPIs. Authorized users submit and edit results; history, target, achievement and below-target status come from PostgreSQL APIs and shared backend calculation rules. |
| Tasks | **Dashboard workflows implemented** | Authorized users create/assign tasks, update status/progress, reassign work, set priority/deadline, record output links/blockers, and filter team/owner/status/upcoming/overdue work. |
| Weekly reviews, blockers, approvals | **Dashboard workflows implemented** | Managers submit reviews with multiple follow-up actions; owners/managers update action status. Blockers support responsible owners and resolution. Task change requests support requester confirmation and manager approval/rejection with history. |
| Telegram and notifications | **Partial** | Telegram webhook endpoint authenticates and deduplicates updates; staff commands include task lookup and completion behind a write gate. Notification persistence, attempt history, retry and interrupted-send recovery exist in the worker. Production bot setup, command coverage, scheduling, and delivery/retry tests remain incomplete. |
| Dashboard and reporting | **Operational workflows connected** | Existing visual identity is preserved. Admin reporting and staff-mode operational views use PostgreSQL APIs. TestClient covers UI contracts and database-backed API workflow behavior; a real browser run remains unverified. |
| Google Sheets runtime | **Removed from application path** | Sheets client and fallback modules/config were removed. The workbook is a one-time import source only. Google credentials may still be used for OIDC/optional analytics, not Sheets. |
| Health and observability | **Partial** | Liveness and PostgreSQL/Alembic readiness checks exist; structured logging is configured. Production monitoring, alerts, and deployed health behavior are unverified. |
| Deployment and recovery | **Not production ready** | No public deployment performed. Hosting environment, HTTPS/domain, separate worker service, database backup schedule, restore test, rollback image, and operational ownership remain unconfigured. |
| Tests | **Workflow suite passes; production coverage gaps remain** | Full pytest suite, Ruff, and JavaScript syntax checks pass. Dedicated production-like PostgreSQL, browser, migration rollback, notification/scheduler, and deployed end-to-end operations tests remain needed. |

## Workbook reconciliation and local import

The workbook contains `START HERE`, `Dashboard`, `Config`, `Teams`, `Team Roster`, `People Directory`, `Job_Descriptions`, `KPI Register`, `Weekly KPI Log`, `Master Task Tracker`, `Weekly Review`, `Blocker Log Worksheet`, `Person Digest`, `Reminder Log`, `Bot Guide`, `Data Dictionary`, and `Lists`. Formula/presentation-oriented sheets are not treated as operational tables. Reconciliation found 9 teams, 9 people, 11 roles, 126 KPIs, and 1 task; no historical KPI result, review, blocker, or reminder records were present. Ten KPIs intentionally have no individual owner.

Import was applied to the local development database after validation. No production database exists in this environment and no production import or public deployment was performed.

## Verification record

- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin -q` — full suite passed; pytest emitted the installed `pytest-asyncio` default-loop-scope deprecation warning.
- `ruff check app scripts tests` — passed.
- `node --check app/static/admin/app.js` — passed.
- `alembic current` — `0004_blocker_ownership (head)` on authorized local PostgreSQL 17.10.
- Read-only database inspection — 21 tables; 9 teams, 9 people, 126 KPIs, 1 task, 10 ownerless KPIs.
- `git diff --check` — run before final commit review.

## Release blockers

1. Verify staff and administrator workflows in a real browser and against a production-like PostgreSQL staging database.
2. Complete Telegram delivery, notification retry/scheduler, and operational monitoring tests with configured provider credentials.
3. Configure production identity, secrets, HTTPS, hosting, a separate worker, PostgreSQL backup/restore, monitoring, and rollback; verify them in staging.
4. Obtain explicit approval before any production import, destructive production database operation, or public deployment.
