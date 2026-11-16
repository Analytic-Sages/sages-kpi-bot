from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import operations_worker as worker
from app.config import Settings
from app.operations_models import Base, Notification, NotificationAttempt


@pytest.fixture
def worker_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(
        worker,
        "settings",
        Settings(_env_file=None, operations_notifications_enabled=True, telegram_test_mode=True),
    )
    yield factory
    engine.dispose()


def add_notification(factory, *, attempts=0, status="Queued", stale=False):
    with factory.begin() as db:
        item = Notification(
            chat_id="controlled-test-chat",
            type="task_assignment",
            reference_id="TASK-1",
            message="Test notification",
            dedupe_key=f"task_assignment:TASK-1:{attempts}:{status}",
            attempts=attempts,
            status=status,
            available_at=datetime.now(UTC) - timedelta(minutes=1),
            delivery_started_at=datetime.now(UTC) - timedelta(minutes=20) if stale else None,
        )
        db.add(item)
        db.flush()
        return item.id


@pytest.mark.asyncio
async def test_telegram_test_mode_records_simulation_without_network(worker_db, monkeypatch):
    notification_id = add_notification(worker_db)

    class NoNetwork:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Test mode attempted an outbound Telegram request")

    monkeypatch.setattr(worker.httpx, "AsyncClient", NoNetwork)
    assert await worker.dispatch() == 1
    with worker_db() as db:
        notification = db.get(Notification, notification_id)
        assert notification.status == "Simulated"
        attempt = db.scalar(select(NotificationAttempt).where(NotificationAttempt.notification_id == notification_id))
        assert attempt.outcome == "simulated"


@pytest.mark.asyncio
@pytest.mark.parametrize(("starting_attempts", "expected_status"), [(0, "Queued"), (4, "Failed")])
async def test_live_delivery_failure_is_recorded_and_bounded(worker_db, monkeypatch, starting_attempts, expected_status):
    notification_id = add_notification(worker_db, attempts=starting_attempts)
    monkeypatch.setattr(
        worker,
        "settings",
        Settings(
            _env_file=None,
            operations_notifications_enabled=True,
            telegram_test_mode=False,
            telegram_bot_token="unit-test-token",
            dry_run=False,
        ),
    )

    class FailedClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise TimeoutError("simulated delivery failure")

    monkeypatch.setattr(worker.httpx, "AsyncClient", FailedClient)
    assert await worker.dispatch() == 0
    with worker_db() as db:
        notification = db.get(Notification, notification_id)
        assert notification.status == expected_status
        assert notification.attempts == starting_attempts + 1
        assert notification.error == "TimeoutError"
        attempt = db.scalar(select(NotificationAttempt).where(NotificationAttempt.notification_id == notification_id))
        assert attempt.outcome == "failed"


def test_restart_recovers_stale_in_flight_delivery(worker_db):
    notification_id = add_notification(worker_db, status="Sending", stale=True)
    assert worker.recover_interrupted_deliveries() == 1
    with worker_db() as db:
        notification = db.get(Notification, notification_id)
        assert notification.status == "Queued"
        assert notification.delivery_started_at is None
        attempt = db.scalar(select(NotificationAttempt).where(NotificationAttempt.notification_id == notification_id))
        assert attempt.outcome == "interrupted"
