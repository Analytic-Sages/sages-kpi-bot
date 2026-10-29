# Polaris Phase 2 — Field Policy, Approval and Safe Mutation Contract

Status: requirements captured; workbook header and protected-range verification is still required before enabling any write policy.

> Implementation note (dry-run foundation): `app/change_requests/policy.py` now provides typed `ChangeProposal` / `PolicyContext` models and a pure `validate_proposal` validator. It uses the proposed names below as a fail-closed contract, fingerprints the complete header-ordered row snapshot, rejects stale expected values and formula-owned fields, and validates the configured field/tier and task/KPI value rules. It performs no Sheets I/O, does not configure a live mutation policy, and does not establish that the live workbook matches these proposed headers. Do not treat a successful validation as permission to write.

Google Sheets remains the operational source of truth. This document translates the approved operational specification into implementation and acceptance requirements. Names below are proposed workbook header names supplied by the operator; they are not a claim that the current live workbook has been inspected. The implementation must read row 1 and compare it with an explicitly approved schema before any write.

## Non-negotiable safety gates

- Keep `DRY_RUN=true` and `ENABLE_TEAM_WRITES=false` in production while this phase is built and tested.
- Add an independent `ENABLE_BOT_WRITES=false` deployment kill switch and a separate workbook config cell `Cfg_BotWritesEnabled`. Both must be explicitly enabled for a future approved write rollout; neither is implied by `Cfg_BotEnabled`, `ENABLE_TEAM_WORKFLOWS`, `ENABLE_SAFE_TELEGRAM_REPLIES`, or any general bot-enabled flag. The deployment-level setting and mutation-service gate are introduced in this increment; workbook config parsing and production wiring remain deferred until the Config worksheet schema is verified.
- Do not enable any write capability because the admin dashboard is accessible or because read-only analytics pass.
- Use a copied workbook, separate test bot token, and test Telegram group for all write-path tests.
- Blocker Log writes are a separate, narrowly allowlisted capability; they must not implicitly update the Master Task Tracker.
- Every shared task/KPI mutation goes through `SafeSheetMutationService`. No command handler, API route, or callback may write to a shared worksheet directly.
- Never report a mutation as applied until the service reads it back and verifies the expected result.
- On timeout or an uncertain outcome, do not blindly retry; inspect the request/audit state first.

## Read-only workbook audit implementation

The read-only audit is implemented in `app/change_requests/workbook_audit.py` and is included in `app.preflight.sheet_findings`. The Sheets client reads formula-visible values with the FORMULA render option; the audit reports only worksheet/field names, formula counts, and severity. It never returns cell contents or formula expressions in the preflight report. Missing proposed policy headers or formulas in proposed writable fields are blockers; missing formula evidence is a warning and does not prove a field is safe. The audit is not an approval to write, and must be run in the configured deployment before a human reviews the live workbook and protected ranges.

## 1. Worksheet and field policy

### Tier 0 — never writable by the bot

Human-managed fields include KPI name, Definition, Direction, Frequency, Unit, Data Source, Owner, `Active?`, Task ID, Task Name, Task Type, Team, Assigned By, Date Assigned, and all Weekly Review fields.

Never write formula-owned grey-header columns, including Assigned To ID, Days To Deadline, Remind Now?, Escalate?, and formula-driven Owner Person ID values. The currently inspected Master Task Tracker uses a human-managed Status dropdown and a manually entered % Complete field; these are Tier 1 fields, not formula-owned. Keep Status changes fail-closed until the live authoritative cell and allowed dropdown values are verified. Do not require an Achievement % header in Master Task Tracker or KPI Register unless the workbook owner approves that schema explicitly. Never write Person Digest, Dashboard, or Team Roster cells.

Bot-owned reminder-tracking columns (for example Last Reminded At, Reminder Count, and Snooze Until) are a separate service-only policy and are not exposed as user-editable fields.

### Tier 1 — self-service, after explicit confirmation

For the task owner or an authorized manager, subject to ownership and transition validation:

- Master Task Tracker: Status (only approved transitions), % Complete, Remarks / Blocker, Output / Link, and Completed Date (only when status is Completed).
- Weekly KPI Log: Actual, Comment, and Next Action.
- Teal task-type-specific detail columns: only when the Task Type matches the configured field policy.

### Tier 2 — approval required

- Master Task Tracker: Deadline, Assigned To, Priority, moving to Completed, and reopening a closed task.
- KPI Register: Target.
- Weekly KPI Log: Target Override.

The KPI owner may propose a Target change; the Team Lead or Operations Manager must approve it. Reassignment/reopening requires approval by the assigner, Team Lead, or Operations Manager. No requester may approve their own request.

### Required policy properties

- Policies are explicit per worksheet, field, actor role, and task type. Unknown fields fail closed.
- Resolve all fields by exact approved header name, never by hard-coded column letter.
- Read row 1 and reject missing, renamed, blank, or duplicate headers before mutation.
- Read target cells in formula-visible mode and reject any target that currently contains a formula.
- Use cell-scoped writes only. Never rewrite a whole row or broad range for a task/KPI mutation.
- Write user-provided text as literal data; reject or safely neutralize formula-injection prefixes `=`, `+`, `-`, and `@` without changing the intended visible value unexpectedly.
- The policy must explicitly distinguish a formula-derived Status column from any human-editable status field. Do not activate a Status policy until the live workbook identifies which cell is authoritative.

## 2. Blocker Log worksheet

The proposed bot-only, append-only worksheet fields are:

1. Blocker ID (unique)
2. Logged At
3. Task ID (optional)
4. Reported By ID / Chat ID
5. Telegram Message ID (deduplication key)
6. Description (sanitized and length-limited)
7. Status (Open / Resolved)
8. Resolved At / Resolved By
9. Request ID (audit correlation)

The bot may append only to this explicitly configured worksheet after its header schema, sharing/protection settings, and test copy are verified. Human edits are out of scope. Duplicate Telegram message IDs must not create duplicate blocker records. Logging a blocker must not automatically mutate a task. A separate confirmed/approved task-status request is required to set a task to Blocked.

## 3. Identity, ownership and validation

### Identity and authorization

- Resolve the Telegram Chat ID against the People Directory; reject unknown or inactive people.
- Resolve Task ID to exactly one record; reject missing or duplicate IDs.
- Check field-specific authorization: Assigned To, Assigned By, the team's Team Lead, or Operations Manager, as permitted by the field tier.
- Verify the acting user and approver server-side at the moment the request is applied. Never trust user IDs carried only in callback data.

### Allowed status transitions

| Current status | Allowed next status |
| --- | --- |
| Not Started | In Progress, Blocked, On Hold, Cancelled |
| In Progress | In Review, Blocked, On Hold, Cancelled; Completed only with approval |
| In Review | In Progress, Blocked; Completed only with approval |
| Blocked / On Hold | In Progress, Cancelled |
| Completed / Cancelled | Closed |
| Closed | Reopening requires manager approval |

The exact source values and any formula-derived status behavior must be verified against the live workbook before activation.

### Value validation

- % Complete is an integer from 0 through 100. Completed requires 100.
- Deadline must parse as a valid date and must not precede Date Assigned.
- Actual and Target must be finite numeric values; Target must be greater than zero.
- Week Start must be one of the explicitly allowed Mondays.
- A `/goal` command must resolve exactly one active KPI matching the name and owned by the sender.
- Multi-field requests must validate all fields before any write is attempted.

### Stale values and conflict detection

At preview time, store the old value of every field and a fingerprint of the relevant row snapshot. Immediately before applying, re-read headers, row values, and target-cell formula state. If the snapshot differs, set the request to CONFLICT, show the current value, and require a fresh preview and confirmation. Never overwrite a concurrent human edit silently.

## 4. Change request state machine

Allowed states:

`DRAFT → AWAITING_CONFIRMATION → AWAITING_APPROVAL → APPROVED → APPLIED`

Terminal or failure states: `REJECTED`, `EXPIRED`, `CONFLICT`, `FAILED`, `CANCELLED`.

- Tier 1: show before/after preview; requester confirms or cancels.
- Tier 2: requester confirms the proposal, then a separate authorized approver approves or rejects it.
- Confirmation expires after 10 minutes. Approval expires after 48 hours, with at most one reminder.
- Approver order: Assigned By, Team Lead, Reports To, then `Cfg_ManagerChatID`, subject to authorization and no self-approval.
- Buttons carry an unguessable, one-time Request ID. Validate the clicking Chat ID, current state, expiry, and authorization server-side.
- Replayed or double-clicked callbacks must be idempotent and must not apply a second write.
- Persist pending requests in a Change Requests worksheet or a durable database, not in the Master Task Tracker. The storage choice must support concurrency and restart recovery.

## 5. Audited mutation contract

Every shared task/KPI write must pass through the mutation service with:

- Request ID (idempotency key)
- Actor ID and, where required, Approver ID
- Worksheet and stable row key
- Field, old value, new value, and reason
- Source message/callback ID and environment
- Before/after snapshot and outcome

Audit order:

1. Record mutation intent.
2. Revalidate schema, authorization, field policy, formula state, and expected values.
3. Apply the smallest safe cell-scoped write (or a truly atomic batch where supported).
4. Read back and verify every changed value.
5. Record the final outcome.

The append-only Audit Log should include Audit ID, Timestamp, Request ID, Actor ID, Approver ID, Sheet, Row key, Field, Old, New, Outcome, Error, Source message ID, and Environment. Do not log secrets. A correction is a new audited mutation; never edit audit history.

A multi-field change must not be described as atomic unless the chosen Sheets API operation guarantees the required atomicity. If a timeout leaves the result uncertain, mark it UNCERTAIN/FAILED_PENDING_RECONCILIATION and reconcile before any retry.

## 6. Controlled testing matrix

Test only against a copied workbook, separate bot token, and test group.

- Success: self-update Status, % Complete, Actual; approved Target change; blocker append; separately confirmed task status update to Blocked.
- Rejections: unknown/inactive user, wrong owner, invalid or duplicate Task ID, illegal status jump, non-numeric Actual, past Deadline, duplicate KPI names, unauthorized or tampered button.
- Conflicts: edit the row between preview and confirmation; verify no silent overwrite.
- Duplicates: resend the same Telegram message, double-tap Confirm, replay Approve, and retry the same Request ID; each request applies at most once.
- Failures: Sheets API error, timeout, expired credentials, protected cell, changed header, formula in target cell, and partial/uncertain multi-field outcome.
- Audit: applied, rejected, expired, conflicted, and failed requests have correct actor, approver, old/new values, Request ID, and source correlation.
- Safety: either write kill switch stops writes; formula cells remain unchanged; blocker append cannot update the Tracker.

## 7. Rollout order and acceptance gates

1. Dry-run only: parse, authorize, validate, and show previews; no writes.
2. Blocker Log only: live append in the controlled test workbook first, then a specifically approved limited rollout.
3. Tier 1 changes: controlled pilot, then staged rollout.
4. Tier 2 approvals: only after Tier 1 audit, idempotency, conflict, and restart-recovery gates pass.

Before any production write capability is enabled, record evidence for:
- Live workbook headers and formula ownership verified against this policy.
- Protected ranges and service-account permissions reviewed.
- Controlled workbook test matrix passing.
- Independent write kill switch tested.
- Audit intent/result and uncertain-outcome reconciliation tested.
- Explicit owner approval for the named rollout stage.

Until then, keep all write and notification feature flags disabled.