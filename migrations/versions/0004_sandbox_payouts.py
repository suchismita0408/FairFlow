"""Freelancer Sandbox payout destinations and milestone payout intents."""
from alembic import op
from app import db

revision='0004_sandbox_payouts'
down_revision='0003_step1_payments'
branch_labels=depends_on=None

def upgrade():
    db.sandbox_recipients.create(op.get_bind(),checkfirst=True)
    db.milestone_payouts.create(op.get_bind(),checkfirst=True)

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup.')
