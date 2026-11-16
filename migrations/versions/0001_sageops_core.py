"""Create the SageOps staff operations schema.

Revision ID: 0001_sageops_core
Revises:
"""
from alembic import op

from app.operations_db import Base
from app import operations_models  # noqa: F401

revision = "0001_sageops_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)
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
