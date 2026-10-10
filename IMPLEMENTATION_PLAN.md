# Polaris Implementation Plan

**Repository:** `Analytic-Sages/sages-kpi-bot`  
**Operating principle:** complete, test, and review one priority before beginning the next.  
**Safety default:** dry-run enabled; no real Telegram messages until routing and recipient IDs are verified.

## Progress tracker

- [x] **P0 — Repository foundation:** package structure, configuration validation, logging, health endpoints, Dockerfile, CI, baseline tests.
- [x] **P1 — Telegram/service foundation:** async Bot API client, command registration, polling loop, dry-run replies, startup/shutdown handling; CI green in PR #2.
- [x] **P2 — Google Sheets data layer:** header-driven reads/writes, schema validation, stable IDs, bounded retries; CI green in PR #3. Live workbook/service-account verification remains a production gate.
- [x] **P3 — Accountability engine:** formula-driven task flags, reminder receipts, cooldowns, per-day limits, escalation safety; merged in PR #4. Live task polling remains gated on workbook verification.
- [ ] **P4 — Team workflows and role intelligence:** `/status`, `/goal`, `/blocker`, `/jd`, role/people matching, drift/bottleneck checks.
- [ ] **P5 — Scheduled workflows:** Monday KPI broadcast, Tue–Fri focus prompt, Friday checkout, timezone-aware scheduler.
- [ ] **P6 — Growth analytics:** GA4/Search Console as explicitly configured, YouTube channel metrics; private/public routing enforced.
- [ ] **P7 — Webhooks, deployment, operations:** authenticated idempotent events, observability, deployment runbook, final acceptance tests.

## P0 acceptance criteria

- [x] App imports and serves HTTP health endpoints.
- [x] Environment configuration validates timezone, port, logging level, and safety defaults.
- [x] No credentials or production secrets are committed.
- [x] CI installs the project, runs Ruff, and runs pytest.
- [x] Readiness response does not falsely claim external integrations are connected.
- [x] Local Docker image definition is included.

## P1 acceptance criteria

- [x] Telegram commands are registered without hard-coded credentials.
- [x] Polling is isolated from the HTTP event loop and handles recoverable API errors.
- [x] Replies respect `DRY_RUN` and no real sends occur by default.
- [x] Startup and shutdown clean up the polling task and HTTP client.
- [x] Ruff and pytest pass in CI.

## P2 acceptance criteria

- [x] Read and write records by header name rather than fixed column indexes.
- [x] Reject blank/duplicate headers and unknown write columns.
- [x] Reject ambiguous stable-ID matches instead of updating multiple people/tasks.
- [x] Keep blocking Google client calls off the async event loop and retry failed API calls with bounded attempts.
- [ ] Verify the live workbook's exact headers, formula fields, service-account access, and authoritative Job Descriptions source before enabling task reminders.

## P3 acceptance criteria

- [x] Reminder eligibility requires formula-driven Remind Now? and Escalate? fields; missing flags block safely.
- [x] Verify active status, Notify?, Reachable?, owner identity, and recipient chat ID before a send.
- [x] Escalations route to Manager Chat ID; missing recipient IDs block delivery.
- [x] Persist successful delivery receipts in SQLite; failed sends release their claim and do not create receipts.
- [x] Enforce cooldowns, a per-task daily cap, and expiring claims to prevent concurrent duplicate sends.
- [x] Dry-run performs no send and creates no receipt.
- [ ] Wire into live task polling only after workbook headers, formula flags, IDs, and service-account permissions are verified.

## P4 acceptance criteria

- [x] `/status` resolves one active team member from their Telegram chat ID and summarizes assigned active KPIs and the latest weekly result.
- [x] `/goal` updates only one active KPI owned by the authenticated requester; ambiguous/missing matches and invalid targets do not write.
- [x] `/blocker` does not claim a blocker was saved unless a configured, schema-validated destination accepted it.
- [x] `/jd` reads only from an explicitly configured authoritative Job Descriptions worksheet; People Directory notes are never treated as JDs.
- [x] Team workflows are opt-in with `ENABLE_TEAM_WORKFLOWS=false` by default.
- [ ] Enable team workflows only after verifying live Telegram identity mapping, KPI fields, blocker destination, authoritative JD worksheet, and service-account write permissions.
- [ ] Add drift/bottleneck checks after source data and ownership fields are verified.

## Design constraints to preserve

1. Google Sheets remains the operational source of truth for team/role/KPI/task state.
2. Task reminder and escalation decisions are driven by spreadsheet flags such as `Remind Now?` and `Escalate?`, not duplicate hard-coded business rules.
3. Reminder receipts and cooldowns must be persisted; successful delivery must be confirmed before receipts are written.
4. Public/community announcements and private growth analytics must use separate Telegram destinations.
5. Directory and tracker columns are resolved by header names, never fixed numeric column positions.
6. People records use stable IDs. Leaving staff should not erase historical KPI/task ownership.
7. `Notify?`, `Reachable?`, status, and recipient IDs must be checked before messaging.
8. `Social Workstream` and `Design Workstream` legacy notices direct users to `Master Task Tracker`.
9. Role/job description checks must use an authoritative Job Descriptions source; `People Directory.Notes` is not assumed to be a job description.
10. GA4 and YouTube reports need deduplication, correct metric aggregation, and private routing. YouTube RSS alone is insufficient for comment metrics.
11. External API calls must not block the async event loop; retry and rate-limit handling must be explicit.
12. All production sends are disabled until IDs, permissions, workbook headers, and routing are verified.

## Workbook schema verification

The supplied optimized workbook currently shows a compact schema that differs from the earlier bot-ready schema in the implementation notes. For example, its `People Directory` has `Person ID`, `Full Name`, `Team`, `Role`, `Email`, `Start Date`, `End Date`, `Status`, `Replacement For`, `Notes`; its `Master Task Tracker` has `Task ID`, `Task Name`, `Date Assigned`, `Team`, `Assigned To ID`, `Assigned To`, `Priority`, `Deadline`, `Status`, `% Complete`, `Output / Link`, `Remarks`. The live Google Sheet may differ; do not hard-code either variant until its actual headers are read.

**Still to verify before P2/P3:** live Google Sheet ID and service-account access, exact live `Master Task Tracker` headers, whether formula fields `Remind Now?` and `Escalate?` exist in the live sheet, authoritative Job Descriptions tab/file, and whether analytics scope includes Search Console in addition to GA4/YouTube.

## Verification commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest --cov=app --cov-report=term-missing
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/health/live` and `/health/ready`.
