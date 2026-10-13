# Polaris — Analytic Sages Growth & Accountability Bot

Polaris is a FastAPI service with Telegram team workflows, scheduled accountability prompts, and read-only growth analytics. Google Sheets remains the operational source of truth.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload
```

The safe defaults are intentional: `DRY_RUN=true`; team workflows, scheduled workflows, analytics, and inbound webhooks are disabled unless explicitly enabled. The app does not need live credentials to run health checks.

## Google Sheets authentication

The Sheets client supports these authentication modes, in priority order:

1. **OAuth refresh token** — set `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REFRESH_TOKEN` together. This is the supported no-service-account-key path for Render.
2. **Google credentials file** — `GOOGLE_CREDS_PATH` may point to a service-account JSON file or a Google external-account/Workload Identity Federation credential configuration. Keep credential files out of source control.

Partial OAuth configuration is rejected rather than silently falling back to a file. Authentication errors do not print tokens, client secrets, refresh tokens, or credential file contents.

### Configure OAuth for Render

Render's documented managed OIDC integrations currently list AWS, Anthropic, and OpenAI, not Google Cloud. For a Render deployment that cannot use a Google service-account private key, configure OAuth using a dedicated Google account that has access to the tracker.

1. In Google Cloud Console, select the project and enable the Google Sheets API.
2. Configure the OAuth consent screen. Use an Internal audience when available for your Google Workspace organization; otherwise configure the External audience and complete the publishing/verification requirements Google presents for the Sheets scope. Do not leave a production integration in External Testing mode: refresh tokens issued to external apps in Testing can expire after seven days.
3. Create an OAuth client ID. If using OAuth 2.0 Playground to obtain the initial refresh token, create a Web application OAuth client and add `https://developers.google.com/oauthplayground` as an authorized redirect URI.
4. Open [OAuth 2.0 Playground](https://developers.google.com/oauthplayground), open its settings, enable **Use your own OAuth credentials**, and enter the OAuth client ID and secret. In Step 1, request `https://www.googleapis.com/auth/spreadsheets`; authorize using the dedicated Google account that should own Polaris's access. Exchange the authorization code in Step 2 and copy the refresh token from the token response. Treat it as a long-lived secret.
5. Share the KPI & Growth Tracker with that dedicated account. Grant Viewer access for initial read-only validation; grant Editor only if a later approved workflow genuinely needs to write to the spreadsheet.
6. In Render Dashboard → Polaris service → Environment, add these variables using Render's secret/environment-variable UI:
   - `GOOGLE_SHEET_ID` — spreadsheet ID from the Google Sheets URL.
   - `GOOGLE_OAUTH_CLIENT_ID` — OAuth client ID.
   - `GOOGLE_OAUTH_CLIENT_SECRET` — OAuth client secret.
   - `GOOGLE_OAUTH_REFRESH_TOKEN` — refresh token.
   - `DRY_RUN=true`.
7. Keep `ENABLE_TEAM_WORKFLOWS=false`, `ENABLE_SCHEDULED_WORKFLOWS=false`, `ENABLE_ANALYTICS=false`, and `ENABLE_WEBHOOKS=false` until staging checks are complete. Redeploy after saving the Render environment.

Use a dedicated account rather than a personal employee account where practical. The refresh token is still a sensitive credential: never commit it, put it in logs, or paste it into chat. Rotate/re-authorize it if revoked. If Google requires OAuth app verification for the chosen audience/scope, complete that process before relying on the integration in production.

## Health checks

- `GET /` — service identity.
- `GET /health/live` — HTTP process liveness only; it does not prove Telegram is polling.
- `GET /health/telegram` — recent successful Telegram `getUpdates` polling plus supervisor-task liveness. Returns `503` if Telegram is configured but polling is stale or unhealthy.
- `GET /health/ready` — configured/enabled integration flags and Telegram polling health. This is not a Google Sheets, GA4, or YouTube provider connectivity test.

For Render free-tier deployments, an external HTTP monitor such as UptimeRobot can request `/health/live` periodically to reduce idle spin-down. Add a separate monitor for `/health/telegram` to alert on Telegram polling degradation. The second monitor reports a failure; it does not restart the Telegram task itself. The application supervisor retries unexpected polling-coroutine exits, but no external monitor can guarantee that a free Render service stays awake or recover a suspended/terminated host. Use an always-on worker/hosting plan if the required service-level availability cannot tolerate free-tier sleep or restarts.

## Read-only production preflight

Run the preflight from the same environment that will run Polaris, with the deployment's Google authentication settings available:

```bash
python -m app.preflight
```

The command checks required environment configuration without displaying secret values, authenticates to and opens the configured spreadsheet in read-only mode, verifies core worksheet headers, and reports the accountability safety fields required for reminders. It never writes to Google Sheets, sends Telegram messages, or enables integrations. A nonzero exit code means one or more blockers remain.

The preflight deliberately checks only headers and access, not individual people's rows. A successful report does not prove that chat IDs are correct, Telegram delivery works, analytics APIs are authorized, HTTPS is configured, SQLite storage is durable, or only one service instance is running. Complete the manual staging checklist below before enabling live workflows.

### Verify Google Sheets from Render without enabling workflows

If you cannot open a shell in the hosting environment, configure `PREFLIGHT_TOKEN` in the hosting secret store with a randomly generated secret of at least 32 characters, then redeploy. With that variable set, `GET /admin/preflight` is available only to callers sending `Authorization: Bearer <PREFLIGHT_TOKEN>`. It authenticates to the configured spreadsheet, checks worksheet headers, and performs read-only integrity checks on the core people, KPI, weekly KPI, and task records. The response includes aggregate counts and check outcomes only; it never returns names, person IDs, chat IDs, or record contents. It does not write to Sheets, send Telegram messages, or enable workflows. Keep the token private and remove it after validation if you no longer need the endpoint.

Example from a local terminal, after storing the token in your shell environment as `POLARIS_PREFLIGHT_TOKEN`:

```bash
curl --fail-with-body -sS \
  -H "Authorization: Bearer $POLARIS_PREFLIGHT_TOKEN" \
  https://YOUR-RENDER-SERVICE.onrender.com/admin/preflight
```

A `200` response with `"status":"passed"` confirms that the deployed app authenticated and all header and row-integrity checks passed. A `503` response with `"status":"blocked"` means one or more blockers need investigation. A `503` response returns sanitized blocker details. A `401` means the bearer token was missing or incorrect; a `404` means the endpoint is disabled because `PREFLIGHT_TOKEN` is not configured. Never put the token in a URL or share it in chat or logs.

## Inbound webhook contract

`POST /webhooks/events` is disabled unless `ENABLE_WEBHOOKS=true` and `WEBHOOK_SECRET` is configured.

Required headers:

- `X-Polaris-Timestamp`: Unix timestamp in seconds.
- `X-Polaris-Event-Id`: stable unique event ID, max 200 characters.
- `X-Polaris-Signature`: `sha256=<hex HMAC>`.

The HMAC-SHA256 input is the exact UTF-8 bytes of `timestamp + "." + event_id + "." + raw_request_body`. The JSON body must be an object with matching `event_id` and a lowercase `event_type` matching `[a-z][a-z0-9_.-]{0,99}`. Timestamps outside `WEBHOOK_MAX_SKEW_SECONDS` (default 300 seconds) are rejected. The body limit defaults to 256 KiB.

Example signing snippet:

```python
import hashlib, hmac, json, time

secret = "replace-with-a-random-secret"
event_id = "evt-unique-123"
timestamp = str(int(time.time()))
body = json.dumps(
    {"event_id": event_id, "event_type": "task.updated"},
    separators=(",", ":"),
).encode()
message = timestamp.encode() + b"." + event_id.encode() + b"." + body
signature = "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
# Send body unchanged with the three X-Polaris-* headers.
```

Responses: `202` for first acceptance, `200` for an identical duplicate, `401` for authentication failure, `400` for a malformed envelope, `409` if an event ID is reused with a different body, `413` for oversized requests, and `503` for receiver/store misconfiguration or store failure. Disabled endpoints return `404`.

**Important:** this first release authenticates and records event metadata/body hash for idempotency; it deliberately does not execute event payloads or trigger Telegram/Sheets writes. The database stores the event ID, type, SHA-256 body hash and receipt time, not the arbitrary payload. Do not enable it until the secret is supplied via the hosting platform's secret manager and persistent storage is available for `WEBHOOK_DB_PATH`. Use HTTPS at the hosting edge; never put the secret in source control or request logs.

## Safe staging rollout

Follow [`docs/SAFE_STAGING_ROLLOUT.md`](docs/SAFE_STAGING_ROLLOUT.md) before enabling any live team, scheduled, analytics, or webhook workflow. It covers private Telegram identity onboarding, pilot KPI configuration, read-only preflight acceptance, safe command tests, staged activation, and rollback gates.

## Deployment checklist

1. Deploy the Docker image or install the project in a managed Python 3.12 environment.
2. Set `PORT` to the hosting platform's injected port. Keep a single app instance while the in-process Telegram poller and APScheduler are enabled.
3. Mount persistent writable storage for `data/` (or configure database paths to a persistent volume). SQLite is not suitable for ephemeral filesystems or multiple independent app replicas.
4. Configure secrets through the platform secret manager: `TELEGRAM_BOT_TOKEN`, Google OAuth settings (or an approved credential file), `WEBHOOK_SECRET`, and API keys. Never commit credential JSON files or `.env`.
5. Start with `DRY_RUN=true`, `ENABLE_TEAM_WORKFLOWS=false`, `ENABLE_SCHEDULED_WORKFLOWS=false`, `ENABLE_ANALYTICS=false`, and `ENABLE_WEBHOOKS=false`.
6. Verify `/health/live` and `/health/ready`, inspect structured logs, and verify the exact Google Sheet headers/permissions and Telegram private/core chat routing.
7. Validate GA4 property access, YouTube channel ID/API key, webhook signing from a trusted sender, duplicate delivery, and persistent SQLite behavior in staging.
8. Enable each integration separately, after its acceptance checks. Do not disable dry-run or enable outbound workflows merely because health endpoints pass.
9. Set alerts for process restarts, non-2xx rates, repeated provider errors, missed scheduled runs, and SQLite storage/permission failures using the hosting platform's monitoring.
10. Confirm a tested rollback image and document the on-call owner before production.

## CI and local verification

```bash
ruff check .
pytest --cov=app --cov-report=term-missing
docker build -t polaris:local .
docker run --rm -e PORT=8000 -p 8000:8000 polaris:local
```

GitHub Actions runs Ruff and pytest on pushes to main/feature branches and pull requests to main.
