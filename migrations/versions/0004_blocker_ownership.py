"""Track the responsible owner for operational blockers.

Revision ID: 0004_blocker_ownership
Revises: 0003_ops_persistence

This revision is idempotent because the current ORM metadata used by revision
0001 may already include the ownership column, foreign key and index.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_blocker_ownership"
down_revision = "0003_ops_persistence"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "fk_blockers_owner_person_id_people"
INDEX_NAME = "ix_blockers_owner_person_id"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table, column):
    inspector = _inspector()
    return inspector.has_table(table) and any(
        item["name"] == column for item in inspector.get_columns(table)
    )


def _has_owner_foreign_key():
    inspector = _inspector()
    if not inspector.has_table("blockers"):
        return False
    return any(
        item.get("constrained_columns") == ["owner_person_id"]
        and item.get("referred_table") == "people"
        for item in inspector.get_foreign_keys("blockers")
    )


def _has_index():
    inspector = _inspector()
    if not inspector.has_table("blockers"):
        return False
    return any(
        item["name"] == INDEX_NAME for item in inspector.get_indexes("blockers")
    )


def upgrade():
    if not _has_column("blockers", "owner_person_id"):
        op.add_column(
            "blockers",
            sa.Column("owner_person_id", sa.String(length=32), nullable=True),
        )

    if not _has_owner_foreign_key():
        op.create_foreign_key(
            CONSTRAINT_NAME,
            "blockers",
            "people",
            ["owner_person_id"],
            ["person_id"],
            ondelete="SET NULL",
        )

    if not _has_index():
        op.create_index(INDEX_NAME, "blockers", ["owner_person_id"])


def downgrade():
    # See the forward-only rationale in 0003_ops_persistence.downgrade().
    # Retaining this nullable ownership field is safer than removing a field
    # that may have been created by revision 0001's current ORM metadata.
    pass
