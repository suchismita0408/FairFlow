import copy
import hashlib
from datetime import date,datetime,timedelta,timezone
import pytest
from sqlalchemy import insert,update
from app import db,paypal,payouts,negotiation as n,assurance,step1_ledger
from test_step1_auth_validation import portals
from test_step1_negotiation import start,call,sign_both,approve_both


def paid_first(portals,monkeypatch,payout_status='paid',merchant='amendment-test-merchant',first_due_days=6,second_due_days=15,deadline_days=22):
    monkeypatch.setenv('PAYPAL_CLIENT_ID',merchant)
    monkeypatch.setenv('PAYPAL_CLIENT_SECRET','amendment-test-secret')
    monkeypatch.setattr(paypal,'payout_sender',lambda:'amendment-test-sender')
    c,f,r=start(portals);pid=r['project']['id'];terms=copy.deepcopy(r['agreement']);today=date.today()
    terms['payment'].update(currency='USD',total_minor=2000)
    terms['timeline'].update(start_date=str(today+timedelta(days=1)),deadline=str(today+timedelta(days=deadline_days)))
    first=terms['timeline']['milestones'][0]
    first.update(amount_minor=1000,currency='USD',due_date=str(today+timedelta(days=first_due_days)))
    terms['timeline']['milestones'].append({**first,'id':'M2','title':'Second milestone','due_date':str(today+timedelta(days=second_due_days)),'deliverable_ids':['D2']})
    terms['scope']['deliverables'][0]['revision_rounds']=0
    terms['scope']['deliverables'].append({**terms['scope']['deliverables'][0],'id':'D2','name':'Second deliverable','milestone_id':'M2'})
    response=f.put(f'/api/freelancer/projects/{pid}/draft',json={'revision':r['revision'],'agreement':terms});assert response.status_code==200,response.text
    r=call(f,'freelancer',response.json(),'send');r=sign_both(c,f,r)
    uid=f.get('/api/auth/me').json()['user']['id'];recipient='amendment-recipient@example.com'
    assert f.put('/api/freelancer/payout-recipient',json={'email':recipient,'sandbox_account_confirmed':True}).status_code==200
    payment=dict(id=db.uid(),project_id=pid,agreement_id=r['project']['active_agreement_id'],milestone_id='M1',amount_minor=1000,currency='USD',status='captured',created_at=db.now(),payload={'capture_id':'PAID-CAPTURE','agreement_hash':r['versions'][-1]['digest'],'merchant_fingerprint':hashlib.sha256(merchant.encode()).hexdigest(),'payout_recipient':{'user_id':uid,'email':recipient}})
    payout=dict(id=db.uid(),payment_id=payment['id'],recipient_user_id=uid,recipient_email=recipient,amount_minor=1000,currency='USD',status=payout_status,created_at=db.now(),payload={'batch_id':'PAID-BATCH','capture_id':'PAID-CAPTURE','sender_fingerprint':'amendment-test-sender','provider_status':'SUCCESS' if payout_status=='paid' else 'PENDING'})
    with db.engine().begin() as conn:
        conn.execute(insert(db.milestone_payments).values(**payment));conn.execute(insert(db.milestone_payouts).values(**payout))
        step1_ledger.capture(conn,payment)
        if payout_status=='paid':step1_ledger.payout(conn,payment,payout,'paid')
    r=c.get(f'/api/client/projects/{pid}').json()
    return c,f,r,payment,payout


def change_second(c,r,amount=1500):
    terms=copy.deepcopy(r['agreement']);terms['timeline']['milestones'][1]['amount_minor']=amount;terms['payment']['total_minor']=1000+amount
    result=c.put(f"/api/client/projects/{r['project']['id']}/draft",json={'revision':r['revision'],'agreement':terms})
    assert result.status_code==200,result.text
    return result.json()


def test_same_project_amendment_preserves_paid_version_and_funds_only_new_terms(portals,monkeypatch):
    c,f,r,payment,payout=paid_first(portals,monkeypatch);pid=r['project']['id'];original=payment['agreement_id']
    with db.engine().connect() as conn:ledger=copy.deepcopy(db.rows(conn,db.ledger))
    assert r['amendment_blocked_reason'] is None
    r=call(c,'client',r,'amend');r=change_second(c,r)
    root=f'/api/client/projects/{pid}/assurance'
    assert c.post(root+'/start',json={'milestone_id':'M2','recipient_email':payout['recipient_email']}).status_code==409
    r=call(c,'client',r,'send');r,sha=approve_both(c,f,r)
    r=call(c,'client',r,'sign',typed_name='Alice Baker',version_hash=sha)
    assert r['project']['active_agreement_id']==original
    assert c.post(root+'/start',json={'milestone_id':'M2','recipient_email':payout['recipient_email']}).status_code==409
    r=call(f,'freelancer',r,'sign',typed_name='Rahul Kumar',version_hash=sha)
    assert r['project']['id']==pid and r['project']['active_agreement_id']!=original
    assert r['agreement']['basics']['title']=='Bakery landing page'
    shown=c.get(root).json()['payments'];assert len(shown)==1 and shown[0]['agreement_id']==original and shown[0]['payout']['status']=='paid'
    async def order(row,project_id):
        assert project_id==pid and row['amount_minor']==1500
        return {'id':'AMENDED-ORDER','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=AMENDED-ORDER'}]}
    monkeypatch.setattr(paypal,'step1_create_order',order)
    result=c.post(root+'/start',json={'milestone_id':'M2','recipient_email':payout['recipient_email']});assert result.status_code==200,result.text
    assert c.post(root+'/start',json={'milestone_id':'M1','recipient_email':payout['recipient_email']}).status_code==409
    with db.engine().connect() as conn:
        saved=db.one(conn,db.milestone_payments,db.milestone_payments.c.id==payment['id'])
        assert saved==payment
        assert db.rows(conn,db.ledger)==ledger
        new=assurance.payment(conn,pid,'M2');assert new['agreement_id']==r['project']['active_agreement_id']
        assert assurance.bound(saved,n.sent(conn,r['project']),conn)==saved
    assert len(c.get(root).json()['payments'])==2


@pytest.mark.parametrize('tamper',['paid_amount','paid_deliverable','currency','title','remove_paid','reorder'])
def test_paid_terms_cannot_be_changed_or_removed(portals,monkeypatch,tamper):
    c,f,r,_,_=paid_first(portals,monkeypatch);r=call(c,'client',r,'amend');terms=copy.deepcopy(r['agreement'])
    if tamper=='paid_amount':terms['timeline']['milestones'][0]['amount_minor']=900
    elif tamper=='paid_deliverable':terms['scope']['deliverables'][0]['description']='Rewrite already paid work.'
    elif tamper=='currency':terms['payment']['currency']='EUR'
    elif tamper=='title':terms['basics']['title']='Different project identity'
    elif tamper=='remove_paid':terms['timeline']['milestones'].pop(0)
    else:terms['timeline']['milestones'].reverse()
    response=c.put(f"/api/client/projects/{r['project']['id']}/draft",json={'revision':r['revision'],'agreement':terms})
    assert response.status_code==422,response.text


@pytest.mark.parametrize('state',['pending','attention','unclaimed','returned'])
def test_unsettled_payout_locks_first_but_allows_unfunded_second_amendment(portals,monkeypatch,state):
    c,f,r,payment,payout=paid_first(portals,monkeypatch,payout_status=state)
    assert r['amendment_blocked_reason'] is None
    r=call(c,'client',r,'amend')
    assert r['amendment_locks']['milestone_ids']==['M1']
    r=change_second(c,r)
    r=call(c,'client',r,'send');r=sign_both(c,f,r)
    assert r['agreement']['timeline']['milestones'][1]['amount_minor']==1500
    with db.engine().connect() as conn:
        assert db.one(conn,db.milestone_payments,db.milestone_payments.c.id==payment['id'])==payment
        assert db.one(conn,db.milestone_payouts,db.milestone_payouts.c.id==payout['id'])==payout


def test_authorized_second_milestone_cannot_be_repriced(portals,monkeypatch):
    c,f,r,payment,_=paid_first(portals,monkeypatch)
    with db.engine().begin() as conn:
        conn.execute(insert(db.milestone_payments).values(**{**payment,'id':db.uid(),'milestone_id':'M2','status':'authorized','payload':{'authorization_id':'ALREADY-FUNDED'}}))
    r=c.get(f"/api/client/projects/{r['project']['id']}").json()
    assert 'M2' in r['amendment_blocked_reason']
    call(c,'client',r,'amend',409)


def test_rejected_amendment_keeps_old_signed_terms(portals,monkeypatch):
    c,f,r,payment,_=paid_first(portals,monkeypatch)
    r=call(c,'client',r,'amend');r=change_second(c,r);r=call(c,'client',r,'send')
    r=call(f,'freelancer',r,'reject-amendment',reason='We do not agree to the new price.')
    assert r['project']['active_agreement_id']==payment['agreement_id']
    assert r['agreement']['timeline']['milestones'][1]['amount_minor']==1000


def test_late_amendment_allows_historical_start_but_requires_current_remaining_dates(portals,monkeypatch):
    c,f,r,payment,_=paid_first(portals,monkeypatch)
    future=datetime.now(timezone.utc)+timedelta(days=40)
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):return future.astimezone(tz) if tz else future.replace(tzinfo=None)
    monkeypatch.setattr(n,'datetime',Later)
    r=call(c,'client',r,'amend');terms=copy.deepcopy(r['agreement'])
    terms['timeline']['milestones'][1]['due_date']=str(future.date()+timedelta(days=5))
    terms['timeline']['deadline']=str(future.date()+timedelta(days=10))
    result=c.put(f"/api/client/projects/{r['project']['id']}/draft",json={'revision':r['revision'],'agreement':terms});assert result.status_code==200,result.text
    r=call(c,'client',result.json(),'send');r=sign_both(c,f,r)
    assert r['project']['active_agreement_id']!=payment['agreement_id']
    assert all(item['fits'] for item in r['payment_rules']['milestones'])


def test_payout_return_before_second_signature_keeps_previous_agreement(portals,monkeypatch):
    c,f,r,payment,payout=paid_first(portals,monkeypatch)
    r=call(c,'client',r,'amend');r=change_second(c,r);r=call(c,'client',r,'send');r,sha=approve_both(c,f,r)
    r=call(c,'client',r,'sign',typed_name='Alice Baker',version_hash=sha)
    with db.engine().begin() as conn:conn.execute(update(db.milestone_payouts).where(db.milestone_payouts.c.id==payout['id']).values(status='returned'))
    call(f,'freelancer',r,'sign',409,typed_name='Rahul Kumar',version_hash=sha)
    r=c.get(f"/api/client/projects/{r['project']['id']}").json()
    assert r['project']['active_agreement_id']==payment['agreement_id']


def test_second_payout_completes_project_across_two_signed_versions(portals,monkeypatch):
    from test_step1_assurance import automatic_release
    c,f,r,paid,first_payout=paid_first(portals,monkeypatch);pid=r['project']['id']
    r=call(c,'client',r,'amend');r=change_second(c,r);r=call(c,'client',r,'send');r=sign_both(c,f,r)
    intent={}
    async def order(row,pid):return {'id':'SECOND-ORDER','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=SECOND-ORDER'}]}
    async def authorize(row):return {'id':'SECOND-AUTH','expiration_time':(datetime.now(timezone.utc)+timedelta(days=29)).isoformat()}
    async def capture(row):return {'id':'SECOND-CAPTURE','amount':paypal.step1_amount(row['amount_minor'],row['currency']),'status':'COMPLETED'}
    async def read_capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(1500 if cid=='SECOND-CAPTURE' else 1000,'USD')}
    async def send(row):
        intent.update(copy.deepcopy(row));return {'batch_header':{'payout_batch_id':'SECOND-BATCH','batch_status':'PENDING'}}
    async def status(batch):
        row=first_payout if batch=='PAID-BATCH' else intent
        return {'batch_header':{'payout_batch_id':batch,'sender_batch_header':{'sender_batch_id':row['id']}},'items':[{'payout_item_id':'ITEM-'+row['id'],'transaction_status':'SUCCESS','payout_item':{'sender_item_id':row['id'],'recipient_type':'EMAIL','receiver':row['recipient_email'],'amount':{'currency':'USD','value':'10.00' if batch=='PAID-BATCH' else '15.00'}}}]}
    for name,fn in [('step1_create_order',order),('step1_authorize',authorize),('step1_capture',capture),('step1_get_capture',read_capture),('step1_create_payout',send),('step1_get_payout',status)]:monkeypatch.setattr(paypal,name,fn)
    root=f'/api/client/projects/{pid}/assurance'
    response=c.post(root+'/start',json={'milestone_id':'M2','recipient_email':first_payout['recipient_email']});assert response.status_code==200,response.text
    assert c.post(root+'/M2/authorize').status_code==200
    assert f.post(root.replace('/client/','/freelancer/')+'/deliver',json={'milestone_id':'M2','url':'https://example.com/updated-work','note':'The amended second milestone is complete.'}).status_code==200
    assert automatic_release(pid,'M2')['payout']['status']=='pending'
    assert c.post(root+'/M2/payout-status').json()['status']=='paid'
    # Historical receipt remains readable and cannot be paid twice.
    assert c.post(root+'/M1/payout-status').json()['status']=='paid'
    assert c.post(root+'/M1/payout',json={'recipient_email':first_payout['recipient_email'],'sandbox_payout_confirmed':True}).status_code==409
    project=next(p for p in c.get('/api/client/projects').json() if p['id']==pid)
    assert project['completed']
    from app import payment_scan
    import asyncio
    asyncio.run(payment_scan.scan(pid))
    with db.engine().connect() as conn:
        assert len(db.rows(conn,db.milestone_payments,db.milestone_payments.c.project_id==pid))==2
        assert len(db.rows(conn,db.milestone_payouts))==2


def test_ai_amendment_preserves_paid_milestone_and_does_not_sign(portals,monkeypatch):
    from app import chat_monitor
    from app.agreement_validation import digest
    c,f,r,payment,payout=paid_first(portals,monkeypatch);pid=r['project']['id']
    user=c.get('/api/auth/me').json()['user']
    with n.transaction() as conn:
        p,rev=n.project(conn,pid,user,True)
        before=copy.deepcopy(chat_monitor.terms(conn,p))
        p['chat_monitor']={'status':'reviewed','agreement_hash':digest(before),'findings':[
            {'field_path':'timeline.milestones.1.amount_minor','expected':1500,'state':'agreed','message_ids':['a','b']},
            {'field_path':'payment.total_minor','expected':2500,'state':'agreed','message_ids':['a','b']} ]}
        chat_monitor.auto_propose(conn,p,rev)
        assert p['active_agreement_id']==payment['agreement_id']
        v=n.sent(conn,p);assert v['payload']['timeline']['milestones'][0]==before['timeline']['milestones'][0]
        assert v['payload']['timeline']['milestones'][1]['amount_minor']==1500
        assert not n.signed(conn,v['id'])
