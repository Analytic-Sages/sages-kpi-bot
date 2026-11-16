import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings
from app.main import app
from app.webhooks.store import WebhookValidationError, parse_event, verify_signature

SECRET = "test-webhook-secret-that-is-long-enough"
NOW = 1_800_000_000


def sign(timestamp: str, event_id: str, body: bytes) -> str:
    content = timestamp.encode() + b"." + event_id.encode() + b"." + body
    return "sha256=" + hmac.new(SECRET.encode(), content, hashlib.sha256).hexdigest()


def test_signature_accepts_valid_request_and_rejects_modified_body():
    body = b'{"event_id":"evt-1","event_type":"task.updated"}'
    signature = sign(str(NOW), "evt-1", body)
    verify_signature(secret=SECRET, timestamp=str(NOW), event_id="evt-1", body=body, signature=signature, now=NOW)
    with pytest.raises(WebhookValidationError, match="signature"):
        verify_signature(secret=SECRET, timestamp=str(NOW), event_id="evt-1", body=body + b" ", signature=signature, now=NOW)


def test_signature_rejects_stale_timestamp():
    with pytest.raises(WebhookValidationError, match="outside"):
        verify_signature(secret=SECRET, timestamp=str(NOW - 301), event_id="evt-1", body=b"{}", signature="sha256=bad", now=NOW)


def test_event_envelope_requires_matching_id_and_valid_type():
    body = json.dumps({"event_id": "evt-1", "event_type": "task.updated"}).encode()
    assert parse_event(body, "evt-1") == ("evt-1", "task.updated")
    with pytest.raises(WebhookValidationError, match="match"):
        parse_event(body, "evt-other")


class FakeSession:
    def __init__(self, events):
        self.events = events
        self.last_event_id = None

    def execute(self, statement):
        values = {getattr(key, "key", key): value.value for key, value in statement._values.items()}
        self.last_event_id = values["event_id"]
        if self.last_event_id in self.events:
            return SimpleNamespace(scalar_one_or_none=lambda: None)
        self.events[self.last_event_id] = SimpleNamespace(**values)
        return SimpleNamespace(scalar_one_or_none=lambda: self.last_event_id)

    def scalar(self, statement):
        return self.events.get(self.last_event_id)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _request_headers(event_id, body, signature=None):
    timestamp = str(int(datetime.now(UTC).timestamp()))
    return {
        "X-Polaris-Timestamp": timestamp,
        "X-Polaris-Event-Id": event_id,
        "X-Polaris-Signature": signature or sign(timestamp, event_id, body),
        "Content-Type": "application/json",
    }


def test_webhook_endpoint_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(_env_file=None, enable_webhooks=False))
    response = TestClient(app).post("/webhooks/events", content=b"{}")
    assert response.status_code == 404


def test_webhook_endpoint_records_idempotency_in_postgres_adapter(monkeypatch):
    events = {}
    monkeypatch.setattr(main_module, "settings", Settings(_env_file=None, enable_webhooks=True, webhook_secret=SECRET))
    monkeypatch.setattr(main_module, "SessionLocal", lambda: FakeSession(events))
    event_id = "evt-api-1"
    body = json.dumps({"event_id": event_id, "event_type": "task.updated"}).encode()
    with TestClient(app) as client:
        first = client.post("/webhooks/events", content=body, headers=_request_headers(event_id, body))
        second = client.post("/webhooks/events", content=body, headers=_request_headers(event_id, body))
    assert first.status_code == 202 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True


def test_webhook_endpoint_rejects_conflicting_event_id(monkeypatch):
    events = {}
    monkeypatch.setattr(main_module, "settings", Settings(_env_file=None, enable_webhooks=True, webhook_secret=SECRET))
    monkeypatch.setattr(main_module, "SessionLocal", lambda: FakeSession(events))
    event_id = "evt-conflict"
    first_body = json.dumps({"event_id": event_id, "event_type": "task.updated"}).encode()
    second_body = json.dumps({"event_id": event_id, "event_type": "task.updated", "changed": True}).encode()
    with TestClient(app) as client:
        client.post("/webhooks/events", content=first_body, headers=_request_headers(event_id, first_body))
        response = client.post("/webhooks/events", content=second_body, headers=_request_headers(event_id, second_body))
    assert response.status_code == 409


def test_webhook_endpoint_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(main_module, "settings", Settings(_env_file=None, enable_webhooks=True, webhook_secret=SECRET))
    event_id = "evt-bad"
    body = json.dumps({"event_id": event_id, "event_type": "task.updated"}).encode()
    response = TestClient(app).post("/webhooks/events", content=body,
                                    headers=_request_headers(event_id, body, "sha256=bad"))
    assert response.status_code == 401
