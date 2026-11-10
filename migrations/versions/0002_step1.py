"""Fixed portal identities and deterministic negotiation evidence.

Legacy accounts remain unclassified; a role is never inferred from an email/name.
New portal registration always requires one fixed role.
"""
from alembic import op
import sqlalchemy as sa
from app import db

revision = '0002_step1'
down_revision = '0001_platform'
branch_labels = depends_on = None

def upgrade():
    bind = op.get_bind()
    for name, columns in {
        'users': [sa.Column('role',sa.String(20)),sa.Column('handle',sa.String(80))],
        'sessions': [sa.Column('last_active_at',sa.String(40)),sa.Column('created_at',sa.String(40)),sa.Column('ip',sa.String(80)),sa.Column('user_agent',sa.String(500))],
        'agreement_versions': [sa.Column('parent_id',sa.String(64),sa.ForeignKey('agreement_versions.id',name='fk_agreement_parent'))],
    }.items():
        present={c['name'] for c in sa.inspect(bind).get_columns(name)}
        with op.batch_alter_table(name) as batch:
            for column in columns:
                if column.name not in present:batch.add_column(column)
    if not any(c.get('name')=='uq_users_handle' or c.get('column_names')==['handle'] for c in sa.inspect(bind).get_unique_constraints('users')):
        with op.batch_alter_table('users') as batch:batch.create_unique_constraint('uq_users_handle',['handle'])
    if not any(c['name']=='user_role' for c in sa.inspect(bind).get_check_constraints('users')):
        with op.batch_alter_table('users') as batch:batch.create_check_constraint('user_role',"role IS NULL OR role IN ('client','freelancer')")
    db.metadata.create_all(bind)
    if bind.dialect.name == 'postgresql':
        op.execute("""CREATE OR REPLACE FUNCTION fairflow_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only record'; END $$""")
        for name in ('audit_events','chat_messages','signatures','agreement_field_changes'):
            op.execute(f'DROP TRIGGER IF EXISTS immutable_record ON {name}')
            op.execute(f'CREATE TRIGGER immutable_record BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION fairflow_append_only()')
        op.execute("""CREATE OR REPLACE FUNCTION fairflow_sent_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.status = 'sent' THEN RAISE EXCEPTION 'sent agreement is immutable'; END IF; RETURN NEW; END $$""")
        op.execute('CREATE TRIGGER immutable_sent_agreement BEFORE UPDATE OR DELETE ON agreement_versions FOR EACH ROW EXECUTE FUNCTION fairflow_sent_immutable()')
        op.execute("""CREATE OR REPLACE FUNCTION fairflow_fixed_role() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF OLD.role IS NOT NULL AND NEW.role IS DISTINCT FROM OLD.role THEN RAISE EXCEPTION 'account role is fixed'; END IF; RETURN NEW; END $$""")
        op.execute('CREATE TRIGGER fixed_user_role BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION fairflow_fixed_role()')

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup.')
