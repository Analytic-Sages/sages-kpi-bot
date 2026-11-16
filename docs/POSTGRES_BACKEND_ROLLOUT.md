# SageOps PostgreSQL backend rollout

## Implemented in the current branch

- PostgreSQL schema and protected normalized read API.
- Existing Control Centre adapter behind OPERATIONS_DB_READS_ENABLED.
- XLSX importer with actual header-name parsing, business-row filtering, duplicate/reference checks and explicit --apply.
- Google OIDC active-staff identity matching and team-scoped permissions.
- Task creation/progress/manager edits with optimistic concurrency.
- KPI creation/maintenance and KPI result submission.
- Weekly review submission, action management and listing.
- Confirm-then-approve task change requests, separate approver and append-only audit events.
- Private Telegram webhook commands with secret validation and update ID deduplication.
- Separate worker for reminder/escalation queueing, retrying and delivery records.
- Daily parity verifier for the seven-day shadow period.

## Provisioning and migration

1. Provision managed PostgreSQL with automated backups and point-in-time recovery.
2. Set DATABASE_URL in the staging secret store.
3. Run alembic upgrade head.
4. Run python -m scripts.import_sageops_workbook workbook.xlsx --report reconciliation.json.
5. Review every duplicate and reference issue. The importer will not apply unless ready=true.
6. Take a database snapshot and run the importer with --apply. The database must be empty.
7. Verify GET /api/v1/operations/health and the normalized records as an admin.

The uploaded workbook currently has 9 teams, 9 people, 126 KPI definitions and 1 populated task. KPI117–KPI126 have no Owner Person ID. The reconciliation report therefore blocks import until these ten KPI ownership fields are resolved. Weekly KPI Log and Weekly Review currently have no populated rows. Do not invent owner assignments.

## Authentication and authorization

Google OIDC admin accounts remain allowlisted through ADMIN_EMAILS. Other users may sign in only when their verified email matches an active People Directory record in PostgreSQL. Staff permissions are team-scoped: team leads/manager roles can manage their team; task assignees can update their own progress; KPI owners can submit results; team leads/managers submit weekly reviews. All writes require X-CSRF-Token.

## Write and notification gates

- OPERATIONS_DB_WRITES_ENABLED=false by default. Enable only in staging after database migration and import.
- OPERATIONS_NOTIFICATIONS_ENABLED=false by default.
- Telegram webhook requires TELEGRAM_WEBHOOK_SECRET and must be configured with Telegram's setWebhook secret_token option.
- Actual delivery also requires TELEGRAM_BOT_TOKEN, the database bot_enabled setting, and DRY_RUN=false.
- Keep legacy Google Sheets writes disabled after PostgreSQL cutover. PostgreSQL is the only writer.

Run the separate worker using python -m app.operations_worker. It records durable notification state and job runs. Review queue failures and retries before enabling live delivery.

## Seven-day shadow period

Export the workbook read-only each day and run:
python -m scripts.sageops_shadow_check workbook.xlsx

The verifier compares team, staff, KPI and task IDs and task statuses, and records each pass/failure in job_runs. Complete seven consecutive local-calendar days of clean parity before requesting production cutover. Investigate all differences; verify backups/restores, access controls, rate limits, webhook security, worker recovery and frontend API compatibility.

## Not yet authorized

No live database has been provisioned or migrated in this session. No workbook records have been imported. The Telegram webhook has not been configured with Telegram. The worker has not been deployed as a separate process. The frontend remains on its existing Sheets-backed read path by default. Seven-day shadow testing has not started. This branch must pass CI and staging integration tests before merge or deployment.
