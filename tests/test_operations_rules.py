from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from app.operations_rules import (
    digest_due,
    escalation_due,
    kpi_achievement,
    kpi_status,
    next_allowed_send,
    reminder_decision,
    task_reminder_type,
)


def test_task_reminder_classification_edges():
    today = date(2026, 11, 16)
    assert task_reminder_type(status="In Progress", deadline=today - timedelta(days=1), assigned_to="P1", today=today) == "OVERDUE"
    assert task_reminder_type(status="Not Started", deadline=today, assigned_to="P1", today=today) == "DUE_TODAY"
    assert task_reminder_type(status="Not Started", deadline=today + timedelta(days=2), assigned_to="P1", today=today) == "DUE_SOON"
    assert task_reminder_type(status="On Hold", deadline=today - timedelta(days=5), assigned_to="P1", today=today) is None
    assert task_reminder_type(status="Blocked", deadline=today - timedelta(days=5), assigned_to="P1", today=today) == "OVERDUE"


def test_quiet_hours_defer_to_07_lagos():
    now = datetime(2026, 11, 16, 21, 0, tzinfo=UTC)
    result = next_allowed_send(now, timezone="Africa/Lagos")
    assert result.astimezone(UTC).hour == 6
    assert result.astimezone(UTC).date() == date(2026, 11, 17)


def test_reminder_cooldown_and_snooze():
    now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
    due = date(2026, 11, 15)
    base = dict(status="In Progress", deadline=due, assigned_to="P1", person_active=True,
                notify=True, chat_id="123", snooze_until=None, last_reminded_at=None, now=now)
    assert reminder_decision(**base).eligible
    assert not reminder_decision(**{**base, "last_reminded_at": now - timedelta(hours=2)}).eligible
    assert not reminder_decision(**{**base, "snooze_until": now + timedelta(hours=1)}).eligible


def test_escalation_threshold_and_cooldown():
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    assert not escalation_due(deadline=date(2026, 11, 14), status="In Progress", today=date(2026, 11, 16), last_escalated_at=None, now=now)
    assert escalation_due(deadline=date(2026, 11, 13), status="In Progress", today=date(2026, 11, 16), last_escalated_at=None, now=now)
    assert not escalation_due(deadline=date(2026, 11, 13), status="In Progress", today=date(2026, 11, 16), last_escalated_at=now - timedelta(hours=2), now=now)


def test_kpi_directions_and_thresholds():
    assert kpi_achievement("Increase", 80, 100) == Decimal("0.8")
    assert kpi_achievement("Decrease", 5, 10) == Decimal("2")
    assert kpi_achievement("Decrease", 0, 10) == Decimal("1")
    assert kpi_achievement("Maintain", 8, 10) == Decimal("0.8")
    assert kpi_achievement("Increase", 10, 0) is None
    assert kpi_achievement("Maintain", 0, 0) is None
    assert kpi_status(Decimal("1")) == "On Track"
    assert kpi_status(Decimal("0.8")) == "At Risk"
    assert kpi_status(Decimal("0.79")) == "Off Track"
    assert kpi_status(None) == "Not Logged"


def test_digest_once_per_local_day_after_configured_time():
    now = datetime(2026, 11, 16, 8, 30, tzinfo=UTC)  # 09:30 Lagos
    assert digest_due(now=now, digest_time=time(8), timezone="Africa/Lagos",
                      last_digest_sent_at=None, attention_count=1,
                      person_active=True, notify=True, chat_id="123")
    assert not digest_due(now=now, digest_time=time(10), timezone="Africa/Lagos",
                          last_digest_sent_at=None, attention_count=1,
                          person_active=True, notify=True, chat_id="123")
