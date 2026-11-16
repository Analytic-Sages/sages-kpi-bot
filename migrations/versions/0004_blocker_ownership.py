"""Track the responsible owner for operational blockers."""
from alembic import op
import sqlalchemy as sa

revision = "0004_blocker_ownership"
down_revision = "0003_ops_persistence"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("blockers", sa.Column("owner_person_id", sa.String(length=32), nullable=True))
    op.create_foreign_key("fk_blockers_owner_person_id_people", "blockers", "people",
                          ["owner_person_id"], ["person_id"], ondelete="SET NULL")
    op.create_index("ix_blockers_owner_person_id", "blockers", ["owner_person_id"])


def downgrade():
    op.drop_index("ix_blockers_owner_person_id", table_name="blockers")
    op.drop_constraint("fk_blockers_owner_person_id_people", "blockers", type_="foreignkey")
    op.drop_column("blockers", "owner_person_id")
