"""Snapshot SQLite and import legacy projects as unassigned, read-only archives."""
from pathlib import Path
from datetime import datetime, timezone
import sqlite3, json, os
from dotenv import load_dotenv
load_dotenv()
from app import db
from sqlalchemy import insert

def run():
    source=Path(os.getenv('DATABASE_PATH','fairflow.db')).resolve()
    if not source.exists():print('No legacy SQLite database found.');return
    folder=Path('backups');folder.mkdir(exist_ok=True)
    backup=folder/('legacy-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')+'.db')
    with sqlite3.connect(source) as original,sqlite3.connect(backup) as snapshot:original.backup(snapshot)
    count=0
    with sqlite3.connect(backup) as snapshot,db.engine().begin() as conn:
        for pid,data in snapshot.execute('SELECT id,data FROM projects'):
            if not db.one(conn,db.projects,db.projects.c.id==pid):
                db.save_project(json.loads(data),conn,archived=True);count+=1
        for eid,data in snapshot.execute('SELECT id,data FROM webhook_events'):
            if not db.one(conn,db.webhooks,db.webhooks.c.id==eid):conn.execute(insert(db.webhooks).values(id=eid,payload=json.loads(data),status='archived',created_at=db.now()))
    print(f'Preserved SQLite snapshot at {backup}; imported {count} projects as unassigned archives. No ownership or consent inferred.')
if __name__=='__main__':run()
