import copy
import asyncio
from datetime import date,timedelta,datetime,timezone
import pytest
from fastapi import HTTPException
from app import paypal
from test_step1_auth_validation import portals
from test_step1_negotiation import create,call,approve_both,endpoint


def automatic_release(pid,mid):
    """Exercise scheduled release at the saved review deadline, without sleeping."""
    from app import db,assurance
    with db.engine().connect() as conn:
        row=assurance.payment(conn,pid,mid)
        member=db.one(conn,db.members,(db.members.c.project_id==pid)&(db.members.c.role=='client'))
        user=db.one(conn,db.users,db.users.c.id==member['user_id'])
    return asyncio.run(assurance.release_payment(pid,mid,user,automatic=True,now=datetime.fromisoformat(row['payload']['review_deadline'])))


def test_retired_or_different_merchant_cannot_reuse_payment(monkeypatch):
    import hashlib
    from app import assurance
    monkeypatch.setenv('PAYPAL_CLIENT_ID','current-us-app')
    row={'agreement_id':'SIGNED','payload':{'merchant_retired':True}}
    with pytest.raises(HTTPException):assurance.bound(row,{'id':'SIGNED'})
    row['payload']={'merchant_fingerprint':hashlib.sha256(b'old-india-app').hexdigest()}
    with pytest.raises(HTTPException):assurance.bound(row,{'id':'SIGNED'})
    row['payload']={'merchant_fingerprint':hashlib.sha256(b'current-us-app').hexdigest()}
    assert assurance.bound(row,{'id':'SIGNED'})==row

def signed_short_project(portals,auto=False):
    c=portals('client');f=portals('freelancer');r=create(c)
    a=copy.deepcopy(r['agreement']);today=date.today()
    a['timeline']['start_date']=str(today+timedelta(days=2))
    a['timeline']['milestones'][0]['due_date']=str(today+timedelta(days=10))
    a['timeline']['deadline']=str(today+timedelta(days=24))
    if auto:a['timeline']['milestones'][0].update(auto_accept=True,auto_accept_days=3)
    a['payment']['currency']='USD';a['timeline']['milestones'][0]['currency']='USD'
    save=c.put(endpoint(c,'client',r['project']['id'])+'/draft',json={'revision':r['revision'],'agreement':a})
    assert save.status_code==200,save.text;r=save.json()
    r=call(c,'client',r,'invite',recipient='freelancer@example.com')
    r=call(f,'freelancer',r,'open')
    r,sha=approve_both(c,f,r)
    r=call(c,'client',r,'sign',version_hash=sha,typed_name='Alice Baker')
    r=call(f,'freelancer',r,'sign',version_hash=sha,typed_name='Rahul Kumar')
    return c,f,r

def test_signed_milestone_sandbox_checkout_delivery_and_capture(portals,monkeypatch):
    monkeypatch.setenv('PAYPAL_CLIENT_ID','test-id');monkeypatch.setenv('PAYPAL_CLIENT_SECRET','test-secret')
    c,f,r=signed_short_project(portals);pid=r['project']['id'];m=r['agreement']['timeline']['milestones'][0]
    root='/api/client/projects/'+pid+'/assurance';freelancer_root='/api/freelancer/projects/'+pid+'/assurance'
    outsider=portals('client','outsider@example.com')
    outside=outsider.get(root);assert outside.status_code==404,outside.text
    assert f.post(freelancer_root+'/start',json={'milestone_id':m['id']}).status_code==403
    calls=[]
    async def order(payment,project_id):
        calls.append(('order',payment['amount_minor'],payment['currency']))
        return {'id':'ORDER-1','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=ORDER-1'}]}
    async def authorize(payment):
        calls.append(('authorize',payment['payload']['order_id']))
        return {'id':'AUTH-1','status':'CREATED','amount':paypal.step1_amount(payment['amount_minor'],payment['currency']),'expiration_time':(date.today()+timedelta(days=29)).isoformat()+'T00:00:00Z'}
    async def capture(payment):
        calls.append(('capture',payment['payload']['authorization_id'],payment['amount_minor']))
        return {'id':'CAPTURE-1','status':'COMPLETED','amount':paypal.step1_amount(payment['amount_minor'],payment['currency'])}
    monkeypatch.setattr(paypal,'step1_create_order',order);monkeypatch.setattr(paypal,'step1_authorize',authorize);monkeypatch.setattr(paypal,'step1_capture',capture)
    assert c.post(root+'/start',json={'milestone_id':m['id']}).status_code==409
    assert f.put('/api/freelancer/payout-recipient',json={'email':'seller@business.example.com','sandbox_account_confirmed':True}).status_code==200
    started=c.post(root+'/start',json={'milestone_id':m['id'],'recipient_email':'seller@business.example.com'});assert started.status_code==200,started.text
    assert started.json()['status']=='awaiting_approval'
    assert started.json()['amount_minor']==m['amount_minor']
    assert c.post(root+'/start',json={'milestone_id':m['id']}).status_code==409
    assert f.post(freelancer_root+'/'+m['id']+'/authorize').status_code==403
    authorized=c.post(root+'/'+m['id']+'/authorize');assert authorized.status_code==200,authorized.text
    assert authorized.json()['status']=='authorized'
    assert c.post(root+'/'+m['id']+'/approve',json={'recipient_email':'seller@business.example.com','sandbox_payout_confirmed':True}).status_code==409
    assert c.post(root+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'The finished deliverable is linked here.'}).status_code==403
    delivered=f.post(freelancer_root+'/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'The finished deliverable is linked here.'})
    assert delivered.status_code==200,delivered.text
    assert delivered.json()['status']=='in_review'
    assert f.put('/api/freelancer/payout-recipient',json={'email':'seller@business.example.com','sandbox_account_confirmed':True}).status_code==200
    async def verified_capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    async def payout(row):
        calls.append(('payout',row['recipient_email'],row['amount_minor']))
        return {'batch_header':{'payout_batch_id':'BATCH-1','batch_status':'PENDING'}}
    monkeypatch.setattr(paypal,'step1_get_capture',verified_capture);monkeypatch.setattr(paypal,'step1_create_payout',payout)
    paid=automatic_release(pid,m['id'])
    assert paid['status']=='captured'
    assert paid['payout']['status']=='pending'
    assert c.post(root+'/'+m['id']+'/approve',json={'recipient_email':'seller@business.example.com','sandbox_payout_confirmed':True}).status_code==409
    assert calls==[('order',m['amount_minor'],'USD'),('authorize','ORDER-1'),('capture','AUTH-1',m['amount_minor']),('payout','seller@business.example.com',m['amount_minor'])]

def test_provider_failure_pauses_instead_of_reporting_capture(portals,monkeypatch):
    monkeypatch.setenv('PAYPAL_CLIENT_ID','test-id');monkeypatch.setenv('PAYPAL_CLIENT_SECRET','test-secret')
    c,f,r=signed_short_project(portals);pid=r['project']['id'];m=r['agreement']['timeline']['milestones'][0];root='/api/client/projects/'+pid+'/assurance'
    async def order(*args):return {'id':'ORDER-1','links':[{'rel':'approve','href':'https://www.sandbox.paypal.com/checkoutnow?token=ORDER-1'}]}
    async def authorize(payment):return {'id':'AUTH-1','expiration_time':(date.today()+timedelta(days=29)).isoformat()+'T00:00:00Z'}
    assert f.put('/api/freelancer/payout-recipient',json={'email':'seller@business.example.com','sandbox_account_confirmed':True}).status_code==200
    async def capture(*args):raise HTTPException(502,'Provider response uncertain')
    monkeypatch.setattr(paypal,'step1_create_order',order);monkeypatch.setattr(paypal,'step1_authorize',authorize);monkeypatch.setattr(paypal,'step1_capture',capture)
    assert c.post(root+'/start',json={'milestone_id':m['id'],'recipient_email':'seller@business.example.com'}).status_code==200
    assert c.post(root+'/'+m['id']+'/authorize').status_code==200
    assert f.post('/api/freelancer/projects/'+pid+'/assurance/deliver',json={'milestone_id':m['id'],'url':'https://example.com/result','note':'The completed work is available here.'}).status_code==200
    with pytest.raises(HTTPException) as error:automatic_release(pid,m['id'])
    assert error.value.status_code==502
    record=c.get(root).json()['payments'][0]
    assert record['status']=='attention' and record['capture_id'] is None
    assert c.post(root+'/'+m['id']+'/approve',json={'recipient_email':'seller@business.example.com','sandbox_payout_confirmed':True}).status_code==409
    async def confirmed_order(*args):
        return {'purchase_units':[{'custom_id':record['id'],'payments':{'captures':[{'id':'RECOVERED-CAP','status':'COMPLETED','amount':paypal.step1_amount(record['amount_minor'],record['currency'])}]}}]}
    async def recovered_capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    async def recovered_payout(row):return {'batch_header':{'payout_batch_id':'RECOVERED-BATCH','batch_status':'PENDING'}}
    monkeypatch.setattr(paypal,'step1_get_capture',recovered_capture);monkeypatch.setattr(paypal,'step1_create_payout',recovered_payout)
    monkeypatch.setattr(paypal,'call',confirmed_order)
    recovered=c.post(root+'/'+m['id']+'/reconcile')
    assert recovered.status_code==200,recovered.text
    assert recovered.json()['local']['status']=='captured'
    assert recovered.json()['local']['capture_id']=='RECOVERED-CAP'
    assert recovered.json()['payout']['status']=='pending'

def test_minor_units_for_zero_and_two_decimal_currencies():
    assert paypal.step1_amount(60000,'INR')=={'currency_code':'INR','value':'600.00'}
    assert paypal.step1_amount(600,'JPY')=={'currency_code':'JPY','value':'600'}

def test_paypal_adapter_checks_buyer_approval_and_exact_amount(monkeypatch):
    payment={'id':'PAY-1','milestone_id':'M1','amount_minor':60000,'currency':'INR','payload':{'order_id':'ORDER-1','order_key':'ORDER-KEY','authorize_key':'AUTH-KEY','authorization_id':'AUTH-1','capture_key':'CAP-KEY'}}
    requests=[]
    async def fake(method,path,body=None,key=None):
        requests.append((method,path,body,key))
        if method=='GET':return {'status':'APPROVED','purchase_units':[{'custom_id':'PAY-1','amount':{'currency_code':'INR','value':'600.00','breakdown':{}}}]}
        if path.endswith('/authorize'):return {'purchase_units':[{'payments':{'authorizations':[{'id':'AUTH-1','status':'CREATED','amount':{'currency_code':'INR','value':'600.00'}}]}}]}
        return {'id':'CAP-1','status':'COMPLETED','amount':{'currency_code':'INR','value':'600.00'}}
    monkeypatch.setattr(paypal,'call',fake)
    assert asyncio.run(paypal.step1_authorize(payment))['id']=='AUTH-1'
    assert asyncio.run(paypal.step1_capture(payment))['id']=='CAP-1'
    assert requests[1][3]=='AUTH-KEY' and requests[2][3]=='CAP-KEY'
    assert requests[2][2]['amount']=={'currency_code':'INR','value':'600.00'}
    async def wrong_order(*args):return {'status':'APPROVED','purchase_units':[{'custom_id':'PAY-1','amount':{'currency_code':'INR','value':'10000.00'}}]}
    monkeypatch.setattr(paypal,'call',wrong_order)
    with pytest.raises(HTTPException):asyncio.run(paypal.step1_authorize(payment))
    async def unapproved(*args):return {'status':'PAYER_ACTION_REQUIRED','purchase_units':[{'custom_id':'PAY-1','amount':{'currency_code':'INR','value':'600.00'}}]}
    monkeypatch.setattr(paypal,'call',unapproved)
    with pytest.raises(HTTPException) as exc:asyncio.run(paypal.step1_authorize(payment))
    assert exc.value.status_code==409


def test_late_delivery_preserves_full_review_window(portals):
    from sqlalchemy import insert
    from app import db
    c,f,r=signed_short_project(portals);pid=r['project']['id'];m=r['agreement']['timeline']['milestones'][0]
    with db.engine().begin() as conn:
        conn.execute(insert(db.milestone_payments).values(id=db.uid(),project_id=pid,agreement_id=r['project']['active_agreement_id'],milestone_id=m['id'],amount_minor=m['amount_minor'],currency='USD',status='authorized',created_at=db.now(),payload={'authorization_id':'AUTH-LATE','expires_at':str(date.today()+timedelta(days=2))+'T00:00:00Z'}))
    response=f.post(f'/api/freelancer/projects/{pid}/assurance/deliver',json={'milestone_id':m['id'],'url':'https://example.com/work','note':'Finished work is available here.'})
    assert response.status_code==409 and 'too late' in response.text
    assert c.get(f'/api/client/projects/{pid}/assurance').json()['payments'][0]['status']=='authorized'


def test_approval_payout_failure_preserves_capture_and_blocks_recapture(portals,monkeypatch):
    from sqlalchemy import insert
    from app import db
    c,f,r=signed_short_project(portals);pid=r['project']['id'];m=r['agreement']['timeline']['milestones'][0]
    f.put('/api/freelancer/payout-recipient',json={'email':'seller@business.example.com','sandbox_account_confirmed':True})
    with db.engine().begin() as conn:
        conn.execute(insert(db.milestone_payments).values(id=db.uid(),project_id=pid,agreement_id=r['project']['active_agreement_id'],milestone_id=m['id'],amount_minor=m['amount_minor'],currency='USD',status='in_review',created_at=db.now(),payload={'authorization_id':'AUTH-READY','delivery_url':'https://example.com/work','expires_at':str(date.today()+timedelta(days=20))+'T00:00:00Z','auto_release':True,'review_deadline':db.now()}))
    calls=[]
    async def capture(row):
        calls.append('capture');return {'id':'CAP-DONE'}
    async def verify(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(m['amount_minor'],'USD')}
    async def payout(row):
        calls.append('payout');raise HTTPException(502,'Payout uncertain')
    monkeypatch.setattr(paypal,'step1_capture',capture);monkeypatch.setattr(paypal,'step1_get_capture',verify);monkeypatch.setattr(paypal,'step1_create_payout',payout)
    consent={'recipient_email':'seller@business.example.com','sandbox_payout_confirmed':True}
    root=f'/api/client/projects/{pid}/assurance'
    assert c.post(root+'/'+m['id']+'/approve',json={**consent,'recipient_email':'other@example.com'}).status_code==409
    assert calls==[]
    result=automatic_release(pid,m['id'])
    assert result['status']=='captured'
    assert 'payout_error' in result
    assert c.post(root+'/'+m['id']+'/approve',json=consent).status_code==409
    assert calls==['capture','payout']
    assert c.get(root).json()['payments'][0]['payout']['status']=='attention'


@pytest.mark.parametrize('invalid',['review','window','price'])
def test_invalid_payment_terms_cannot_be_invited(portals,invalid):
    c=portals('client');r=create(c);terms=copy.deepcopy(r['agreement'])
    if invalid=='review':terms['timeline']['milestones'][0]['review_days']=2
    elif invalid=='price':terms['payment']['total_minor']+=1
    else:
        terms['timeline']['milestones'][0]['due_date']=str(date.today()+timedelta(days=50))
        terms['timeline']['deadline']=str(date.today()+timedelta(days=60))
    root=endpoint(c,'client',r['project']['id'])
    saved=c.put(root+'/draft',json={'revision':r['revision'],'agreement':terms})
    assert saved.status_code==200
    response=c.post(root+'/actions',json={'action':'invite','revision':saved.json()['revision'],'recipient':'freelancer@example.com'})
    assert response.status_code==422,response.text
