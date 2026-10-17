# Polaris — Six-Phase Implementation Plan

This plan continues from the accepted read-only analytics baseline. Google Sheets remains the source of truth. Every phase must pass automated tests and its acceptance gate before any later phase is activated.

## Non-negotiable safety state

Keep these values in Render until a separate, explicit production approval:

```dotenv
DRY_RUN=true
ENABLE_TEAM_WRITES=false
ENABLE_BOT_WRITES=false
ENABLE_SCHEDULED_WORKFLOWS=false
ENABLE_ANALYTICS=false
ENABLE_WEBHOOKS=false
ENABLE_SAFE_TELEGRAM_REPLIES=false
ENABLE_DRY_RUN_TEAM_REPLIES=false
ENABLE_TEAM_WORKFLOWS=false
```

No production spreadsheet edits or live messages are used as smoke tests. Secrets stay in the hosting secret store. Every protected API must authenticate and authorize on the server, return `Cache-Control: no-store`, and fail closed on missing/ambiguous source data.

## Phase 1 — Operational record centre

**Accepted by operator after deployed Control Centre smoke test.**

- [x] Authenticated, paginated, read-only task list API with status/team/person/query/overdue filters.
- [x] Authenticated task detail API that rejects missing or ambiguous IDs.
- [x] Authenticated, paginated, read-only KPI register API with owner/team filters and latest recorded observation.
- [x] Same-origin task and KPI tables with loading/error/empty states and pagination.
- [x] CI passes on the merged main commit.
- [x] Operator confirmed the deployed Control Centre loads, authenticates the admin, and displays the read-only boundary.

**Exit gate:** the dashboard reads live records without writes, unauthorized requests return 401, and source failures return sanitized 503 responses.

## Phase 2 — Audited mutation and approval workflow

Implementation contract: [`PHASE_2_FIELD_POLICY_AND_APPROVALS.md`](PHASE_2_FIELD_POLICY_AND_APPROVALS.md). The proposed workbook header names still require live workbook and protected-range verification before write policies are activated.

- Add a separate `ENABLE_BOT_WRITES=false` kill switch, independent of general bot and workflow flags.
- [x] Add a durable SQLite change-request state store for DRAFT, confirmation, approval, applied, rejected, expired, conflict, failed, and cancelled states.
- [x] Enforce requester identity, separate Tier 2 approver identity, no self-approval, 10-minute confirmation expiry, and 48-hour approval expiry in the state store.
- [x] Persist append-only request-transition events and deduplicate source messages / request IDs.
- [x] Add typed change proposals and fail-closed pure policy validation for task/KPI changes (dry-run model only; not wired to Sheets or user-facing handlers).
- [ ] Verify the live workbook headers and formula ownership before enabling any worksheet allowlist.
- Validate stable IDs, owner references, permitted status transitions, field values, and expected-value snapshots.
- Route all changes through `SafeSheetMutationService`; require actor identity, correlation/request ID, audit intent/outcome, and read-after-write verification.
- Add preview/confirmation UX, conflict refresh, uncertain-outcome state, and idempotent request handling.
- Keep the production write gate off; test against a controlled workbook or explicitly approved pilot rows.

**Exit gate:** tests prove invalid, unauthorized, duplicate, stale, and uncertain writes do not produce false success; formula and neighboring cells are preserved; an owner explicitly approves the pilot.

## Phase 3 — Accountability and reminder engine

- Separate reminder selection, message composition, eligibility, and delivery.
- Persist delivery receipts and cooldown state; deduplicate across retries/restarts.
- Suppress reminders for completed/cancelled tasks, inactive members, ambiguous ownership, missing recipients, and malformed due dates.
- Produce recipient/message previews and dry-run delivery reports first.
- Add per-channel delivery outcomes and safe retry rules; do not retry an uncertain send blindly.
- Keep Telegram delivery and scheduled workflow flags off until a staged recipient audit passes.

**Exit gate:** deterministic tests cover time zones, duplicate schedules, cooldowns, missing recipients, delivery failures, and restart recovery; a human approves the preview.

## Phase 4 — Google Workspace and email integrations

- Add Gmail only with least-privilege scopes and an approved sender identity.
- Add permission-aware Drive retrieval using the connected user's actual authorization; no content is returned after denied or revoked access.
- Store tokens only in the host secret store and redact credentials/identifiers from logs.
- Audit requests and delivery outcomes without logging document contents or secrets.
- Keep email, Drive retrieval, and Telegram independently gated.

**Exit gate:** authorization, revocation, quota/rate-limit, failure, and audit tests pass independently for each integration.

## Phase 5 — Growth intelligence and bounded AI assistance

- Treat existing GA4/YouTube integrations as separate from operational task/KPI data.
- Add source freshness, ingestion health, and metric provenance before presenting growth summaries.
- Start AI with read-only summaries grounded in authorized source data.
- Treat retrieved documents and messages as untrusted content; AI cannot grant access or bypass policy.
- Any proposed operational change must return to the normal validation, preview, approval, and audited mutation path.

**Exit gate:** source attribution, permission enforcement, stale-data warnings, and no-side-effect tests pass; no AI output directly triggers a write or send.

## Phase 6 — Production readiness and controlled rollout

- Verify exact deployed commit, CI, Render health, live Sheets preflight, approved admin/non-admin access, and integration health.
- Confirm persistent storage, backup/restore, process count, scheduler coordination, and restart behavior.
- Review logs, error alerts, secret rotation, rate limits, rollback steps, and incident ownership.
- Enable one capability at a time with a named approver and a defined observation window.
- Record rollout evidence and immediately revert the individual gate if integrity, authorization, or delivery checks fail.

**Exit gate:** all required operational evidence is recorded. Polaris must not be described as production-ready while any mandatory gate remains open.

## Current implementation status

- Read-only admin sign-in, overview, audit history, and team performance analytics are merged on `main`.
- The task/KPI record centre is merged on `main`; CI passed on the merge commit and the operator confirmed the deployed read-only Control Centre.
- PR #36 merged: Phase 2 requirements contract and independent `ENABLE_BOT_WRITES=false` gate are in `main`.
- PR #37 merged: durable SQLite change-request state machine. The current branch adds typed proposals and pure policy validation with stale-row fingerprints, formula-field rejection, field/tier allowlists, status/date/numeric checks, and identity/owner checks. It does not expose command handlers or perform Sheets writes. Live workbook schema and formula ownership remain unverified; all production write and notification flags remain disabled.
- Live mutation policies, workbook schema, formula ownership, protected ranges, and bot identity/approval routing remain unverified until explicitly checked.
