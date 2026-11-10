"""Immutable authenticated delivery artifacts; workflow state uses revisioned projects."""
from alembic import op
from app import db

revision='0005_delivery_evidence'
down_revision='0004_sandbox_payouts'
branch_labels=depends_on=None

def upgrade():
    bind=op.get_bind()
    db.delivery_files.create(bind,checkfirst=True)
    if bind.dialect.name=='postgresql':
        for table in ('delivery_files','evidence'):
            op.execute(f"CREATE OR REPLACE FUNCTION immutable_{table}_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only record'; END $$")
            op.execute(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION immutable_{table}_guard()")

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup.')
