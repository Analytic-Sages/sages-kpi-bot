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
    op.alter_column("kpis", "owner_person_id", existing_type=sa.String(length=32), nullable=True)

def downgrade():
    has_unassigned = op.get_bind().execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM kpis WHERE owner_person_id IS NULL)")
    ).scalar()
    if has_unassigned:
        raise RuntimeError("Cannot downgrade: unassigned team KPIs would be lost")
    op.alter_column("kpis", "owner_person_id", existing_type=sa.String(length=32), nullable=False)
