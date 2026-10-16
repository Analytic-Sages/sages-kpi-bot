# Polaris Implementation Workflow

This document is the build checklist for extending the existing Polaris application. Work proceeds one stage at a time. Do not start a later stage until the current stage's exit gate is recorded as passed.

## Project direction

- Product: admin-only control centre for Polaris.
- Existing application: FastAPI + Telegram bot + APScheduler.
- Operational source of truth: Google Sheets for people, tasks, and KPIs.
- Durable supporting state: audit events, reminder receipts, delivery outcomes, and synchronization metadata.
- Future UI: Next.js + React + TypeScript, using authenticated backend APIs only.
- Future integrations: Gmail, permission-aware Google Drive, and bounded AI assistance.
- Do not rewrite the application or duplicate task/KPI authority in a new database.

## Safety invariants

Until separately reviewed and approved, keep the following Render configuration:

```dotenv
DRY_RUN=true
ENABLE_TEAM_WRITES=false
ENABLE_SCHEDULED_WORKFLOWS=false
ENABLE_ANALYTICS=false
ENABLE_WEBHOOKS=false
ENABLE_SAFE_TELEGRAM_REPLIES=false
ENABLE_DRY_RUN_TEAM_REPLIES=false
ENABLE_TEAM_WORKFLOWS=false
```

- No live write is used as a smoke test.
- No frontend code contains Google credentials, OAuth refresh tokens, or privileged backend secrets.
- Authorization is enforced server-side on every protected route.
- Google Sheets field writes are allowlisted; formula-derived columns and unrelated cells are never overwritten.
- Ambiguous IDs, duplicate matches, unknown owners, stale edits, and invalid transitions fail closed.
- A request is not reported successful until the result has been verified. An uncertain network outcome is surfaced as uncertain, not blindly retried.
- Each external side effect has an independent feature flag and acceptance gate.
- Never place personal Telegram IDs, employee records, or secrets in public issues, commits, test fixtures, or logs.

## Stage 0 — Baseline and discovery

### Tasks
- [x] Confirm repository default branch and current main commit.
- [x] Inventory core application entry point, configuration, Google Sheets client, Telegram workflows, scheduler, and representative tests.
- [x] Inspect CI configuration and available status metadata; actual pass/fail remains unverified.
- [ ] Inspect workbook header contracts against the live workbook and distinguish human-editable from formula-derived fields.
- [ ] Review current auth/access controls; identify an approved admin authentication provider before adding write endpoints.
- [ ] Confirm Render deployment shape, process count, persistent storage, and available frontend deployment target.
- [ ] Re-run protected read-only preflight after the Operations Manager completes workbook updates.
- [ ] Capture current health results and known blockers without exposing private records.

### Exit gate
- [ ] Baseline report identifies existing functionality, gaps, tests, deployment facts, and decisions still requiring owner approval.
- [x] Source-level assumptions are labelled separately from runtime/deployment facts.

## Stage 1 — Shared services and safe write path

### Tasks
- [ ] Separate task/KPI business rules from Telegram-specific command handling where needed.
- [ ] Define typed, allowlisted mutation schemas for supported task/KPI fields.
- [ ] Map each editable field to its exact worksheet/header and validate against the live schema.
- [ ] Reject absent or non-unique task/KPI IDs; do not rely on fuzzy names for mutations.
- [ ] Validate owner/person references and permitted status transitions.
- [ ] Preserve formula-driven columns and update only targeted cells.
- [ ] Add dry-run previews that show intended field-level changes without mutating Sheets.
- [ ] Define stale-write detection using a snapshot/version/hash or equivalent comparison supported by the source.
- [ ] Define behavior for partial writes, timeouts, and uncertain write outcomes; avoid blind retries.
- [ ] Add audit events for actor, record ID, changed fields, before/after values where safe, timestamp, request/correlation ID, and result.
- [ ] Add unit tests with fake Sheets clients and integration tests against a controlled test workbook.

### Exit gate
- [ ] Valid writes are previewed correctly in dry-run.
- [ ] Unauthorized, malformed, ambiguous, stale, and invalid writes fail closed.
- [ ] Formula/unrelated-cell preservation and audit behavior are covered by tests.
- [ ] Production write flag remains disabled.

## Stage 2 — Authenticated admin API

### Candidate endpoints (finalize after baseline)
- `GET /api/v1/admin/overview`
- `GET /api/v1/admin/tasks`
- `GET /api/v1/admin/tasks/{task_id}`
- `PATCH /api/v1/admin/tasks/{task_id}`
- `GET /api/v1/admin/kpis`
- `PATCH /api/v1/admin/kpis/{kpi_id}`
- `GET /api/v1/admin/audit-events`
- `GET /api/v1/admin/reminder-deliveries`
- Existing protected read-only `GET /admin/preflight` remains compatible unless a reviewed migration is approved.

### Tasks
- [ ] Choose admin authentication/session strategy and identity source before exposing write APIs.
- [ ] Add server-side role/permission checks and deny-by-default behavior.
- [ ] Add request validation, pagination, filtering, safe error models, and correlation IDs.
- [ ] Add rate limiting/abuse controls appropriate to the chosen deployment/auth strategy.
- [ ] Add API tests for anonymous, non-admin, admin, invalid input, conflicts, provider failures, and sanitized responses.
- [ ] Publish an API contract/OpenAPI review artifact for frontend work.

### Exit gate
- [ ] Protected APIs are tested independently of the frontend.
- [ ] No endpoint can be used to bypass service-layer validation or write flags.

## Stage 3 — Admin-only frontend

### Initial screens
- [ ] Sign-in/session handling and access-denied state.
- [ ] Overview: tasks, overdue/blocked work, KPI health, integration state, and data freshness.
- [ ] Task list/detail/edit with allowlisted fields, confirmation, conflict refresh, and outcome states.
- [ ] KPI list/detail/edit for approved fields.
- [ ] Audit history.
- [ ] Reminder/delivery monitoring when backend delivery records are available.
- [ ] System/preflight status and safe configuration visibility.

### Tasks
- [ ] Build with Next.js, React, and TypeScript after the API contract is stable.
- [ ] Use the backend API exclusively for operational reads/writes.
- [ ] Keep privileged secrets out of browser bundles and client storage.
- [ ] Implement secure session handling, CSRF protections where applicable, and safe rendering.
- [ ] Show loading, empty, stale, partial-failure, unauthorized, conflict, and retry states explicitly.
- [ ] Add component and end-to-end tests for admin-only access and mutation workflows.

### Exit gate
- [ ] A non-admin cannot access admin pages or protected APIs.
- [ ] Admin changes go through the same tested backend service and audit path as other interfaces.
- [ ] The UI does not claim writes succeeded unless the API returns a verified result.

## Stage 4 — Controlled write acceptance

- [ ] Confirm test workbook or explicitly approved pilot rows and exact editable fields.
- [ ] Test a valid edit, invalid field, missing record, duplicate record, invalid owner, stale edit, and permission denial.
- [ ] Test Sheets API failure before write and connection loss after a write may have succeeded.
- [ ] Confirm formulas and neighboring rows/cells remain intact.
- [ ] Confirm audit event and read-after-write verification.
- [ ] Test duplicate submission and safe retry behavior.
- [ ] Document rollback/recovery and identify the approver/operator.
- [ ] Obtain explicit approval before any production write flag is changed.

### Exit gate
- [ ] All tests pass on controlled data and evidence is recorded.
- [ ] No production write flag is enabled by this checklist alone.

## Stage 5 — Reminders and integrations, separately gated

### Reminder engine
- [ ] Confirm task fields, recurrence rules, timezone, recipient mapping, priority, and escalation policy.
- [ ] Persist delivery receipts and cooldowns on durable storage.
- [ ] Prove idempotency across scheduler repeats and process restarts.
- [ ] Record Telegram and email outcomes separately; retry only the failed channel where safe.
- [ ] Suppress reminders for completed tasks, inactive people, missing recipients, or invalid task data.
- [ ] Observe dry-run recipient previews before approving any live sends.

### Gmail
- [ ] Use least-privilege OAuth scopes and approved sender identity.
- [ ] Store tokens only in the host secret store.
- [ ] Test successful send, authorization failure, rate limit, retry, and delivery receipt behavior.

### Google Drive
- [ ] Define folder registry and the authorization source of truth.
- [ ] Enforce the requester's access before retrieving or summarizing content.
- [ ] Test direct, inherited, group, and revoked access; denied access must return no content.
- [ ] Treat document contents as untrusted data, not system instructions.

### AI
- [ ] Add only after deterministic authorization, source retrieval, and audit behavior pass.
- [ ] Start with summaries and read-only questions that cite source records/documents.
- [ ] AI cannot authorize access, decide to bypass policy, or directly mutate Sheets.
- [ ] Any AI-proposed change must go through normal validation and explicit human approval.

### Exit gate
- [ ] Each integration has independent flags, tests, monitoring, rollback, and approval.
- [ ] Do not activate multiple side-effecting integrations together for the first time.

## Stage 6 — Deployment and operations

- [ ] CI lint/tests pass on the exact candidate commit.
- [ ] Deploy to staging or controlled environment first.
- [ ] Verify health endpoints, Google Sheets preflight, Telegram routing, and logs.
- [ ] Confirm persistent storage and single-instance/scheduler coordination assumptions.
- [ ] Verify secret configuration without printing secret values.
- [ ] Record commit SHA, reviewer, preflight outcome, tests, feature flags, approval, and rollback decision.
- [ ] Enable only the explicitly approved capability.
- [ ] Observe the agreed pilot window and review failures before expansion.

## Definition of done

- Automated lint and test suites pass.
- Protected endpoints have authorization tests.
- Invalid, ambiguous, stale, and unauthorized writes cause no unintended mutation.
- Verified successful writes preserve formulas and are audited.
- Admin UI uses backend APIs only and enforces server-side authorization.
- Reminder receipts survive restart and duplicate sends are prevented.
- Integrations are enabled independently only after acceptance.
- The deployed commit and acceptance evidence are recorded.
- Polaris is not labelled production-ready while any required gate is incomplete.

## Decision log

- Admin web interface: **admin-only control centre**.
- Task editing: **approved edits go through Polaris backend and are written to Google Sheets**.
- Operational source of truth: **Google Sheets**.
- Existing FastAPI/Telegram system: **extend, do not replace**.
- Initial write/scheduler/integration state: **disabled / dry-run until acceptance**.


## Baseline report — 2026-10-17

### Verified from repository source
- Default branch is `main`; repository: `Analytic-Sages/sages-kpi-bot`.
- Current application is a FastAPI service with a Telegram polling supervisor, optional team workflows, APScheduler workflows, analytics, protected read-only preflight, and a signed inbound webhook receiver.
- Google Sheets access is header-driven. The existing client rejects missing/duplicate stable record keys and unknown headers, and writes targeted cell ranges rather than rewriting entire rows.
- Existing team commands include `/status`, `/goal`, `/blocker`, and `/jd`; private identity checks reject team commands in group chats.
- Existing settings include safe defaults for dry-run and independently gated team writes, scheduled workflows, analytics, and webhooks.
- CI configuration in `.github/workflows/ci.yml` runs Ruff and pytest with coverage on pushes to `main`/feature branches and pull requests to `main`.
- Existing tests include Sheets schema/client tests, team workflow tests, config tests, preflight tests, and row-integrity tests.
- Existing runbook is `docs/SAFE_STAGING_ROLLOUT.md`; it remains authoritative for existing workflow activation gates.

### Gaps to address before any admin write endpoint
- The only admin HTTP endpoint identified in the entry point is the protected read-only `/admin/preflight`; an admin sign-in/session and role authorization system has not yet been identified.
- `GoogleSheetsClient.update_record` validates headers and unique keys, but it does not by itself enforce a business-specific allowlist of editable fields, stale-snapshot protection, audit logging, or read-after-write verification.
- The Sheets client's generic retry wrapper is used around writes as well as reads. The write path needs explicit handling for timeouts/uncertain outcomes before being exposed to a UI; blind retries must not be treated as verified success.
- Telegram command and delivery logs have been updated to omit chat/sender IDs in merged PR #24. Continue avoiding personal identifiers in new logs.
- No frontend package or admin dashboard was verified in the files inspected so far.
- The actual Google Sheets workbook headers/formula ownership must be confirmed before finalizing editable-field allowlists.

### Stage 0 status
**In progress — not passed.** Repository architecture and several critical files have been inspected. The following remain open:
- [ ] Record the exact current `main` commit SHA and CI status.
- [ ] Inspect the remainder of the Sheets client, reminder receipt persistence, scheduler, and relevant tests.
- [ ] Confirm the admin authentication/session approach and approved admin identity source.
- [ ] Verify the current Render deployment/instance/persistent-storage setup.
- [ ] Re-run the protected read-only preflight after the Operations Manager finishes workbook edits.
- [ ] Capture current deployed health and preflight results without including secrets or personal identifiers.

Do not begin Stage 1 production-facing mutation work until the open baseline items are resolved or explicitly documented as blocked, with an agreed safe test strategy.


### Runtime verification update

- Latest `main` commit observed from GitHub: `6bebdf1a8d8db02e78a599016d79c362d3c7fdba`, merging the Telegram log-redaction fix in PR #24.
- PR #23 (implementation workflow) and PR #24 (Telegram log redaction) are both merged.
- The status lookup returned no status contexts for the latest merge commit, so current CI status remains **unknown**, not green.
- The first CI run for the safe-mutation PR failed Ruff on three overlong lines. Those lines were corrected; a passing CI run for the corrected head has not yet been observed.
- The current verification channel could not retrieve the live Render health endpoints. No current production health claim is made.
- The last known protected preflight result from the prior staging test was blocked by incomplete KPI configuration/owner references. Rerun after the Operations Manager confirms workbook edits are complete.
- Stage 1 implementation is in draft PR #25. It adds a reusable service and tests but is not wired into FastAPI, has no admin API, and has no production mutation policy for the task tracker.

**Current gate: Stage 0 runtime/data checks remain open, and Stage 1 is under review.** Do not treat the implementation PR as deployed or production-approved.
