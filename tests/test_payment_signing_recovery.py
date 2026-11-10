import asyncio
import copy
from datetime import datetime,timedelta,timezone,date
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import insert,update
from app import db,negotiation as n,paypal,payouts
from app.agreement_validation import validate
from app.agreement_examples import example_agreement
from app.payment_policy import reserve_days,summary
from test_step1_auth_validation import portals
from test_step1_negotiation import start,approve_both,call
from test_step1_payouts import setup_payment


def test_separate_sender_credentials_and_frozen_sender(monkeypatch,tmp_path):
    from dotenv import set_key
    path=tmp_path/'payout.env'
    set_key(str(path),'PAYPAL_CLIENT_ID','us-sandbox-app')
    set_key(str(path),'PAYPAL_CLIENT_SECRET','test-secret')
    monkeypatch.setenv('PAYPAL_PAYOUT_CREDENTIALS_FILE',str(path))
    monkeypatch.setenv('PAYPAL_CLIENT_ID','original-capture-app')
    assert paypal.payout_credentials()==('us-sandbox-app','test-secret')
    original=paypal.payout_sender()
    set_key(str(path),'PAYPAL_CLIENT_ID','another-sender')
    with pytest.raises(HTTPException):
        paypal.check_payout_sender({'payload':{'sender_fingerprint':original}})


def test_incomplete_separate_sender_does_not_fall_back(monkeypatch,tmp_path):
    from dotenv import set_key
    path=tmp_path/'payout.env'
    set_key(str(path),'PAYPAL_CLIENT_ID','us-app')
    monkeypatch.setenv('PAYPAL_PAYOUT_CREDENTIALS_FILE',str(path))
    with pytest.raises(HTTPException):paypal.payout_credentials()


def test_signature_requires_payment_rules_confirmation(portals):
    c,f,r=start(portals);r,sha=approve_both(c,f,r)
    result=c.post(f"/api/client/projects/{r['project']['id']}/actions",json=dict(revision=r['revision'],action='sign',version_hash=sha,typed_name='Alice Baker'))
    assert result.status_code==422 and '29-day' in result.text
    r=call(c,'client',r,'sign',version_hash=sha,typed_name='Alice Baker')
    with db.engine().connect() as conn:
        events=db.rows(conn,db.audit,db.audit.c.kind=='payment_rules_confirmed')
        assert len(events)==1 and events[0]['data']['version_hash']==sha
        assert events[0]['data']['rules']['milestones'][0]['fits']


@pytest.mark.parametrize('invalid',['duration','final_deadline','revision_budget'])
def test_historical_invalid_version_cannot_be_signed_by_either_party(portals,invalid):
    c,f,r=start(portals);pid=r['project']['id']
    user=c.get('/api/auth/me').json()['user']
    terms=copy.deepcopy(r['agreement'])
    if invalid=='duration':terms['timeline']['milestones'][0]['due_date']=str(date.today()+timedelta(days=40))
    elif invalid=='final_deadline':terms['timeline']['deadline']=str(date.today()+timedelta(days=60))
    else:terms['scope']['deliverables'][0]['revision_rounds']=10
    # Simulate an immutable version created under the older validation rules.
    with n.transaction() as conn:
        p,revision=n.project(conn,pid,user,True)
        v=n.snapshot(conn,p,terms,user)
        p.update(status='READY_TO_SIGN',draft=None,draft_owner=None)
        n.write(conn,p,revision)
        for member in db.rows(conn,db.members,db.members.c.project_id==pid):
            conn.execute(insert(db.acceptances).values(id=db.uid(),agreement_id=v['id'],user_id=member['user_id'],role=member['role'],digest=v['digest'],created_at=db.now()))
    for client,role,name in ((c,'client','Alice Baker'),(f,'freelancer','Rahul Kumar')):
        r=client.get(f'/api/{role}/projects/{pid}').json()
        response=client.post(f'/api/{role}/projects/{pid}/actions',json=dict(action='sign',revision=r['revision'],version_hash=v['digest'],typed_name=name,payment_rules_confirmed=True))
        assert response.status_code==422,response.text
        assert response.json()['detail']['flags']
    with db.engine().connect() as conn:assert not n.signed(conn,v['id'])


def test_all_revision_rounds_and_exact_29_day_boundary():
    a=example_agreement(date(2030,1,1));m=a['timeline']['milestones'][0]
    a['timeline'].update(start_date='2030-01-01',deadline='2030-01-30')
    m['due_date']='2030-01-16'
    assert reserve_days(a,m)==14
    assert summary(a)['milestones'][0]['days_required']==29
    assert not [f for f in validate(a) if f['severity']=='BLOCKER']
    m['due_date']='2030-01-17'
    assert not summary(a)['milestones'][0]['fits']
    assert any('29-day' in f['message'] for f in validate(a))
    assert reserve_days(a,m,{'D1':1})==9


def failed_payout(portals,monkeypatch):
    c,f,pid,mid,payment=setup_payment(portals)
    email='seller@business.example.com'
    f.put('/api/freelancer/payout-recipient',json={'email':email,'sandbox_account_confirmed':True})
    async def capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(payment['amount_minor'],payment['currency'])}
    async def fail(row):raise HTTPException(502,'Payout response lost')
    monkeypatch.setattr(paypal,'step1_get_capture',capture);monkeypatch.setattr(paypal,'step1_create_payout',fail)
    root=f'/api/client/projects/{pid}/assurance/{mid}'
    data={'recipient_email':email,'sandbox_payout_confirmed':True}
    assert c.post(root+'/payout',json=data).status_code==502
    with db.engine().connect() as conn:row=db.one(conn,db.milestone_payouts,db.milestone_payouts.c.payment_id==payment['id'])
    return c,f,root,data,row


def test_recovery_same_intent_frozen_recipient_and_verified_receipt(portals,monkeypatch):
    c,f,root,data,row=failed_payout(portals,monkeypatch)
    f.put('/api/freelancer/payout-recipient',json={'email':'changed@business.example.com','sandbox_account_confirmed':True})
    calls=[]
    async def send(intent):
        calls.append(intent)
        return {'batch_header':{'payout_batch_id':'RECOVERED','batch_status':'SUCCESS'}}
    monkeypatch.setattr(paypal,'step1_create_payout',send)
    assert f.post(root.replace('/client/','/freelancer/')+'/payout-recover',json=data).status_code==403
    assert c.post(root+'/payout-recover',json={**data,'recipient_email':'changed@business.example.com'}).status_code==409
    result=c.post(root+'/payout-recover',json=data)
    assert result.status_code==200 and result.json()['status']=='pending',result.text
    assert len(calls)==1 and calls[0]['id']==row['id'] and calls[0]['recipient_email']==row['recipient_email']
    assert calls[0]['amount_minor']==row['amount_minor']
    assert c.post(root+'/payout-recover',json=data).status_code==409
    async def status(batch):
        amount=paypal.step1_amount(row['amount_minor'],row['currency'])
        return {'batch_header':{'payout_batch_id':batch,'sender_batch_header':{'sender_batch_id':row['id']}},'items':[{'payout_item_id':'ITEM','transaction_id':'TX','transaction_status':'SUCCESS','payout_item':{'sender_item_id':row['id'],'recipient_type':'EMAIL','receiver':row['recipient_email'],'amount':{'currency':row['currency'],'value':amount['value']}}}]}
    monkeypatch.setattr(paypal,'step1_get_payout',status)
    assert c.post(root+'/payout-status').json()['status']=='paid'


def test_recovery_expired_window_and_concurrent_calls(portals,monkeypatch):
    c,f,root,data,row=failed_payout(portals,monkeypatch)
    with db.engine().begin() as conn:conn.execute(update(db.milestone_payouts).where(db.milestone_payouts.c.id==row['id']).values(created_at=(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()))
    assert c.post(root+'/payout-recover',json=data).status_code==409
    with db.engine().begin() as conn:conn.execute(update(db.milestone_payouts).where(db.milestone_payouts.c.id==row['id']).values(created_at=db.now()))
    calls=[]
    async def send(intent):
        calls.append(intent['id']);await asyncio.sleep(.05)
        return {'batch_header':{'payout_batch_id':'ONE'}}
    monkeypatch.setattr(paypal,'step1_create_payout',send)
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(lambda _:c.post(root+'/payout-recover',json=data).status_code,range(2)))
    assert sorted(statuses)==[200,409] and calls==[row['id']]


def test_known_payout_ineligibility_does_not_globally_block_client_checkout(portals,monkeypatch):
    c,f,root,data,row=failed_payout(portals,monkeypatch)
    with db.engine().begin() as conn:
        payouts.save(conn,row,'attention',attention='PayPal request failed. Code: PAYOUT_NOT_AVAILABLE')
    base=root.rsplit('/',1)[0]
    response=c.get(base)
    assert response.status_code==200 and response.json()['payout_setup_error']
    # A nonexistent milestone fails its own validation, not an unrelated
    # global payout gate. Capturing still checks payout setup independently.
    assert c.post(base+'/start',json={'milestone_id':'M2','recipient_email':data['recipient_email']}).status_code==404


def test_second_milestone_requires_first_capture_and_verifies_its_own_receipt(portals,monkeypatch):
    from test_step1_negotiation import create,sign_both
    c=portals('client');f=portals('freelancer');room=create(c);pid=room['project']['id']
    terms=copy.deepcopy(room['agreement']);today=date.today()
    terms['timeline'].update(start_date=str(today+timedelta(days=2)),deadline=str(today+timedelta(days=17)))
    terms['payment'].update(currency='USD',total_minor=2000)
    first=terms['timeline']['milestones'][0]
    first.update(amount_minor=1000,currency='USD',due_date=str(today+timedelta(days=6)))
    second={**first,'id':'M2','title':'Second milestone','deliverable_ids':['D2'],'due_date':str(today+timedelta(days=12))}
    terms['timeline']['milestones'].append(second)
    terms['scope']['deliverables'][0]['revision_rounds']=0
    terms['scope']['deliverables'].append({**terms['scope']['deliverables'][0],'id':'D2','milestone_id':'M2','name':'Second deliverable'})
    # Seed a fully specified unsigned fixture, as create() does; both people
    # still approve and sign it through the real endpoints below.
    with db.engine().begin() as conn:
        project=db.one(conn,db.projects,db.projects.c.id==pid)['payload']
        project['draft']=terms
        conn.execute(update(db.projects).where(db.projects.c.id==pid).values(payload=project))
    room=c.get(f'/api/client/projects/{pid}').json()
    room=call(c,'client',room,'invite',recipient='freelancer@example.com');room=call(f,'freelancer',room,'open')
    sign_both(c,f,room)
    email='seller@business.example.com';f.put('/api/freelancer/payout-recipient',json={'email':email,'sandbox_account_confirmed':True})
    monkeypatch.setenv('PAYPAL_CLIENT_ID','test');monkeypatch.setenv('PAYPAL_CLIENT_SECRET','test')
    transfers={};captures=[]
    async def order(row,pid):return {'id':'ORDER-'+row['id'],'links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=TEST'}]}
    async def authorize(row):return {'id':'AUTH-'+row['id'],'expiration_time':(datetime.now(timezone.utc)+timedelta(days=29)).isoformat()}
    async def capture(row):captures.append(row['milestone_id']);return {'id':'CAP-'+row['id']}
    async def verify(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(1000,'USD')}
    async def send(row):
        batch='BATCH-'+row['id'];transfers[batch]=row
        return {'batch_header':{'payout_batch_id':batch,'batch_status':'PENDING'}}
    async def status(batch):
        row=transfers[batch]
        return {'batch_header':{'payout_batch_id':batch,'sender_batch_header':{'sender_batch_id':row['id']}},'items':[{'payout_item_id':'ITEM-'+row['id'],'transaction_status':'SUCCESS','payout_item':{'sender_item_id':row['id'],'recipient_type':'EMAIL','receiver':email,'amount':{'currency':'USD','value':'10.00'}}}]}
    for name,fn in [('step1_create_order',order),('step1_authorize',authorize),('step1_capture',capture),('step1_get_capture',verify),('step1_create_payout',send),('step1_get_payout',status)]:monkeypatch.setattr(paypal,name,fn)
    base=f'/api/client/projects/{pid}/assurance'
    assert c.post(base+'/start',json={'milestone_id':'M2','recipient_email':email}).status_code==409
    for mid in ('M1','M2'):
        result=c.post(base+'/start',json={'milestone_id':mid,'recipient_email':email});assert result.status_code==200,result.text
        assert c.post(base+'/'+mid+'/authorize').status_code==200
        assert f.post(base.replace('/client/','/freelancer/')+'/deliver',json={'milestone_id':mid,'url':'https://example.com/delivery','note':'Completed the signed deliverable.'}).status_code==200
        from test_step1_assurance import automatic_release
        result=automatic_release(pid,mid)
        assert result['payout']['status']=='pending',result
        received=c.post(base+'/'+mid+'/payout-status').json()
        assert received['status']=='paid' and received['recipient_email']==email and received['amount_minor']==1000
    assert captures==['M1','M2'] and len(transfers)==2


@pytest.mark.parametrize('host',['api-m.sandbox.paypal.com','attacker.example'])
def test_duplicate_batch_follows_only_sandbox_resource(monkeypatch,host):
    real=httpx.AsyncClient;requests=[]
    def handle(request):
        requests.append(str(request.url))
        if request.url.path.endswith('/token'):return httpx.Response(200,json={'access_token':'private-token'})
        if request.method=='POST':return httpx.Response(400,json={'name':'DUPLICATE_BATCH_ID','links':[{'href':f'https://{host}/v1/payments/payouts/EXISTING'}]})
        return httpx.Response(200,json={'batch_header':{'payout_batch_id':'EXISTING'}})
    monkeypatch.setattr(paypal.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handle),**kw))
    if host=='attacker.example':
        with pytest.raises(HTTPException):asyncio.run(paypal.call('POST','/v1/payments/payouts',{},'ORIGINAL'))
        assert not any(host in url for url in requests)
    else:assert asyncio.run(paypal.call('POST','/v1/payments/payouts',{},'ORIGINAL'))['batch_header']['payout_batch_id']=='EXISTING'
