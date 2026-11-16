"""Create the SageOps staff operations schema.

Revision ID: 0001_sageops_core
Revises:
"""
import sqlalchemy as sa
from alembic import op

from app import operations_models  # noqa: F401
from app.operations_db import Base

revision = "0001_sageops_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
    settings_table = sa.table(
        "settings",
        sa.column("key", sa.String()),
        sa.column("value", sa.JSON()),
        sa.column("description", sa.Text()),
    )
    op.bulk_insert(settings_table, [
        {"key": "timezone", "value": "Africa/Lagos", "description": "Organization business timezone"},
        {"key": "bot_enabled", "value": False, "description": "Global outgoing Telegram kill switch"},
        {"key": "chat_writes_enabled", "value": False, "description": "Independent Telegram data-mutation kill switch"},
        {"key": "reminder_cooldown_hours", "value": 20, "description": "Minimum hours between task reminders"},
        {"key": "escalate_after_days", "value": 3, "description": "Overdue days before escalation"},
        {"key": "escalation_cooldown_hours", "value": 44, "description": "Minimum hours between escalations"},
        {"key": "due_soon_days", "value": 2, "description": "Days ahead included in due-soon reminders"},
        {"key": "quiet_hours_start", "value": "20:00", "description": "Local quiet-hours start"},
        {"key": "quiet_hours_end", "value": "07:00", "description": "Local quiet-hours end"},
        {"key": "digest_time", "value": "08:00", "description": "Local daily digest delivery time"},
        {"key": "on_track_threshold", "value": 1.0, "description": "KPI achievement ratio considered on track"},
        {"key": "at_risk_threshold", "value": 0.8, "description": "KPI achievement ratio lower bound for at risk"},
    ])
    op.execute("""
        CREATE OR REPLACE FUNCTION sageops_reject_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION sageops_reject_audit_mutation();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS sageops_reject_audit_mutation()")
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
