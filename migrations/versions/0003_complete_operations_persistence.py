"""Preserve workbook metadata and PostgreSQL-owned delivery/permission state.

Revision ID: 0003_ops_persistence
Revises: 0002_unassigned_team_kpis

This revision is deliberately idempotent. Revision 0001 historically used
Base.metadata.create_all(), so on a fresh install it may already create columns,
tables and indexes introduced by this revision. On older installations, this
revision still adds whichever objects are missing.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_ops_persistence"
down_revision = "0002_unassigned_team_kpis"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table, column):
    inspector = _inspector()
    return inspector.has_table(table) and any(
        item["name"] == column for item in inspector.get_columns(table)
    )


def _add_column_if_missing(table, column):
    if not _has_column(table, column.name):
        op.add_column(table, column)


def _set_nullable_if_needed(table, column, existing_type, nullable):
    inspector = _inspector()
    if not inspector.has_table(table):
        raise RuntimeError(f"Cannot migrate missing table: {table}")
    current = next(
        (item for item in inspector.get_columns(table) if item["name"] == column),
        None,
    )
    if current is None:
        raise RuntimeError(f"Cannot alter missing column: {table}.{column}")
    if current["nullable"] != nullable:
        op.alter_column(
            table,
            column,
            existing_type=existing_type,
            nullable=nullable,
        )


def _create_table_if_missing(name, *args, **kwargs):
    if not _inspector().has_table(name):
        op.create_table(name, *args, **kwargs)


def _has_index(table, name):
    inspector = _inspector()
    return inspector.has_table(table) and any(
        item["name"] == name for item in inspector.get_indexes(table)
    )


def _create_index_if_missing(name, table, columns):
    if not _has_index(table, name):
        op.create_index(name, table, columns)


def upgrade():
    _add_column_if_missing(
        "notifications",
        sa.Column("delivery_started_at", sa.DateTime(timezone=True), nullable=True),
    )

    for table in (
        "teams",
        "roles",
        "people",
        "kpis",
        "kpi_results",
        "weekly_reviews",
        "review_actions",
        "blockers",
    ):
        _add_column_if_missing(
            table,
            sa.Column(
                "source_data",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            ),
        )

    for table in ("teams", "people", "kpis"):
        _add_column_if_missing(table, sa.Column("notes", sa.Text(), nullable=True))

    _add_column_if_missing("kpis", sa.Column("period_start", sa.Date(), nullable=True))
    _add_column_if_missing("kpis", sa.Column("checkin_due_date", sa.Date(), nullable=True))
    _add_column_if_missing(
        "kpis", sa.Column("checkin_status", sa.String(length=40), nullable=True)
    )

    _set_nullable_if_needed(
        "weekly_reviews", "submitted_by", sa.String(length=32), True
    )
    _set_nullable_if_needed(
        "review_actions", "owner_person_id", sa.String(length=32), True
    )
    _set_nullable_if_needed("blockers", "reported_by", sa.String(length=32), True)
    _set_nullable_if_needed(
        "blockers", "telegram_message_id", sa.String(length=100), True
    )
    _set_nullable_if_needed("notifications", "chat_id", sa.String(length=64), True)

    _create_table_if_missing(
        "role_permissions",
        sa.Column("role_id", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.role_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission"),
    )
    _create_table_if_missing(
        "notification_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["notification_id"], ["notifications.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _create_index_if_missing(
        "ix_notification_attempts_notification",
        "notification_attempts",
        ["notification_id", "attempted_at"],
    )
    _create_table_if_missing(
        "webhook_events",
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    _create_index_if_missing(
        "ix_webhook_events_received", "webhook_events", ["received_at"]
    )


def downgrade():
    # Forward-only compatibility migration. Revision 0001 uses the live ORM
    # metadata to create its baseline schema, which may already include every
    # object introduced here. Dropping objects cannot distinguish those baseline
    # objects from objects added by this revision and could break a fresh install.
    # Extra nullable columns/tables/indexes are backward-compatible and retained.
    pass
