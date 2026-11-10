import json, os, sqlite3
from contextlib import contextmanager

@contextmanager
def connection():
    db = sqlite3.connect(os.getenv('DATABASE_PATH', 'fairflow.db'), timeout=15)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS webhook_events (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    try:
        yield db
        db.commit()
    finally:
        db.close()

def all_projects():
    from . import db
    if db.enabled(): return db.all_projects()
    with connection() as db:
        return [json.loads(row[0]) for row in db.execute('SELECT data FROM projects ORDER BY rowid DESC')]

def get(pid):
    from . import db
    if db.enabled(): return db.get_project(pid)
    with connection() as db:
        row = db.execute('SELECT data FROM projects WHERE id=?', (pid,)).fetchone()
        return json.loads(row[0]) if row else None

def save(project):
    from . import db
    if db.enabled(): return db.save_project(project)
    with connection() as db:
        db.execute('INSERT INTO projects VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (project['id'], json.dumps(project)))
