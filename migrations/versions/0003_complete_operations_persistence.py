"""Preserve workbook metadata and PostgreSQL-owned delivery/permission state.

Revision ID: 0003_ops_persistence
Revises: 0002_unassigned_team_kpis
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_ops_persistence"
down_revision = "0002_unassigned_team_kpis"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("notifications", sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True))
    for table in ("teams", "roles", "people", "kpis", "kpi_results", "weekly_reviews", "review_actions", "blockers"):
        op.add_column(table, sa.Column("source_data", sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
    op.add_column("teams", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("kpis", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("kpis", sa.Column("period_start", sa.Date(), nullable=True))
    op.add_column("kpis", sa.Column("checkin_due_date", sa.Date(), nullable=True))
    op.add_column("kpis", sa.Column("checkin_status", sa.String(length=40), nullable=True))

    op.alter_column("weekly_reviews", "submitted_by", existing_type=sa.String(length=32), nullable=True)
    op.alter_column("review_actions", "owner_person_id", existing_type=sa.String(length=32), nullable=True)
    op.alter_column("blockers", "reported_by", existing_type=sa.String(length=32), nullable=True)
    op.alter_column("blockers", "telegram_message_id", existing_type=sa.String(length=100), nullable=True)
    op.alter_column("notifications", "chat_id", existing_type=sa.String(length=64), nullable=True)

    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.role_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission"),
    )
    op.create_table(
        "notification_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_attempts_notification",
        "notification_attempts",
        ["notification_id", "attempted_at"],
    )
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_webhook_events_received", "webhook_events", ["received_at"])


def downgrade():
    op.drop_index("ix_webhook_events_received", table_name="webhook_events")
    op.drop_table("webhook_events")
    op.drop_index("ix_notification_attempts_notification", table_name="notification_attempts")
    op.drop_table("notification_attempts")
    op.drop_table("role_permissions")
    op.drop_column("notifications", "delivery_started_at")
    op.alter_column("notifications", "chat_id", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("blockers", "telegram_message_id", existing_type=sa.String(length=100), nullable=False)
    op.alter_column("blockers", "reported_by", existing_type=sa.String(length=32), nullable=False)
    op.alter_column("review_actions", "owner_person_id", existing_type=sa.String(length=32), nullable=False)
    op.alter_column("weekly_reviews", "submitted_by", existing_type=sa.String(length=32), nullable=False)
    op.drop_column("kpis", "checkin_status")
    op.drop_column("kpis", "checkin_due_date")
    op.drop_column("kpis", "period_start")
    op.drop_column("kpis", "notes")
    op.drop_column("people", "notes")
    op.drop_column("teams", "notes")
    for table in ("blockers", "review_actions", "weekly_reviews", "kpi_results", "kpis", "people", "roles", "teams"):
        op.drop_column(table, "source_data")
