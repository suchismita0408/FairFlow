"""Browser-scoped judge demo identities and project access."""
from alembic import op
from app import db

revision = '0006_judge_demos'
down_revision = '0005_delivery_evidence'
branch_labels = depends_on = None


def upgrade():
    db.judge_demos.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup.')
