"""Relational SageOps domain model.

IDs such as P001 and T0012 remain stable across migration. All operational timestamps are
timezone-aware; business-date calculations use the configured organization timezone.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    func,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.operations_db import Base


class Team(Base):
    __tablename__ = "teams"
    team_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    lead_person_id: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(Base):
    __tablename__ = "roles"
    role_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    core_domain: Mapped[str | None] = mapped_column(String(160))
    responsibilities: Mapped[str | None] = mapped_column(Text)
    output_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class Person(Base):
    __tablename__ = "people"
    person_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.team_id", ondelete="RESTRICT"))
    role_id: Mapped[str | None] = mapped_column(ForeignKey("roles.role_id", ondelete="RESTRICT"))
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    telegram_handle: Mapped[str | None] = mapped_column(String(64))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    reports_to: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), default="Active", nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    replacement_for: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    notify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_digest_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (CheckConstraint("status IN ('Active','Inactive','On Leave')", name="ck_people_status"),)


class KPI(Base):
    __tablename__ = "kpis"
    kpi_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id", ondelete="RESTRICT"), nullable=False)
    role_id: Mapped[str | None] = mapped_column(ForeignKey("roles.role_id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    definition: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_person_id: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"), nullable=True)
    target: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(40))
    data_source: Mapped[str] = mapped_column(String(16), default="Manual", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        CheckConstraint("direction IN ('Increase','Decrease','Maintain')", name="ck_kpis_direction"),
        CheckConstraint("frequency IN ('Daily','Weekly','Monthly','Quarterly')", name="ck_kpis_frequency"),
        Index("uq_kpis_owner_name_active", "owner_person_id", text("lower(name)"), unique=True, postgresql_where=text("active IS TRUE")),
    )


class KPIResult(Base):
    __tablename__ = "kpi_results"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kpi_id: Mapped[str] = mapped_column(ForeignKey("kpis.kpi_id", ondelete="RESTRICT"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    actual: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    target_used: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    target_override: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    comment: Mapped[str | None] = mapped_column(Text)
    next_action: Mapped[str | None] = mapped_column(Text)
    logged_by: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    logged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("kpi_id", "period_start", name="uq_kpi_results_period"),)


class Task(Base):
    __tablename__ = "tasks"
    task_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id", ondelete="RESTRICT"), nullable=False)
    assigned_to: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="RESTRICT"))
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="RESTRICT"))
    reviewer: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    priority: Mapped[str] = mapped_column(String(16), default="Medium", nullable=False)
    date_assigned: Mapped[date] = mapped_column(Date, nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), default="Not Started", nullable=False)
    percent_complete: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output_link: Mapped[str | None] = mapped_column(Text)
    blocker_note: Mapped[str | None] = mapped_column(Text)
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("percent_complete BETWEEN 0 AND 100", name="ck_tasks_percent"),
        CheckConstraint("status IN ('Not Started','In Progress','In Review','Blocked','On Hold','Completed','Cancelled','Closed')", name="ck_tasks_status"),
        CheckConstraint("priority IN ('Low','Medium','High','Urgent')", name="ck_tasks_priority"),
        Index("ix_tasks_team_status_deadline", "team_id", "status", "deadline"),
        Index("ix_tasks_assigned_to_status", "assigned_to", "status"),
    )


class TaskDetail(Base):
    __tablename__ = "task_details"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id", ondelete="CASCADE"), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class TaskReminderState(Base):
    __tablename__ = "task_reminder_state"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.task_id", ondelete="CASCADE"), primary_key=True)
    last_reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigner_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WeeklyReview(Base):
    __tablename__ = "weekly_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.team_id", ondelete="RESTRICT"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    key_achievement: Mapped[str | None] = mapped_column(Text)
    challenge: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(Text)
    submitted_by: Mapped[str] = mapped_column(ForeignKey("people.person_id", ondelete="RESTRICT"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("team_id", "week_start", name="uq_weekly_reviews_team_week"),)


class ReviewAction(Base):
    __tablename__ = "review_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("weekly_reviews.id", ondelete="CASCADE"), nullable=False)
    owner_person_id: Mapped[str] = mapped_column(ForeignKey("people.person_id", ondelete="RESTRICT"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="Open", nullable=False)
    __table_args__ = (CheckConstraint("status IN ('Open','Done','Dropped')", name="ck_review_action_status"),)


class Blocker(Base):
    __tablename__ = "blockers"
    blocker_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.task_id", ondelete="SET NULL"))
    reported_by: Mapped[str] = mapped_column(ForeignKey("people.person_id", ondelete="RESTRICT"), nullable=False)
    telegram_message_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="Open", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool] = mapped_column(JSON, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    chat_id: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="Queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_notifications_dispatch", "status", "available_at"),)


class ChangeRequest(Base):
    __tablename__ = "change_requests"
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    requester: Mapped[str] = mapped_column(ForeignKey("people.person_id", ondelete="RESTRICT"), nullable=False)
    entity: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    changes: Mapped[list] = mapped_column(JSON, nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="AWAITING_CONFIRMATION", nullable=False)
    approver: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"
    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    approver: Mapped[str | None] = mapped_column(ForeignKey("people.person_id", ondelete="SET NULL"))
    entity: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    field: Mapped[str] = mapped_column(String(100), nullable=False)
    old: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    new: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"
    update_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
