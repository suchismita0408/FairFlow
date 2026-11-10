"""Sandbox milestone payment records bound to signed agreement versions."""
from alembic import op
from app import db

revision='0003_step1_payments'
down_revision='0002_step1'
branch_labels=depends_on=None

def upgrade():
    db.milestone_payments.create(op.get_bind(),checkfirst=True)

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup.')
