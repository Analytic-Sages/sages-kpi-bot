"""Pure SageOps business rules; no database or Telegram side effects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ReminderDecision:
    reminder_type: str | None
    eligible: bool
    reason: str
    send_after: datetime | None = None


def task_reminder_type(*, status: str, deadline: date | None, assigned_to: str | None,
                       today: date, due_soon_days: int = 2) -> str | None:
    """Mirror the agreed task classification; Blocked remains overdue until policy is decided."""
    if not assigned_to:
        return "UNASSIGNED"
    normalized = status.strip().casefold()
    if normalized in {"completed", "cancelled", "closed"}:
        return None
    if normalized == "on hold":
        return None
    if deadline is None:
        return "NO_DEADLINE"
    days = (deadline - today).days
    if days < 0:
        return "OVERDUE"
    if days == 0:
        return "DUE_TODAY"
    if days <= due_soon_days:
        return "DUE_SOON"
    return None


def next_allowed_send(now: datetime, *, timezone: str, quiet_start: time = time(20, 0),
                      quiet_end: time = time(7, 0)) -> datetime:
    """Return now outside quiet hours, otherwise the next local quiet-end instant in UTC."""
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    current = local.timetz().replace(tzinfo=None)
    in_quiet = current >= quiet_start or current < quiet_end
    if not in_quiet:
        return now
    target_date = local.date() + (timedelta(days=1) if current >= quiet_start else timedelta())
    target = datetime.combine(target_date, quiet_end, tzinfo=zone)
    return target.astimezone(now.tzinfo)


def reminder_decision(*, status: str, deadline: date | None, assigned_to: str | None,
                      person_active: bool, notify: bool, chat_id: str | None,
                      snooze_until: datetime | None, last_reminded_at: datetime | None,
                      now: datetime, timezone: str = "Africa/Lagos",
                      due_soon_days: int = 2, cooldown_hours: int = 20,
                      quiet_start: time = time(20, 0),
                      quiet_end: time = time(7, 0)) -> ReminderDecision:
    kind = task_reminder_type(status=status, deadline=deadline, assigned_to=assigned_to,
                              today=now.astimezone(ZoneInfo(timezone)).date(),
                              due_soon_days=due_soon_days)
    if kind not in {"OVERDUE", "DUE_TODAY", "DUE_SOON"}:
        return ReminderDecision(kind, False, "not_a_reminder_class")
    if not person_active or not notify or not chat_id:
        return ReminderDecision(kind, False, "assignee_not_reachable")
    if snooze_until and snooze_until > now:
        return ReminderDecision(kind, False, "snoozed", snooze_until)
    if last_reminded_at and last_reminded_at + timedelta(hours=cooldown_hours) > now:
        return ReminderDecision(kind, False, "cooldown")
    send_after = next_allowed_send(now, timezone=timezone, quiet_start=quiet_start, quiet_end=quiet_end)
    return ReminderDecision(kind, True, "queued", send_after)


def escalation_due(*, deadline: date | None, status: str, today: date,
                   last_escalated_at: datetime | None, now: datetime,
                   escalate_after_days: int = 3, cooldown_hours: int = 44) -> bool:
    if deadline is None or status.strip().casefold() in {"completed", "cancelled", "closed", "on hold"}:
        return False
    if (today - deadline).days < escalate_after_days:
        return False
    return not last_escalated_at or last_escalated_at + timedelta(hours=cooldown_hours) <= now


def kpi_achievement(direction: str, actual: Decimal | int | float | str | None,
                    target: Decimal | int | float | str) -> Decimal | None:
    """Return an achievement ratio; None means no comparable result."""
    if actual is None or str(actual).strip() == "":
        return None
    try:
        a, t = Decimal(str(actual)), Decimal(str(target))
    except (InvalidOperation, ValueError):
        return None
    if not a.is_finite() or not t.is_finite():
        return None
    mode = direction.strip().casefold()
    if mode == "increase":
        return None if t == 0 else a / t
    if mode == "decrease":
        return Decimal(1) if a <= 0 else (t / a if a else Decimal(1))
    if mode == "maintain":
        if t == 0:
            return None
        return max(Decimal(0), Decimal(1) - abs(a - t) / abs(t))
    raise ValueError("direction must be Increase, Decrease, or Maintain")


def kpi_status(achievement: Decimal | None, *, on_track: Decimal = Decimal("1.00"),
               at_risk: Decimal = Decimal("0.80")) -> str:
    if achievement is None:
        return "Not Logged"
    if achievement >= on_track:
        return "On Track"
    if achievement >= at_risk:
        return "At Risk"
    return "Off Track"


def digest_due(*, now: datetime, digest_time: time, timezone: str,
               last_digest_sent_at: datetime | None, attention_count: int,
               person_active: bool, notify: bool, chat_id: str | None) -> bool:
    if not person_active or not notify or not chat_id or attention_count <= 0:
        return False
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    if local.timetz().replace(tzinfo=None) < digest_time:
        return False
    if last_digest_sent_at and last_digest_sent_at.astimezone(zone).date() == local.date():
        return False
    return True
