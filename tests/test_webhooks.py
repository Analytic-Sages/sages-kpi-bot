import hashlib
import hmac
import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.main import app
from app.webhooks.store import (
    SQLiteWebhookEventStore,
    WebhookConflictError,
    WebhookValidationError,
    parse_event,
    verify_signature,
)


SECRET = "test-webhook-secret-that-is-long-enough"
NOW = 1_800_000_000


def sign(timestamp: str, event_id: str, body: bytes) -> str:
    content = timestamp.encode() + b"." + event_id.encode() + b"." + body
    return "sha256=" + hmac.new(SECRET.encode(), content, hashlib.sha256).hexdigest()


def test_signature_accepts_valid_request_and_rejects_modified_body():
    timestamp = str(NOW)
    body = b'{"event_id":"evt-1","event_type":"task.updated"}'
    signature = sign(timestamp, "evt-1", body)

    verify_signature(
        secret=SECRET, timestamp=timestamp, event_id="evt-1", body=body,
        signature=signature, now=NOW,
    )
    with pytest.raises(WebhookValidationError, match="signature"):
        verify_signature(
            secret=SECRET, timestamp=timestamp, event_id="evt-1", body=body + b" ",
            signature=signature, now=NOW,
        )


def test_signature_rejects_stale_timestamp():
    with pytest.raises(WebhookValidationError, match="outside"):
        verify_signature(
            secret=SECRET, timestamp=str(NOW - 301), event_id="evt-1", body=b"{}",
            signature="sha256=bad", now=NOW,
        )


def test_event_envelope_requires_matching_id_and_valid_type():
    body = json.dumps({"event_id": "evt-1", "event_type": "task.updated"}).encode()
    assert parse_event(body, "evt-1") == ("evt-1", "task.updated")
    with pytest.raises(WebhookValidationError, match="match"):
        parse_event(body, "evt-other")


@pytest.mark.asyncio
async def test_event_store_is_idempotent_and_detects_event_id_conflicts(tmp_path):
    store = SQLiteWebhookEventStore(tmp_path / "events.sqlite3")
    body = b'{"event_id":"evt-1","event_type":"task.updated"}'

    assert await store.record("evt-1", "task.updated", body) is True
    assert await store.record("evt-1", "task.updated", body) is False
    with pytest.raises(WebhookConflictError):
        await store.record(
            "evt-1", "task.updated",
            b'{"event_id":"evt-1","event_type":"task.updated","different":true}',
        )


def test_webhook_endpoint_is_disabled_by_default():
    from app import main as main_module

    original = main_module.settings
    main_module.settings = Settings(_env_file=None, enable_webhooks=False)
    try:
        response = TestClient(app).post("/webhooks/events", content=b"{}")
        assert response.status_code == 404
    finally:
        main_module.settings = original


def test_webhook_endpoint_authenticates_and_deduplicates(tmp_path, monkeypatch):
    from app import main as main_module

    settings = Settings(
        _env_file=None,
        enable_webhooks=True,
        webhook_secret=SECRET,
        webhook_db_path=str(tmp_path / "events.sqlite3"),
    )
    monkeypatch.setattr(main_module, "settings", settings)
    timestamp = str(int(datetime.now(UTC).timestamp()))
    event_id = "evt-api-1"
    body = json.dumps({"event_id": event_id, "event_type": "task.updated"}).encode()
    headers = {
        "X-Polaris-Timestamp": timestamp,
        "X-Polaris-Event-Id": event_id,
        "X-Polaris-Signature": sign(timestamp, event_id, body),
        "Content-Type": "application/json",
    }

    with TestClient(app) as client:
        first = client.post("/webhooks/events", content=body, headers=headers)
        second = client.post("/webhooks/events", content=body, headers=headers)

    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


def test_webhook_endpoint_rejects_invalid_signature(tmp_path, monkeypatch):
    from app import main as main_module

    monkeypatch.setattr(
        main_module,
        "settings",
        Settings(
            _env_file=None,
            enable_webhooks=True,
            webhook_secret=SECRET,
            webhook_db_path=str(tmp_path / "events.sqlite3"),
        ),
    )
    timestamp = str(int(datetime.now(UTC).timestamp()))
    event_id = "evt-bad"
    body = json.dumps({"event_id": event_id, "event_type": "task.updated"}).encode()
    with TestClient(app) as client:
        response = client.post(
            "/webhooks/events",
            content=body,
            headers={
                "X-Polaris-Timestamp": timestamp,
                "X-Polaris-Event-Id": event_id,
                "X-Polaris-Signature": "sha256=bad",
            },
        )
    assert response.status_code == 401
