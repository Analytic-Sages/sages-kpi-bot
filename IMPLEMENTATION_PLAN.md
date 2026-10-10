# Polaris Implementation Plan

**Repository:** `Analytic-Sages/sages-kpi-bot`  
**Operating principle:** complete, test, and review one priority before beginning the next.  
**Safety default:** dry-run enabled; no real Telegram messages until routing and recipient IDs are verified.

## Progress tracker

- [x] **P0 — Repository foundation:** package structure, configuration validation, logging, health endpoints, Dockerfile, CI, baseline tests.
- [ ] **P1 — Telegram/service foundation:** bot lifecycle, command registration, message routing, startup/shutdown handling.
- [ ] **P2 — Google Sheets data layer:** header-driven reads/writes, schema validation, stable IDs, retry policy.
- [ ] **P3 — Accountability engine:** formula-driven task flags, reminder receipts, cooldowns, per-day limits, escalation safety.
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

## Known workbook schema from supplied update

### People Directory
`Person ID`, `Full Name`, `Team`, `Role`, `Email`, `Telegram Handle`, `Telegram Chat ID`, `Reports To ID`, `Start Date`, `End Date`, `Status`, `Replacement For`, `Notify?`, `Notes`, `Reachable?`, `Manager Chat ID`, `Data Check`, `Last Digest Sent At`

### KPI Register
`KPI ID`, `Team`, `Role`, `KPI`, `Definition / Measurement`, `Direction`, `Frequency`, `Owner Person ID`, `Owner Name`, `Target`, `Unit`, `Data Source`, `Active?`, `Period Start`, `Check-in Due Date`, `Check-in Status`, `Notes`

**Still to verify before P2/P3:** exact `Master Task Tracker` headers, authoritative Job Descriptions tab/file, formulas and expected values for reminder/escalation flags, Google service-account access, and whether the analytics scope includes Search Console in addition to GA4/YouTube.

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
