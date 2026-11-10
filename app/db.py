"""Relational platform persistence. PostgreSQL in development; SQLite only for isolated tests."""
import os, json, uuid, copy
from datetime import datetime, timezone
from functools import lru_cache
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Text, Integer, Boolean, JSON, ForeignKey, UniqueConstraint, CheckConstraint, event, select, insert, update, delete)
from cryptography.fernet import Fernet
from fastapi import HTTPException

metadata=MetaData()
def uid():return uuid.uuid4().hex
def now():return datetime.now(timezone.utc).isoformat()
def table(name,*cols):return Table(name,metadata,Column('id',String(64),primary_key=True),*cols)
users=table('users',Column('email',String(254),unique=True,nullable=False),Column('name',String(100),nullable=False),Column('password_hash',Text,nullable=False),Column('verified',Boolean,nullable=False,default=False),Column('created_at',String(40),nullable=False),Column('role',String(20)),Column('handle',String(80),unique=True),CheckConstraint("role IS NULL OR role IN ('client','freelancer')",name='user_role'))
sessions=table('sessions',Column('user_id',ForeignKey('users.id'),nullable=False),Column('csrf',String(100),nullable=False),Column('expires_at',String(40),nullable=False),Column('last_active_at',String(40)),Column('created_at',String(40)),Column('ip',String(80)),Column('user_agent',String(500)))
client_profiles=table('client_profiles',Column('user_id',ForeignKey('users.id'),unique=True,nullable=False),Column('payload',JSON,nullable=False))
freelancer_profiles=table('freelancer_profiles',Column('user_id',ForeignKey('users.id'),unique=True,nullable=False),Column('payload',JSON,nullable=False))
tokens=table('account_tokens',Column('user_id',ForeignKey('users.id'),nullable=False),Column('kind',String(30),nullable=False),Column('expires_at',String(40),nullable=False),Column('used',Boolean,default=False,nullable=False))
projects=table('platform_projects',Column('payload',JSON,nullable=False),Column('version',Integer,nullable=False,default=1),Column('archived',Boolean,nullable=False,default=False),Column('created_at',String(40),nullable=False))
members=table('project_members',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('role',String(20),nullable=False),UniqueConstraint('project_id','user_id'),UniqueConstraint('project_id','role'))
invitations=table('invitations',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('email',String(254),nullable=False),Column('role',String(20),nullable=False),Column('expires_at',String(40),nullable=False),Column('accepted_by',ForeignKey('users.id')))
agreements=table('agreement_versions',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('number',Integer,nullable=False),Column('digest',String(64),nullable=False),Column('payload',JSON,nullable=False),Column('status',String(20),nullable=False),Column('created_by',ForeignKey('users.id'),nullable=False),Column('created_at',String(40),nullable=False),Column('parent_id',ForeignKey('agreement_versions.id')),UniqueConstraint('project_id','number'))
clauses=table('agreement_clauses',Column('agreement_id',ForeignKey('agreement_versions.id'),nullable=False),Column('reference',String(30),nullable=False),Column('kind',String(30),nullable=False),Column('text',Text,nullable=False),UniqueConstraint('agreement_id','reference'))
acceptances=table('agreement_acceptances',Column('agreement_id',ForeignKey('agreement_versions.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('role',String(20),nullable=False),Column('digest',String(64),nullable=False),Column('created_at',String(40),nullable=False),UniqueConstraint('agreement_id','user_id'))
evidence=table('evidence',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('milestone_id',String(64),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('title',String(200),nullable=False),Column('url',Text,nullable=False),Column('description',Text,nullable=False),Column('digest',String(64),nullable=False),Column('created_at',String(40),nullable=False))
decisions=table('ai_decisions',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('milestone_id',String(64),nullable=False),Column('agreement_id',ForeignKey('agreement_versions.id'),nullable=False),Column('requested_by',ForeignKey('users.id'),nullable=False),Column('request',Text,nullable=False),Column('result',JSON,nullable=False),Column('model_trace',JSON,nullable=False),Column('status',String(30),nullable=False),Column('created_at',String(40),nullable=False))
actions=table('joint_actions',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('kind',String(40),nullable=False),Column('payload',JSON,nullable=False),Column('digest',String(64),nullable=False),Column('status',String(20),nullable=False),Column('created_by',ForeignKey('users.id'),nullable=False),Column('created_at',String(40),nullable=False))
votes=table('joint_action_votes',Column('action_id',ForeignKey('joint_actions.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('digest',String(64),nullable=False),Column('created_at',String(40),nullable=False),UniqueConstraint('action_id','user_id'))
messages=table('messages',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('body',Text,nullable=False),Column('created_at',String(40),nullable=False))
audit=table('audit_events',Column('project_id',ForeignKey('platform_projects.id')),Column('actor_id',String(64)),Column('kind',String(80),nullable=False),Column('data',JSON,nullable=False),Column('created_at',String(40),nullable=False))
mail=table('mail_outbox',Column('user_id',ForeignKey('users.id')),Column('email',String(254),nullable=False),Column('subject',String(200),nullable=False),Column('body',Text,nullable=False),Column('status',String(20),nullable=False),Column('created_at',String(40),nullable=False))
jobs=table('jobs',Column('project_id',ForeignKey('platform_projects.id')),Column('kind',String(40),nullable=False),Column('payload',JSON,nullable=False),Column('status',String(20),nullable=False),Column('attempts',Integer,nullable=False,default=0),Column('run_at',String(40),nullable=False),Column('lease_until',String(40)),Column('lease_token',String(64)),Column('last_error',Text))
ledger=table('ledger_entries',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('external_id',String(160),unique=True,nullable=False),Column('account',String(40),nullable=False),Column('amount_cents',Integer,nullable=False),Column('currency',String(3),nullable=False),Column('created_at',String(40),nullable=False))
operations=table('payment_operations',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('payment_id',String(64),nullable=False),Column('kind',String(40),nullable=False),Column('status',String(30),nullable=False),Column('amount_cents',Integer,nullable=False),Column('provider_id',String(100)))
webhooks=table('provider_events',Column('payload',JSON,nullable=False),Column('status',String(20),nullable=False),Column('created_at',String(40),nullable=False))
limits=table('rate_limits',Column('count',Integer,nullable=False),Column('reset_at',String(40),nullable=False))

# Step 1 records share project identities, but never enter the payment engine.
field_changes=table('agreement_field_changes',Column('agreement_id',ForeignKey('agreement_versions.id'),nullable=False),Column('field_path',Text,nullable=False),Column('before',JSON),Column('after',JSON),Column('actor_id',ForeignKey('users.id'),nullable=False),Column('created_at',String(40),nullable=False))
validation_flags=table('validation_flags',Column('agreement_id',ForeignKey('agreement_versions.id'),nullable=False),Column('flag_id',String(64),nullable=False),Column('payload',JSON,nullable=False),UniqueConstraint('agreement_id','flag_id'))
signatures=table('signatures',Column('agreement_id',ForeignKey('agreement_versions.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('version_hash',String(64),nullable=False),Column('typed_name',String(200),nullable=False),Column('ip',String(80),nullable=False),Column('user_agent',String(500),nullable=False),Column('created_at',String(40),nullable=False),UniqueConstraint('agreement_id','user_id'))
threads=table('chat_threads',Column('project_id',ForeignKey('platform_projects.id'),unique=True,nullable=False))
chat_messages=table('chat_messages',Column('thread_id',ForeignKey('chat_threads.id'),nullable=False),Column('user_id',ForeignKey('users.id')),Column('body',Text,nullable=False),Column('field_path',Text),Column('kind',String(20),nullable=False),Column('created_at',String(40),nullable=False))
notifications=table('notifications',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('kind',String(80),nullable=False),Column('body',Text,nullable=False),Column('read',Boolean,default=False,nullable=False),Column('created_at',String(40),nullable=False))
milestone_payments=table('milestone_payments',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('agreement_id',ForeignKey('agreement_versions.id'),nullable=False),Column('milestone_id',String(64),nullable=False),Column('amount_minor',Integer,nullable=False),Column('currency',String(3),nullable=False),Column('status',String(32),nullable=False),Column('payload',JSON,nullable=False),Column('created_at',String(40),nullable=False),UniqueConstraint('project_id','agreement_id','milestone_id',name='uq_step1_payment_milestone'),CheckConstraint('amount_minor > 0',name='step1_payment_positive'))
chat_reads=table('chat_reads',Column('thread_id',ForeignKey('chat_threads.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('read_at',String(40),nullable=False),UniqueConstraint('thread_id','user_id'))
sandbox_recipients=table('sandbox_payout_recipients',Column('user_id',ForeignKey('users.id'),unique=True,nullable=False),Column('email',String(127),nullable=False),Column('updated_at',String(40),nullable=False))
milestone_payouts=table('milestone_payouts',Column('payment_id',ForeignKey('milestone_payments.id'),unique=True,nullable=False),Column('recipient_user_id',ForeignKey('users.id'),nullable=False),Column('recipient_email',String(127),nullable=False),Column('amount_minor',Integer,nullable=False),Column('currency',String(3),nullable=False),Column('status',String(32),nullable=False),Column('payload',JSON,nullable=False),Column('created_at',String(40),nullable=False),CheckConstraint('amount_minor > 0',name='step1_payout_positive'))
delivery_files=table('delivery_files',Column('project_id',ForeignKey('platform_projects.id'),nullable=False),Column('user_id',ForeignKey('users.id'),nullable=False),Column('filename',String(180),nullable=False),Column('digest',String(64),nullable=False),Column('content',Text,nullable=False),Column('size',Integer,nullable=False),Column('created_at',String(40),nullable=False))

@event.listens_for(metadata, 'after_create')
def append_only_guards(target, connection, **kw):
    # SQLite is used by local tests; PostgreSQL guards are installed by migration.
    if connection.dialect.name == 'sqlite':
        for name in ('audit_events','chat_messages','signatures','agreement_field_changes','delivery_files','evidence'):
            for operation in ('UPDATE','DELETE'):
                connection.exec_driver_sql(f"CREATE TRIGGER IF NOT EXISTS immutable_{name}_{operation} BEFORE {operation} ON {name} BEGIN SELECT RAISE(ABORT, 'append-only record'); END")
        connection.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS immutable_sent_agreement BEFORE UPDATE ON agreement_versions WHEN OLD.status = 'sent' BEGIN SELECT RAISE(ABORT, 'sent agreement is immutable'); END")
        connection.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS no_delete_sent_agreement BEFORE DELETE ON agreement_versions WHEN OLD.status = 'sent' BEGIN SELECT RAISE(ABORT, 'sent agreement is immutable'); END")
        connection.exec_driver_sql("CREATE TRIGGER IF NOT EXISTS fixed_user_role BEFORE UPDATE OF role ON users WHEN OLD.role IS NOT NULL AND NEW.role IS NOT OLD.role BEGIN SELECT RAISE(ABORT, 'account role is fixed'); END")

@lru_cache(maxsize=8)
def _engine(url):
    kw={'pool_pre_ping':True}
    if url.startswith('sqlite'):kw['connect_args']={'check_same_thread':False}
    eng=create_engine(url,**kw)
    if url.startswith('sqlite'):
        @event.listens_for(eng,'connect')
        def foreign_keys(connection, record):connection.execute('PRAGMA foreign_keys=ON')
    return eng
def engine():
    url=os.getenv('DATABASE_URL')
    if not url:raise RuntimeError('DATABASE_URL is required for authenticated platform mode.')
    return _engine(url)
def enabled():return bool(os.getenv('DATABASE_URL'))
def secure():return os.getenv('AUTH_ENABLED','false').lower()=='true'
def cipher():
    key=os.getenv('TOKEN_ENCRYPTION_KEY')
    if not key:raise RuntimeError('TOKEN_ENCRYPTION_KEY is required.')
    return Fernet(key.encode())
def encrypt(value):return cipher().encrypt(value.encode()).decode()
def decrypt(value):return cipher().decrypt(value.encode()).decode()
def rows(conn,t,where=None):
    query=select(t)
    return [dict(x) for x in conn.execute(query.where(where) if where is not None else query).mappings()]
def one(conn,t,where):return next(iter(rows(conn,t,where)),None)
def log(conn,kind,actor=None,project_id=None,**data):conn.execute(insert(audit).values(id=uid(),project_id=project_id,actor_id=actor,kind=kind,data=data,created_at=now()))
def pack(p):
    p=copy.deepcopy(p);p.pop('_db_version',None)
    v=p.get('vault',{})
    if v.get('token_id'):
        v['encrypted_token']=encrypt(v.pop('token_id'))
    return p
def unpack(row):
    p=copy.deepcopy(row['payload']);p['_db_version']=row['version'];p['_archived']=row['archived']
    v=p.get('vault',{})
    if v.get('encrypted_token'):v['token_id']=decrypt(v.pop('encrypted_token'))
    return p
def get_project(pid):
    with engine().connect() as conn:
        row=one(conn,projects,projects.c.id==pid)
        return unpack(row) if row else None
def all_projects():
    with engine().connect() as conn:return [unpack(r) for r in rows(conn,projects)]
def save_project(p,conn=None,archived=False):
    if conn is None:
        with engine().begin() as connection:return save_project(p,connection,archived)
    current=one(conn,projects,projects.c.id==p['id'])
    data=pack(p);data.pop('_archived',None)
    if current:
        version=p.get('_db_version',current['version'])
        result=conn.execute(update(projects).where(projects.c.id==p['id'],projects.c.version==version).values(payload=data,version=version+1))
        if result.rowcount!=1:raise HTTPException(409,'Project changed concurrently. Reload before retrying.')
        p['_db_version']=version+1
    else:
        conn.execute(insert(projects).values(id=p['id'],payload=data,version=1,archived=archived,created_at=p.get('created_at',now())))
        p['_db_version']=1
    # Normalize immutable monetary operations and balanced postings alongside project updates.
    for pay in p.get('payments',[]):
        for op in pay.get('operations',[]):
            values={'project_id':p['id'],'payment_id':pay['id'],'kind':op['kind'],'status':op['status'],'amount_cents':op['amount_cents'],'provider_id':op.get('provider_id')}
            if one(conn,operations,operations.c.id==op['key']):conn.execute(update(operations).where(operations.c.id==op['key']).values(**values))
            else:conn.execute(insert(operations).values(id=op['key'],**values))
        caps=pay.get('captures',[])
        if not caps and pay.get('capture_id') and pay.get('status')=='captured':caps=[{'id':pay['capture_id'],'amount_cents':pay['amount_cents']}]
        for cap in caps:
            for account,sign in [('provider_receivable',1),('project_liability',-1)]:
                external=f"capture:{cap['id']}:{account}"
                if not one(conn,ledger,ledger.c.external_id==external):conn.execute(insert(ledger).values(id=uid(),project_id=p['id'],external_id=external,account=account,amount_cents=sign*cap['amount_cents'],currency=p.get('currency','USD'),created_at=now()))
