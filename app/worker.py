"""Durable PostgreSQL job queue, leased work, per-project locking, bounded retries."""
import asyncio,os,contextlib
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,insert,update
from fastapi import HTTPException
from . import db,store,agile
from .locking import project_lock

def later(seconds):return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
def enqueue_scans():
    with db.engine().begin() as conn:
        for row in db.rows(conn,db.projects,db.projects.c.archived==False):
            step1=row['payload'].get('engine')=='agreement_step1'
            if not step1 and os.getenv('WORKER_STEP1_ONLY','false')=='true':continue
            if step1 and not row['payload'].get('active_agreement_id'):continue
            jid='scan-'+row['id'];job=db.one(conn,db.jobs,db.jobs.c.id==jid)
            if not job:conn.execute(insert(db.jobs).values(id=jid,project_id=row['id'],kind='step1_scan' if step1 else 'scan',payload={},status='queued',attempts=0,run_at=db.now()))
            elif job['status'] in ('done','failed') and job['run_at']<=db.now():conn.execute(update(db.jobs).where(db.jobs.c.id==jid).values(status='queued',attempts=0,run_at=db.now(),last_error=None))

def claim():
    with db.engine().begin() as conn:
        query=select(db.jobs).where(((db.jobs.c.status=='queued')&(db.jobs.c.run_at<=db.now()))|((db.jobs.c.status=='running')&(db.jobs.c.lease_until<db.now()))).order_by(db.jobs.c.run_at).with_for_update(skip_locked=True).limit(1)
        if os.getenv('WORKER_STEP1_ONLY','false')=='true':
            query=query.where(db.jobs.c.kind.in_(('step1_scan','mail')))
        row=conn.execute(query).mappings().first()
        if not row:return None
        job=dict(row);lease=db.uid()
        conn.execute(update(db.jobs).where(db.jobs.c.id==job['id']).values(status='running',lease_token=lease,lease_until=later(180),attempts=job['attempts']+1))
        job['lease_token']=lease;return job

async def process(job):
    from . import workspace,reconciliation
    if job['kind']=='mail':
        from .mail_delivery import deliver
        await asyncio.to_thread(deliver,job['payload']['mail_id'])
        return
    pid=job['project_id']
    if not pid:return
    if job['kind']!='step1_scan' and os.getenv('WORKER_STEP1_ONLY','false')=='true':return
    async with project_lock(pid):
        if job['kind']=='step1_scan':
            from .payment_scan import scan
            await scan(pid)
            return
        p=store.get(pid)
        if not p or p.get('_archived'):return
        if job['kind']=='scan':
            if not p.get('external_payment_issue'):await agile.scan(p)
            # Review reminders are durable and emitted once per delivery deadline.
            p=store.get(pid)
            for m in p['milestones']:
                if m['status']=='in_review' and m.get('review_deadline'):
                    key=m['id']+':'+m['review_deadline']
                    if key not in p.get('notified_reviews',[]):
                        p.setdefault('notified_reviews',[]).append(key)
                        with db.engine().begin() as conn:
                            workspace.notification(conn,p,'Milestone ready for review',m['title']+' is ready. Review deadline: '+m['review_deadline'])
                            db.save_project(p,conn)
        elif job['kind']=='reconcile':await reconciliation.reconcile(p)
        elif job['kind']=='joint_action':
            aid=job['payload']['action_id']
            with db.engine().connect() as conn:action=db.one(conn,db.actions,db.actions.c.id==aid)
            if not action or action['status']=='applied':return
            await workspace.apply_joint(action)
            with db.engine().begin() as conn:conn.execute(update(db.actions).where(db.actions.c.id==aid).values(status='applied'))

async def heartbeat(job):
    while True:
        await asyncio.sleep(30)
        with db.engine().begin() as conn:
            conn.execute(update(db.jobs).where(db.jobs.c.id==job['id'],db.jobs.c.lease_token==job['lease_token'],db.jobs.c.status=='running').values(lease_until=later(180)))

async def tick(max_jobs=20):
    from .negotiation import sweep_invitations
    from .mail_delivery import enqueue
    sweep_invitations()
    enqueue()
    enqueue_scans();done=0
    for _ in range(max_jobs):
        job=claim()
        if not job:break
        pulse=asyncio.create_task(heartbeat(job))
        try:
            await process(job)
        except Exception as exc:
            # Money-changing failures do not loop; participants must review the uncertainty.
            message=str(exc.detail) if isinstance(exc,HTTPException) else type(exc).__name__
            retry=job['kind'] in ('scan','reconcile','step1_scan') and job['attempts']<4
            with db.engine().begin() as conn:
                conn.execute(update(db.jobs).where(db.jobs.c.id==job['id'],db.jobs.c.lease_token==job['lease_token']).values(status='queued' if retry else 'failed',run_at=later(min(300,2**(job['attempts']+1))),last_error=message,lease_token=None,lease_until=None))
                if job['kind']=='joint_action':conn.execute(update(db.actions).where(db.actions.c.id==job['payload']['action_id']).values(status='needs_review'))
        else:
            with db.engine().begin() as conn:conn.execute(update(db.jobs).where(db.jobs.c.id==job['id'],db.jobs.c.lease_token==job['lease_token']).values(status='done',run_at=later(15),lease_token=None,lease_until=None,last_error=None))
            done+=1
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):await pulse
    from .chat_monitor import retry_pending
    await retry_pending()
    return done

async def run():
    while True:
        try:await tick()
        except Exception:
            import logging;logging.exception('Durable worker scan failed')
        await asyncio.sleep(5)

if __name__=='__main__':
    from dotenv import load_dotenv
    load_dotenv()
    import main  # Bind milestone runtime; this does not start an HTTP server.
    asyncio.run(run())
