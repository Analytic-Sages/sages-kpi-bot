# Polaris Admin API — Read-Only Contract

## Scope and safety boundary

The API is mounted at `/api/v1/admin` and uses the same Google OIDC signed session as `/admin/session`. Every route checks the session server-side against the current `ADMIN_EMAILS` allowlist. Responses include `Cache-Control: no-store`.

This increment is deliberately read-only. It does not expose task/KPI rows, does not invoke Google Sheets, and does not call the Sheets mutation service. Task/KPI browsing and editing remain unavailable until the Operations Manager confirms workbook updates are complete and the live headers, formulas, editable columns, stable IDs, and owner/status rules have been reviewed.

## Endpoints

### `GET /api/v1/admin/overview`

Requires an authenticated admin session.

Returns:
- Current service name, environment, and dry-run state.
- Boolean integration configuration/feature-flag states; no credential values.
- Safety flags, including an explicit `live_sheet_mutations_available: false`.
- Capability flags identifying the current task/KPI schema blocker.
- Whether the configured audit database file exists.

A Sheets status of `configured_not_checked` means only that a sheet ID is configured. This endpoint does not make a Google API request and must not be interpreted as a connectivity or workbook-readiness check.

### `GET /api/v1/admin/audit-events`

Requires an authenticated admin session. Query parameters:
- `limit`: 1–100, default 25.
- `offset`: 0–100000, default 0.
- `outcome`: optional exact outcome filter, up to 32 characters.
- `worksheet`: optional exact worksheet filter, up to 128 characters.

Returns `items`, `total`, `limit`, and `offset`. Items include event ID/time, request ID, actor, action, worksheet, record ID, changed field names, before/after values, and outcome. Because before/after values may be operationally sensitive, this endpoint is admin-only and must not be made public or embedded in client-side telemetry.

The audit store is opened in SQLite read-only mode for listing. A missing database or table returns an empty list without creating files. Database failures return a generic 503 response; internal paths and SQL/provider errors are not returned.

## Admin control centre

The same-origin, dependency-free control centre is served at `/control/`. It uses the existing session cookie and calls only the protected read-only API routes. The CSRF token is held in JavaScript memory for logout and is not written to local storage. All user-controlled/server-provided values are rendered with DOM `textContent`, not interpolated into HTML.

The page includes:
- Google sign-in entry point and signed-in/read-only session state.
- Service configuration and feature-flag overview.
- Explicit display of disabled side-effect gates.
- Paginated audit history with exact outcome and worksheet filters.
- Empty, loading, error, refresh, and session-expiry states.
- A clear notice that task/KPI management is not available until workbook schema approval.

The control centre is hosted by FastAPI under the same origin so the HttpOnly session cookie is sent naturally. Its responses receive a restrictive Content Security Policy, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, and `Cache-Control: no-store`. No frontend secrets or third-party scripts are required.

## Authentication and browser behavior

- Use `GET /auth/login` to begin Google sign-in.
- Use `GET /admin/session` to retrieve the authenticated identity and CSRF token.
- The signed cookie is HttpOnly; frontend code should not read or store it directly.
- Read-only GET endpoints do not need a CSRF header. Any future state-changing endpoint must validate the session and `X-CSRF-Token`, validate authorization on the server, and call the audited mutation service.
- Never put OAuth credentials, session secrets, or CSRF tokens in browser bundles, source control, logs, screenshots, or issue comments.

## Deferred endpoints

No task/KPI list/detail endpoints are published yet. The schema is not approved and the live workbook is being updated. Do not guess header names, expose raw rows, or create worksheet mutation policies from fixture data. The existing protected `GET /admin/preflight` remains separate and continues to require `PREFLIGHT_TOKEN`.

## Verification checklist

- [ ] CI lint and tests pass on the exact pull request commit.
- [ ] Anonymous requests to both endpoints return 401 and no-store headers.
- [ ] Authenticated requests return only the documented fields.
- [ ] Audit listing is bounded, parameterized, and read-only; missing DB/table does not create a file.
- [ ] Live Render API check is completed after deployment without sharing session/CSRF tokens.
- [ ] Persistent storage and single-instance assumptions for SQLite are confirmed.
- [ ] Task/KPI endpoints remain deferred until workbook schema review is approved.


## Team performance analytics

`GET /api/v1/admin/team-performance` requires the same authenticated admin session. Optional query parameters:

- `start_date=YYYY-MM-DD`
- `end_date=YYYY-MM-DD`
- `team=<exact team name>`
- `person=<person ID or exact name>`
- `status=completed|in_progress|open|blocked`

The endpoint reads `People Directory`, `Master Task Tracker`, `KPI Register`, and `Weekly KPI Log` and returns summary metrics, team/member scorecards, overdue/blocked/stale follow-ups, KPI observations, a six-week activity series, and data-quality counts. It is read-only and does not depend on `ENABLE_ANALYTICS`, which remains the separate flag for the existing growth analytics integration. Missing Google Sheets configuration returns 503; source connection/schema failures are sanitized and return 503. Invalid date ranges and filters return 422.

See `docs/TEAM_PERFORMANCE_ANALYTICS.md` for header aliases, calculation semantics, and limitations. Treat the dashboard as operational visibility, not a formal performance rating, until the Operations Manager verifies the workbook contract and representative counts.
