# Safe Google Sheets Mutation Service

## Scope

This is the first implementation slice for Stage 1. It adds a reusable backend service; it does **not** expose an HTTP endpoint, authenticate an administrator, enable production writes, or wire mutations into application startup.

Google Sheets remains the operational source of truth. The service requires a per-worksheet SheetMutationPolicy supplied by trusted backend code. A worksheet with no explicit policy cannot be mutated.

## Safety behavior

- Dry-run is the default and previews never call the Sheets update method.
- A real update requires both dry_run=False and writes_enabled=True.
- A verified actor identifier is required by the service; the future API must derive this identity from a validated server-side session, never from a request body.
- Every changed field must be explicitly allowlisted and include the expected value loaded by the caller.
- The service rejects missing/duplicate stable record IDs, unknown fields, stale snapshots, formula-like user input, and field values rejected by configured validators.
- The Sheets adapter rechecks expected values in the same grid snapshot it uses to locate the row, then updates only the targeted cells using RAW input.
- An intent audit event is persisted before a live write. The service then reads the record back and only returns applied after the changed values match. Provider failures or failed verification return an uncertain outcome and must be reconciled before retrying.
- Audit records are stored in SQLite at AUDIT_DB_PATH (default data/polaris-audit.sqlite3) when the host constructs SQLiteAuditLog. The host must provide durable persistent storage before using this in production.

## Current policy boundary

The current implementation has been tested against the known KPI Register Target field in fixtures. This does **not** mean a production policy has been configured or that the live workbook has been inspected.

Do not add editable fields for Master Task Tracker until the actual workbook headers, human-editable columns, formula columns, permitted status transitions, and ownership rules have been confirmed. Do not expose this service via an admin endpoint until a server-side admin authentication and authorization design is approved.

## Concurrency limitation

The expected-value comparison detects stale data observed before the update and protects against concurrent calls through this service instance. Google Sheets does not provide an atomic compare-and-swap operation through the current repository interface, so a human edit from another client can still race between the last read and the write. This service must not be described as providing a fully atomic lock against external workbook editors. Before production acceptance, decide whether to use a stronger revision/locking strategy or constrain editing during the pilot window.

## Required tests before API integration

- Preview mode makes no write call and records a preview audit event.
- Non-allowlisted field is rejected.
- Missing or duplicate stable record ID is rejected.
- Stale expected value is rejected before the update.
- Formula-like input and invalid numeric targets are rejected.
- Live path records intent, writes only allowed cells, verifies by read-after-write, then records a verified outcome.
- Provider failure or verification failure is not reported as success.
- Audit failure before the write prevents the write.
- SQLite audit events persist across service/store instances.
- Existing Sheets client tests remain green.

The service is not yet connected to FastAPI, and production write flags must remain disabled.
