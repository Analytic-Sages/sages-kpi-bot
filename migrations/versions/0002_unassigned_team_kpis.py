"""Allow team KPIs to exist before an individual owner is assigned.

Revision ID: 0002_unassigned_team_kpis
Revises: 0001_sageops_core
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_unassigned_team_kpis"
down_revision = "0001_sageops_core"
branch_labels = None
depends_on = None

def upgrade():
    op.alter_column(
        "kpis",
        "owner_person_id",
        existing_type=sa.String(length=32),
        nullable=True,
    )
    op.alter_column(
        "kpis",
        "owner_person_id",
        existing_type=sa.String(length=32),
        existing_nullable=True,
        nullable=True,
    )

def downgrade():
    op.execute("DELETE FROM kpis WHERE owner_person_id IS NULL")
    op.alter_column(
        "kpis",
        "owner_person_id",
        existing_type=sa.String(length=32),
        nullable=False,
    )
