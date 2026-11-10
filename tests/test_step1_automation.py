import asyncio
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import insert,update
from app import db,paypal,assurance,payment_scan,worker
from test_step1_auth_validation import portals
from test_step1_assurance import signed_short_project


def test_step1_worker_leaves_legacy_jobs_unclaimed(portals,monkeypatch):
    monkeypatch.setenv('WORKER_STEP1_ONLY','true')
    with db.engine().begin() as conn:
        for jid,kind in [('legacy','joint_action'),('email','mail'),('current','step1_scan')]:
            conn.execute(insert(db.jobs).values(id=jid,project_id=None,kind=kind,payload={},status='queued',attempts=0,run_at=db.now()))
    claimed=[worker.claim(),worker.claim()]
    assert {job['id'] for job in claimed}=={'email','current'}
    assert worker.claim() is None
    with db.engine().connect() as conn:
        legacy=db.one(conn,db.jobs,db.jobs.c.id=='legacy')
        assert legacy['status']=='queued' and legacy['attempts']==0
    monkeypatch.setenv('WORKER_STEP1_ONLY','false')
    assert worker.claim()['id']=='legacy'


def prepared(portals,monkeypatch,auto=True):
    c,f,r=signed_short_project(portals,auto=auto);pid=r['project']['id'];m=r['agreement']['timeline']['milestones'][0]
    f.put('/api/freelancer/payout-recipient',json={'email':'seller@business.example.com','sandbox_account_confirmed':True})
    clock=datetime.now(timezone.utc)
    calls=[]
    async def order(row,pid):return {'id':'ORDER','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=ORDER'}]}
    async def auth(row):return {'id':'AUTH','expiration_time':(clock+timedelta(days=29)).isoformat()}
    async def capture(row):calls.append('capture');return {'id':'CAP'}
    async def read_capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    async def payout(row):calls.append('payout');return {'batch_header':{'payout_batch_id':'BATCH','batch_status':'PENDING'}}
    monkeypatch.setattr(paypal,'step1_create_order',order);monkeypatch.setattr(paypal,'step1_authorize',auth)
    monkeypatch.setattr(paypal,'step1_capture',capture);monkeypatch.setattr(paypal,'step1_get_capture',read_capture);monkeypatch.setattr(paypal,'step1_create_payout',payout)
    root=f'/api/client/projects/{pid}/assurance';fr=root.replace('/client/','/freelancer/')
    if auto:assert c.post(root+'/start',json={'milestone_id':m['id']}).status_code==409
    result=c.post(root+'/start',json={'milestone_id':m['id'],'recipient_email':'seller@business.example.com'})
    assert result.status_code==200,result.text
    assert c.post(root+'/'+m['id']+'/authorize').status_code==200
    delivered=f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/delivery','note':'Delivered all the agreed work.'})
    assert delivered.status_code==200,delivered.text
    return c,f,pid,m,root,fr,calls,delivered.json()


def test_ghost_client_auto_release_exactly_once_after_review(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    due=datetime.fromisoformat(row['review_deadline'])
    asyncio.run(payment_scan.scan(pid,now=due-timedelta(seconds=1)))
    assert calls==[]
    asyncio.run(payment_scan.scan(pid,now=due))
    assert calls==['capture','payout']
    with db.engine().connect() as conn:
        events=db.rows(conn,db.audit,(db.audit.c.project_id==pid)&(db.audit.c.kind=='milestone_auto_release_started'))
        assert len(events)==1 and events[0]['actor_id'] is None
    result=c.post(root+'/'+m['id']+'/approve',json={'recipient_email':'seller@business.example.com','sandbox_payout_confirmed':True})
    assert result.status_code==409 and calls==['capture','payout']
    assert c.get(root).json()['payments'][0]['payout']['status']=='pending'


def test_no_checkout_auto_consent_no_timeout_capture(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    # Historical funding without checkout consent must never be silently upgraded.
    with db.engine().begin() as conn:
        saved=assurance.payment(conn,pid,m['id']);assurance.save(conn,saved,'in_review',auto_release=False)
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(row['review_deadline'])+timedelta(seconds=1)))
    assert calls==[] and c.get(root).json()['payments'][0]['status']=='in_review'


def test_revision_and_dispute_pause_timeout_and_require_bilateral_resolution(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    route=root+'/'+m['id']
    outsider=portals('client','outsider@example.com')
    objection={'kind':'revision','reason':'Please fix the mobile layout.','deliverable_id':'D1'}
    assert outsider.post(route+'/object',json=objection).status_code==404
    assert f.post(fr+'/'+m['id']+'/object',json=objection).status_code==403
    result=c.post(route+'/object',json=objection)
    assert result.status_code==200 and result.json()['revision_counts']=={'D1':1}
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(row['review_deadline'])))
    assert calls==[]
    assert f.post(fr+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/v2','note':'Mobile layout is now fixed.'}).status_code==200
    assert c.post(route+'/object',json={'kind':'dispute','reason':'We disagree about the agreed layout.'}).status_code==200
    reason={'reason':'Both parties agree the delivered layout meets the signed criteria.'}
    first=c.post(route+'/resolve',json=reason);assert first.json()['status']=='disputed'
    assert c.post(route+'/approve',json={'recipient_email':'seller@business.example.com','sandbox_payout_confirmed':True}).status_code==409
    assert f.post(fr+'/'+m['id']+'/resolve',json={'reason':'Different wording that was not agreed.'}).status_code==409
    final=f.post(fr+'/'+m['id']+'/resolve',json=reason)
    assert final.status_code==200 and final.json()['status']=='in_review'
    assert final.json()['objection'] is None and calls==[]


def test_expiry_pauses_payment_and_notifies_once(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    clock=datetime.fromisoformat(row['expires_at'])+timedelta(seconds=1)
    asyncio.run(payment_scan.scan(pid,now=clock));asyncio.run(payment_scan.scan(pid,now=clock))
    assert calls==[] and c.get(root).json()['payments'][0]['status']=='expired'
    with db.engine().connect() as conn:
        assert len(db.rows(conn,db.audit,(db.audit.c.project_id==pid)&(db.audit.c.kind=='sandbox_authorization_expired')))==1


def test_reauthorization_preserves_original_expiry_and_uses_new_id(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    route=root+'/'+m['id']+'/reauthorize'
    assert c.post(route).status_code==409
    with db.engine().begin() as conn:
        saved=assurance.payment(conn,pid,m['id']);assurance.save(conn,saved,'in_review',authorized_at=(datetime.now(timezone.utc)-timedelta(days=4)).isoformat())
    async def renew(row):return {'id':'AUTH-NEW','expiration_time':(datetime.now(timezone.utc)+timedelta(days=40)).isoformat()}
    monkeypatch.setattr(paypal,'step1_reauthorize',renew)
    result=c.post(route);assert result.status_code==200,result.text
    assert result.json()['authorization_id']=='AUTH-NEW'
    assert datetime.fromisoformat(result.json()['expires_at'])==datetime.fromisoformat(row['expires_at'])
    assert c.post(route).status_code==409


def test_worker_enqueues_step1_scan_and_reminder_is_once(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    worker.enqueue_scans();worker.enqueue_scans()
    with db.engine().connect() as conn:
        jobs=db.rows(conn,db.jobs,db.jobs.c.project_id==pid)
        assert len(jobs)==1 and jobs[0]['kind']=='step1_scan'
    clock=datetime.fromisoformat(row['review_deadline'])-timedelta(hours=12)
    asyncio.run(payment_scan.scan(pid,now=clock));asyncio.run(payment_scan.scan(pid,now=clock))
    with db.engine().connect() as conn:
        notices=db.rows(conn,db.notifications,(db.notifications.c.project_id==pid)&(db.notifications.c.kind=='review_reminder'))
        assert len(notices)==1


def test_timeout_and_manual_approval_race_capture_only_once(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    with db.engine().begin() as conn:
        saved=assurance.payment(conn,pid,m['id']);assurance.save(conn,saved,'in_review',review_deadline=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
        member=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='client'))
        user=db.one(conn,db.users,db.users.c.id==member['user_id'])
    def run(auto):
        try:
            return asyncio.run(assurance.release_payment(pid,m['id'],user,assurance.Approval(recipient_email='seller@business.example.com',sandbox_payout_confirmed=True),automatic=auto))['status']
        except Exception as exc:
            from fastapi import HTTPException
            assert isinstance(exc,HTTPException) and exc.status_code==409
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,[True,False]))
    assert sorted(results)==['captured','conflict'] and calls==['capture','payout']


def test_reauthorization_adapter_rejects_wrong_amount(monkeypatch):
    from fastapi import HTTPException
    row={'amount_minor':1000,'currency':'USD','payload':{'authorization_id':'OLD','reauthorize_key':'KEY'}}
    calls=[]
    async def request(method,path,body,key):
        calls.append((method,path,body,key));return {'id':'NEW','status':'CREATED','amount':{'currency_code':'USD','value':'1.00'}}
    monkeypatch.setattr(paypal,'call',request)
    with pytest.raises(HTTPException):asyncio.run(paypal.step1_reauthorize(row))
    assert calls[0]==('POST','/v2/payments/authorizations/OLD/reauthorize',{'amount':{'currency_code':'USD','value':'10.00'}},'KEY')


def test_verified_webhook_pauses_payment_and_is_deduplicated(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    monkeypatch.setenv('PAYPAL_WEBHOOK_ID','TEST-WEBHOOK')
    verified=False
    async def verify(*args,**kwargs):return {'verification_status':'SUCCESS' if verified else 'FAILURE'}
    monkeypatch.setattr(paypal,'call',verify)
    event={'id':'EVENT-1','event_type':'PAYMENT.AUTHORIZATION.VOIDED','resource':{'id':'AUTH'}}
    assert c.post('/api/webhooks/paypal',json=event).status_code==400
    assert c.get(root).json()['payments'][0]['status']=='in_review'
    verified=True
    assert c.post('/api/webhooks/paypal',json=event).status_code==200
    assert c.post('/api/webhooks/paypal',json=event).status_code==200
    assert c.get(root).json()['payments'][0]['status']=='attention'
    with db.engine().connect() as conn:
        assert len(db.rows(conn,db.webhooks,db.webhooks.c.id=='EVENT-1'))==1
    assert calls==[]


def test_completed_requires_verified_payout_not_just_capture(portals,monkeypatch):
    c,f,pid,m,root,fr,calls,row=prepared(portals,monkeypatch)
    asyncio.run(payment_scan.scan(pid,now=datetime.fromisoformat(row['review_deadline'])))
    projects=c.get('/api/client/projects').json()
    assert not next(p for p in projects if p['id']==pid)['completed']
    with db.engine().begin() as conn:
        payment=assurance.payment(conn,pid,m['id'])
        conn.execute(update(db.milestone_payouts).where(db.milestone_payouts.c.payment_id==payment['id']).values(status='paid'))
    projects=c.get('/api/client/projects').json()
    assert next(p for p in projects if p['id']==pid)['completed']
