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

## Health checks

- `GET /` — service identity.
- `GET /health/live` — process liveness.
- `GET /health/ready` — configured/enabled integration flags. This is not a provider connectivity test.

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

## Deployment checklist

1. Deploy the Docker image or install the project in a managed Python 3.12 environment.
2. Set `PORT` to the hosting platform's injected port. Keep a single app instance while the in-process Telegram poller and APScheduler are enabled.
3. Mount persistent writable storage for `data/` (or configure database paths to a persistent volume). SQLite is not suitable for ephemeral filesystems or multiple independent app replicas.
4. Configure secrets through the platform secret manager: `TELEGRAM_BOT_TOKEN`, Google credentials, `WEBHOOK_SECRET`, and API keys. Never commit credential JSON files or `.env`.
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
