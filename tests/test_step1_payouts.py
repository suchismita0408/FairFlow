import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from sqlalchemy import insert,update
from app import db,paypal,payouts
from test_step1_auth_validation import portals
from test_step1_assurance import signed_short_project

def setup_payment(portals):
    client,freelancer,room=signed_short_project(portals)
    pid=room['project']['id'];milestone=room['agreement']['timeline']['milestones'][0]
    row=dict(id=db.uid(),project_id=pid,agreement_id=room['project']['active_agreement_id'],milestone_id=milestone['id'],
             amount_minor=milestone['amount_minor'],currency='USD',status='captured',created_at=db.now(),payload={'capture_id':'CAP-1'})
    with db.engine().begin() as conn:conn.execute(insert(db.milestone_payments).values(**row))
    return client,freelancer,pid,milestone['id'],row

def test_only_freelancer_can_set_sandbox_recipient(portals):
    c=portals('client');f=portals('freelancer');data={'email':'seller@business.example.com','sandbox_account_confirmed':True}
    assert c.put('/api/client/payout-recipient',json=data).status_code==403
    assert f.put('/api/freelancer/payout-recipient',json={**data,'sandbox_account_confirmed':False}).status_code==422
    assert f.put('/api/freelancer/payout-recipient',json={**data,'email':'bad'}).status_code==422
    assert f.put('/api/freelancer/payout-recipient',json=data).status_code==200
    assert f.get('/api/freelancer/payout-recipient').json()['email']==data['email']

def test_captured_milestone_payout_pending_then_recipient_success(portals,monkeypatch):
    c,f,pid,mid,pay=setup_payment(portals);root=f'/api/client/projects/{pid}/assurance/{mid}'
    consent={'recipient_email':'seller@business.example.com','sandbox_payout_confirmed':True}
    assert c.post(root+'/payout',json=consent).status_code==409
    assert f.put('/api/freelancer/payout-recipient',json={'email':consent['recipient_email'],'sandbox_account_confirmed':True}).status_code==200
    assert c.post(root+'/payout',json={**consent,'recipient_email':'stale@business.example.com'}).status_code==409
    assert f.post(root.replace('/client/','/freelancer/')+'/payout',json=consent).status_code==403
    outsider=portals('client','outsider@example.com')
    assert outsider.post(root+'/payout',json=consent).status_code==404
    with db.engine().begin() as conn:conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==pay['id']).values(status='authorized'))
    assert c.post(root+'/payout',json=consent).status_code==409
    with db.engine().begin() as conn:conn.execute(update(db.milestone_payments).where(db.milestone_payments.c.id==pay['id']).values(status='captured'))
    assert c.post(root+'/payout',json={**consent,'amount_minor':1}).status_code==422
    calls=[];intent={}
    async def capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(pay['amount_minor'],'USD')}
    async def send(row):
        calls.append(row['id']);intent.update(copy.deepcopy(row))
        return {'batch_header':{'payout_batch_id':'BATCH-1','batch_status':'PENDING'}}
    monkeypatch.setattr(paypal,'step1_get_capture',capture);monkeypatch.setattr(paypal,'step1_create_payout',send)
    result=c.post(root+'/payout',json=consent);assert result.status_code==200,result.text
    assert result.json()['status']=='pending' and result.json()['amount_minor']==pay['amount_minor']
    assert c.post(root+'/payout',json=consent).status_code==409
    assert len(calls)==1
    assert f.put('/api/freelancer/payout-recipient',json={'email':'new@business.example.com','sandbox_account_confirmed':True}).status_code==200
    item={'payout_item_id':'ITEM-1','transaction_status':'PENDING','payout_item':{'sender_item_id':intent['id'],'recipient_type':'EMAIL','receiver':consent['recipient_email'],'amount':{'currency':'USD','value':paypal.step1_amount(pay['amount_minor'],'USD')['value']}}}
    async def details(batch):return {'batch_header':{'payout_batch_id':batch,'batch_status':'SUCCESS','sender_batch_header':{'sender_batch_id':intent['id']}},'items':[item]}
    monkeypatch.setattr(paypal,'step1_get_payout',details)
    assert c.post(root+'/payout-status').json()['status']=='pending'
    item['transaction_status']='UNCLAIMED'
    assert c.post(root+'/payout-status').json()['status']=='unclaimed'
    item['transaction_status']='SUCCESS';item['transaction_id']='TRANSFER-1'
    received=f.post(root.replace('/client/','/freelancer/')+'/payout-status')
    assert received.status_code==200,received.text
    assert received.json()['status']=='paid'
    assert received.json()['recipient_email']==consent['recipient_email']
    assert received.json()['transaction_id']=='TRANSFER-1'
    entries=c.get(f'/api/client/projects/{pid}/assurance/ledger').json()['entries']
    assert len(entries)==4 and sum(e['amount_cents'] for e in entries)==0
    assert sum(e['amount_cents'] for e in entries if e['account']=='project_liability')==0
    assert c.post(root+'/payout-status').status_code==200
    assert len(c.get(f'/api/client/projects/{pid}/assurance/ledger').json()['entries'])==4
    item['payout_item']['receiver']='wrong@business.example.com'
    assert c.post(root+'/payout-status').status_code==502

def test_uncertain_payout_never_retries_or_reports_paid(portals,monkeypatch):
    c,f,pid,mid,pay=setup_payment(portals);root=f'/api/client/projects/{pid}/assurance/{mid}'
    email='seller@business.example.com'
    f.put('/api/freelancer/payout-recipient',json={'email':email,'sandbox_account_confirmed':True})
    async def capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(pay['amount_minor'],'USD')}
    calls=[]
    async def uncertain(row):calls.append(row['id']);raise HTTPException(502,'Provider response uncertain')
    monkeypatch.setattr(paypal,'step1_get_capture',capture);monkeypatch.setattr(paypal,'step1_create_payout',uncertain)
    data={'recipient_email':email,'sandbox_payout_confirmed':True}
    assert c.post(root+'/payout',json=data).status_code==502
    assert c.post(root+'/payout',json=data).status_code==409
    assert len(calls)==1
    result=c.get(f'/api/client/projects/{pid}/assurance').json()['payments'][0]['payout']
    assert result['status']=='attention'

def test_payout_adapter_uses_frozen_amount_recipient_and_request_key(monkeypatch):
    calls=[]
    async def call(*args):calls.append(args);return {'batch_header':{'payout_batch_id':'BATCH'}}
    monkeypatch.setattr(paypal,'call',call)
    row={'id':'INTENT','payment_id':'PAYMENT','amount_minor':1000,'currency':'USD','recipient_email':'seller@business.example.com'}
    asyncio.run(paypal.step1_create_payout(row))
    method,path,body,key=calls[0]
    assert method=='POST' and path=='/v1/payments/payouts' and key=='INTENT'
    assert body['sender_batch_header']['sender_batch_id']=='INTENT'
    assert body['items'][0]['amount']=={'currency':'USD','value':'10.00'}
    assert body['items'][0]['receiver']==row['recipient_email']

def test_refunded_capture_cannot_be_paid_out(portals,monkeypatch):
    c,f,pid,mid,pay=setup_payment(portals);email='seller@business.example.com'
    f.put('/api/freelancer/payout-recipient',json={'email':email,'sandbox_account_confirmed':True})
    async def capture(cid):return {'id':cid,'status':'REFUNDED','amount':paypal.step1_amount(pay['amount_minor'],'USD')}
    async def forbidden(row):raise AssertionError('Must not send a payout for refunded capture')
    monkeypatch.setattr(paypal,'step1_get_capture',capture);monkeypatch.setattr(paypal,'step1_create_payout',forbidden)
    result=c.post(f'/api/client/projects/{pid}/assurance/{mid}/payout',json={'recipient_email':email,'sandbox_payout_confirmed':True})
    assert result.status_code==502

def test_simultaneous_payout_requests_send_only_once(portals,monkeypatch):
    c,f,pid,mid,pay=setup_payment(portals);email='seller@business.example.com'
    f.put('/api/freelancer/payout-recipient',json={'email':email,'sandbox_account_confirmed':True})
    calls=[]
    async def capture(cid):return {'id':cid,'status':'COMPLETED','amount':paypal.step1_amount(pay['amount_minor'],'USD')}
    async def send(row):
        calls.append(row['id']);await asyncio.sleep(.05)
        return {'batch_header':{'payout_batch_id':'BATCH','batch_status':'PENDING'}}
    monkeypatch.setattr(paypal,'step1_get_capture',capture);monkeypatch.setattr(paypal,'step1_create_payout',send)
    def request():return c.post(f'/api/client/projects/{pid}/assurance/{mid}/payout',json={'recipient_email':email,'sandbox_payout_confirmed':True}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:request(),range(2)))
    assert sorted(results)==[200,409]
    assert len(calls)==1
