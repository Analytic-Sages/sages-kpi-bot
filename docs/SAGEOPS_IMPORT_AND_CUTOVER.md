# SageOps import, permissions and cutover

## Workbook contract
The importer reads headers by name from row 1. It considers a row real only when Team Name, Full Name, KPI, or Task Name is populated, ignoring prefilled IDs and formula scaffolding. It imports teams, people, roles, KPIs and tasks. KPI definitions without an owner remain valid team-level KPIs with a null individual owner. Empty weekly KPI and weekly review sheets are reported as zero; no records are invented.

## Import procedure
1. Provision PostgreSQL, set DATABASE_URL, and run alembic upgrade head.
2. Run python scripts/import_sageops_workbook.py workbook.xlsx --report reconciliation.json.
3. Resolve every issue and duplicate ID; ready must be true.
4. Take a database snapshot.
5. Run the same command with --apply. The importer requires an empty database and never writes to Sheets.

Critical task priority is normalized to Urgent. The source workbook remains unchanged.

## Staff access and APIs
A non-admin Google OIDC session must match an active People Directory email. Team leads and manager/head roles receive team management rights. Staff can update their own task progress; KPI owners can submit KPI results; team leads/managers submit weekly reviews. Writes require X-CSRF-Token. Task updates use optimistic version checks.

Task changes can be requested through POST /api/v1/operations/changes, confirmed by the requester, and approved or rejected by a different team manager or admin. Audit history is append-only.

## Notifications
Run python -m app.operations_worker as a separate process. It queues overdue, due-today, due-soon and escalation notifications with deduplication and retry backoff. Actual delivery requires a Telegram token, OPERATIONS_NOTIFICATIONS_ENABLED=true, and DRY_RUN=false. The global bot_enabled setting is an additional kill switch.

## Shadow and cutover
Run at least seven consecutive days with outbound delivery disabled. Compare task totals, due classifications, KPI results, reminder decisions, digests and weekly reviews with the workbook. Investigate differences, test restore from backup, load-test APIs, rotate credentials and obtain explicit approval. Only then enable PostgreSQL reads/writes and retire Sheets writes. PostgreSQL must be the sole writer after cutover.
