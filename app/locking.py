import asyncio, hashlib
from contextlib import asynccontextmanager
from sqlalchemy import text
from fastapi import HTTPException
from . import db
_locks={}
@asynccontextmanager
async def project_lock(pid):
    lock=_locks.setdefault(pid,asyncio.Lock())
    async with lock:
        conn=None
        key=int.from_bytes(hashlib.sha256(pid.encode()).digest()[:8],'big') & ((1<<63)-1)
        try:
            if db.enabled() and db.engine().dialect.name=='postgresql':
                conn=db.engine().connect()
                acquired=conn.execute(text('SELECT pg_try_advisory_lock(:key)'),{'key':key}).scalar()
                if not acquired:raise HTTPException(409,'Project is being updated. Retry shortly.')
            yield
        finally:
            if conn is not None:
                conn.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':key})
                conn.close()
