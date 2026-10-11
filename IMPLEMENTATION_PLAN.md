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
- [x] **P7 — Webhooks, deployment, operations:** authenticated idempotent inbound event ledger, deployment runbook, operational health/logging; merged in PR #8 and post-merge CI passed.

## P7 acceptance criteria

- [x] Webhook receiver is disabled by default and returns no event details when disabled.
- [x] Signed requests use HMAC-SHA256 over timestamp, event ID, and raw request body; timestamp freshness is checked.
- [x] Body size and event envelope are validated before recording.
- [x] Event IDs are durable/idempotent; identical repeats return duplicate success and conflicting reuse returns 409.
- [x] Event payloads are not executed and are not stored; only metadata and a body hash are persisted.
- [x] Health readiness reports webhook configuration state without claiming external connectivity.
- [x] Deployment, secrets, persistence, single-instance, verification, and rollback guidance documented.
- [x] CI is green and PR #8 is merged.
- [ ] Validate webhook integration with the actual sender in staging; production use requires HTTPS, persistent storage, a rotated secret, monitoring, and a named owner.

## P8 — Production readiness preflight

- [x] Add `python -m app.preflight` read-only checks for configuration presence and the four core worksheet schemas.
- [x] Report missing accountability formula/recipient safety headers as blockers.
- [x] Keep secret values and individual sheet records out of preflight output.
- [x] Add tests for schema comparison, blocker reporting, and Google authentication modes.
- [x] Support Render production Google Sheets OAuth refresh-token credentials without a downloadable service-account private key.
- [x] Preserve support for service-account files and external-account/Workload Identity Federation credential files.
- [ ] Configure the OAuth consent screen and refresh token for the dedicated Google account in Render, then run preflight against the live spreadsheet.
- [ ] Verify Telegram identity/routing, GA4/YouTube access, persistent SQLite storage, HTTPS, and single-instance operation.
- [ ] Complete staging acceptance tests before enabling any live outbound workflows.
- [x] Add private-chat-only team command identity resolution and a private `/whoami` onboarding command.
- [x] Reject group IDs in the People Directory identity preflight.
- [x] Block staging readiness when no real KPI is configured or configured KPI setup fields are incomplete.
- [x] Document the gated rollout and acceptance sequence in `docs/SAFE_STAGING_ROLLOUT.md`.
- [ ] Populate and verify one to three owner-approved pilot KPIs in the live workbook; re-run the protected preflight.
- [ ] Collect and privately map active members' personal Telegram IDs; re-run preflight and test private command routing.

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

- Verify live Google Sheet ID/OAuth access, actual headers/formula flags, and authoritative Job Descriptions source.
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
