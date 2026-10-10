# Polaris Implementation Plan

**Repository:** `Analytic-Sages/sages-kpi-bot`  
**Operating principle:** complete, test, and review one priority before beginning the next.  
**Safety default:** dry-run enabled; no real Telegram messages until routing and recipient IDs are verified.

## Progress tracker

- [x] **P0 — Repository foundation:** package structure, configuration validation, logging, health endpoints, Dockerfile, CI, baseline tests.
- [x] **P1 — Telegram/service foundation:** async Bot API client, command registration, polling loop, dry-run replies, startup/shutdown handling; merged in PR #2.
- [x] **P2 — Google Sheets data layer:** header-driven reads/writes, schema validation, stable IDs, bounded retries; merged in PR #3. Live workbook/service-account verification remains a production gate.
- [x] **P3 — Accountability engine:** formula-driven task flags, reminder receipts, cooldowns, per-day limits, escalation safety; merged in PR #4. Live task polling remains gated on workbook verification.
- [x] **P4 — Team workflows and role intelligence:** `/status`, `/goal`, `/blocker`, `/jd`; merged in PR #5.
- [x] **P5 — Scheduled workflows:** Monday KPI snapshot (09:00 UTC), Tue–Fri focus prompt (09:30 UTC), Friday checkout (15:00 UTC); merged in PR #6. Live delivery remains gated on schema/routing/deployment verification.
- [x] **P6 — Growth analytics:** read-only GA4 and YouTube metrics, private core-chat access, opt-in `/growth`; merged in PR #7. Provider credentials and property access remain to be verified.
- [ ] **P7 — Webhooks, deployment, operations:** authenticated idempotent inbound event ledger, deployment runbook, operational health/logging, final acceptance tests.

## P7 acceptance criteria

- [x] Webhook receiver is disabled by default and returns no event details when disabled.
- [x] Signed requests use HMAC-SHA256 over timestamp, event ID, and raw request body; timestamp freshness is checked.
- [x] Body size and event envelope are validated before recording.
- [x] Event IDs are durable/idempotent; identical repeats return duplicate success and conflicting reuse returns 409.
- [x] Event payloads are not executed and are not stored; only metadata and a body hash are persisted.
- [x] Health readiness reports webhook configuration state without claiming external connectivity.
- [x] Deployment, secrets, persistence, single-instance, verification, and rollback guidance documented.
- [ ] CI is green and the PR is reviewed/merged.
- [ ] Validate webhook integration with the actual sender in staging; production use requires HTTPS, persistent storage, a rotated secret, monitoring, and a named owner.

## Design constraints to preserve

1. Google Sheets remains the operational source of truth for team/role/KPI/task state.
2. Task reminder and escalation decisions are driven by spreadsheet flags such as `Remind Now?` and `Escalate?`, not duplicate hard-coded rules.
3. Persist reminder receipts/cooldowns; confirm successful delivery before writing receipts.
4. Public/community announcements and private growth analytics use separate Telegram destinations.
5. Resolve sheet columns by header names, never fixed numeric positions.
6. People records use stable IDs; inactive staff history is retained.
7. Check `Notify?`, `Reachable?`, status and recipient IDs before messaging.
8. Legacy Social/Design Workstream tabs direct users to Master Task Tracker.
9. Job-description checks require an authoritative source; People Directory notes are not JDs.
10. GA4/YouTube analytics remain read-only and private; Search Console is a separate scope decision.
11. External API calls must not block the async event loop; retries and rate limits must be explicit.
12. Production sends remain disabled until IDs, permissions, workbook headers and routing are verified.
13. Inbound webhooks authenticate requests and enforce idempotency; payloads must not cause side effects until each event type has a separately reviewed handler.
14. SQLite requires durable local storage and a single-writer deployment; use an external database before horizontal scaling.

## Outstanding production gates

- Verify live Google Sheet ID/service-account access, actual headers/formula flags, and authoritative Job Descriptions source.
- Verify Telegram core/public chat IDs and single-instance deployment before enabling team workflows or schedules.
- Verify GA4 property access and YouTube API key/channel ID before enabling analytics.
- Verify webhook sender contract, HTTPS termination, secret rotation, persistent storage, and alerting before enabling inbound webhooks.
- Search Console remains out of scope until its property and credentials are confirmed.

## Verification commands

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest --cov=app --cov-report=term-missing
docker build -t polaris:local .
uvicorn app.main:app --reload
```
