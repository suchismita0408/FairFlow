from alembic.config import Config
from alembic import command
from sqlalchemy import inspect,text
from app import db

def test_fresh_migration_and_previous_schema_upgrade(tmp_path,monkeypatch):
    for existing in (False,True):
        monkeypatch.setenv('DATABASE_URL','sqlite:///'+str(tmp_path/('old.db' if existing else 'fresh.db')))
        engine=db.engine()
        if existing:
            with engine.begin() as conn:
                conn.execute(text('CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, email VARCHAR(254) NOT NULL UNIQUE, name VARCHAR(100) NOT NULL, password_hash TEXT NOT NULL, verified BOOLEAN NOT NULL, created_at VARCHAR(40) NOT NULL)'))
                conn.execute(text('CREATE TABLE sessions (id VARCHAR(64) PRIMARY KEY, user_id VARCHAR(64) NOT NULL REFERENCES users(id), csrf VARCHAR(100) NOT NULL, expires_at VARCHAR(40) NOT NULL)'))
                conn.execute(text('CREATE TABLE platform_projects (id VARCHAR(64) PRIMARY KEY, payload JSON NOT NULL, version INTEGER NOT NULL, archived BOOLEAN NOT NULL, created_at VARCHAR(40) NOT NULL)'))
                conn.execute(text('CREATE TABLE agreement_versions (id VARCHAR(64) PRIMARY KEY, project_id VARCHAR(64) NOT NULL REFERENCES platform_projects(id), number INTEGER NOT NULL, digest VARCHAR(64) NOT NULL, payload JSON NOT NULL, status VARCHAR(20) NOT NULL, created_by VARCHAR(64) NOT NULL REFERENCES users(id), created_at VARCHAR(40) NOT NULL, UNIQUE(project_id,number))'))
                conn.execute(text("INSERT INTO users VALUES ('old-user','old@example.com','Existing user','preserved-hash',1,'2026-01-01')"))
                conn.execute(text("INSERT INTO sessions VALUES ('old-session','old-user','old-csrf','2099-01-01')"))
                conn.execute(text("INSERT INTO platform_projects VALUES ('old-project','{}',1,0,'2026-01-01')"))
                conn.execute(text("INSERT INTO agreement_versions VALUES ('old-agreement','old-project',1,'old-hash','{}','proposed','old-user','2026-01-01')"))
                conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY NOT NULL)'))
                conn.execute(text("INSERT INTO alembic_version VALUES ('0001_platform')"))
        command.upgrade(Config('alembic.ini'),'head')
        inspector=inspect(engine)
        assert {'role','handle'}<={c['name'] for c in inspector.get_columns('users')}
        assert {'chat_messages','signatures','validation_flags','client_profiles','freelancer_profiles'}<=set(inspector.get_table_names())
        with engine.connect() as conn:
            assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0005_delivery_evidence'
            if existing:
                old=db.one(conn,db.users,db.users.c.id=='old-user')
                assert old['password_hash']=='preserved-hash' and old['role'] is None
                assert db.one(conn,db.sessions,db.sessions.c.id=='old-session')['csrf']=='old-csrf'
                assert db.one(conn,db.agreements,db.agreements.c.id=='old-agreement')['digest']=='old-hash'
                assert conn.exec_driver_sql('PRAGMA foreign_key_check').fetchall()==[]
