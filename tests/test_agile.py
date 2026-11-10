import os
os.environ['FAIRFLOW_MODE']='demo'
os.environ['AI_MODE']='demo'
import asyncio
import copy
from datetime import timedelta
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
import main
from app import store, agile, paypal

CONTRACT={'title':'Agile website project','task_description':'Deliver the agreed static website in three phases. No backend.', 'price_cents':90000,'allowed_revisions':1,'deliverables':['Static website'],'exclusions':['Database backend'],'hourly_rate_cents':5000}
PLAN=[{'title':f'Phase {i+1}', 'scope':f'Accepted phase {i+1} of the static website with a reviewed deliverable.', 'amount_cents':30000,'duration_days':21} for i in range(3)]

@pytest.fixture
def c(tmp_path,monkeypatch):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'agile.db'))
    monkeypatch.setenv('AI_MODE','demo')
    monkeypatch.setattr(main,'MODE','demo')
    with TestClient(main.app) as client: yield client

def post(c,path,body=None,status=200):
    r=c.post('/api/agile'+path,json=body or {})
    assert r.status_code==status,r.text
    return r.json()

def create(c, vault=True, fund=True):
    p=post(c,'/projects',{'contract':CONTRACT,'client_name':'Alex','freelancer_name':'Jordan','milestones':PLAN},201)
    root='/projects/'+p['id']
    for role in ['client','freelancer']:
        p=post(c,root+'/accept',{'role':role,'plan_hash':p['plan_hash']})
    if vault:
        post(c,root+'/vault/setup',{'agreed':True,'plan_hash':p['plan_hash']})
        p=post(c,root+'/vault/confirm')
    if fund:p=post(c,root+'/milestones/'+p['milestones'][0]['id']+'/fund')
    return p

def root(p):return '/projects/'+p['id']
def mr(p,index=0):return root(p)+'/milestones/'+p['milestones'][index]['id']
def delivery(c,p,index=0):return post(c,mr(p,index)+'/deliver',{'url':'https://example.com/delivery','notes':'Agreed deliverable ready'})
def progress(c,p,accept=True):
    p=post(c,mr(p)+'/progress',{'percent':80,'evidence_url':'https://example.com/progress','completed_work':'Layout and responsive components are delivered.','remaining_work':'Complete the agreed accessibility review and handoff.'})
    if accept:p=post(c,mr(p)+'/progress/accept',{'progress_id':p['milestones'][0]['progress']['id'],'agreed':True})
    return p

def test_plan_constraints(c):
    data={'contract':CONTRACT,'client_name':'A','freelancer_name':'B','milestones':copy.deepcopy(PLAN)}
    data['milestones'][0]['duration_days']=22
    post(c,'/projects',data,422)
    data['milestones'][0]['duration_days']=13
    post(c,'/projects',data,422)
    data['milestones'][0]['duration_days']=14
    data['milestones'][0]['amount_cents']=1
    post(c,'/projects',data,422)

def test_draft_sums_exact_cents(c):
    p=post(c,'/draft',{'chat_history':'Build a static site for $800.01 with 2 revisions.','milestone_count':3})
    assert sum(m['amount_cents'] for m in p['milestones'])==80001
    assert all(14<=m['duration_days']<=21 for m in p['milestones'])

def test_continuous_consent_and_hidden_vault(c):
    p=create(c,fund=False)
    assert 'token_id' not in p['vault']
    assert store.get(p['id'])['vault']['token_id'].startswith('DEMO-')
    r=c.get('/api/projects/'+p['id']).json()
    assert 'token_id' not in r['vault']
    assert 'token_id' not in c.get('/api/projects').json()[0]['vault']
    post(c,root(p)+'/accept',{'role':'client','plan_hash':'0'*64},409)
    p=post(c,mr(p)+'/fund')
    assert p['milestones'][0]['work_allowed']
    assert len(p['payments'])==1
    assert len(post(c,mr(p)+'/fund')['payments'])==1

def test_five_day_review_auto_funds_next(c):
    p=create(c);p=delivery(c,p)
    assert (agile.parse(p['milestones'][0]['review_deadline'])-agile.parse(p['server_time'])).total_seconds()>431990
    p=post(c,root(p)+'/demo/advance',{'days':4})
    assert p['milestones'][0]['status']=='in_review'
    p=post(c,root(p)+'/demo/advance',{'days':1})
    assert p['milestones'][0]['status']=='completed'
    assert p['milestones'][1]['status']=='active'
    assert p['milestones'][1]['work_allowed']
    assert len(p['payments'][0]['captures'])==1
    post(c,mr(p)+'/approve')
    assert len(store.get(p['id'])['payments'][0]['captures'])==1

def test_three_milestone_lifecycle(c):
    p=create(c)
    for i in range(3):
        p=delivery(c,p,i)
        p=post(c,mr(p,i)+'/approve')
    assert p['status']=='completed'
    assert sum(cap['amount_cents'] for pay in p['payments'] for cap in pay['captures'])==90000

def test_day_28_approved_partial_capture_rollover(c):
    p=progress(c,create(c))
    p=post(c,root(p)+'/demo/advance',{'days':28})
    assert p['milestones'][0]['status']=='rolled_over'
    assert p['payments'][0]['status']=='voided'
    assert p['payments'][0]['captures'][0]['amount_cents']==24000
    assert p['payments'][0]['operations'][0]['final_capture'] is False
    assert p['milestones'][1]['amount_cents']==36000
    assert p['milestones'][1]['status']=='active'
    assert p['payments'][1]['amount_cents']==36000
    assert 'accessibility' in p['milestones'][1]['scope']
    assert sum(x['amount_cents'] for x in p['milestones'] if x['status']!='rolled_over')+24000==90000
    post(c,mr(p)+'/partial-settle')
    assert len(store.get(p['id'])['payments'][0]['captures'])==1

def test_unapproved_progress_never_bills(c):
    p=progress(c,create(c),False)
    post(c,mr(p)+'/partial-settle',status=409)
    p=post(c,root(p)+'/demo/advance',{'days':28})
    assert p['payments'][0]['status']=='voided'
    assert not p['payments'][0]['captures']
    assert not p['milestones'][0]['work_allowed']

def test_late_delivery_does_not_shorten_review(c):
    p=create(c)
    p=post(c,root(p)+'/demo/advance',{'days':24})
    post(c,mr(p)+'/deliver',{'url':'https://example.com/work'},409)
    assert not store.get(p['id'])['payments'][0]['captures']

def test_emergency_pause_clean_void_and_fresh_hold(c):
    p=create(c)
    old_id=p['payments'][0]['authorization_id']
    p=post(c,root(p)+'/pause',{'request':'Medical emergency requires a pause.'})
    assert p['status']=='paused_emergency'
    assert p['payments'][0]['status']=='voided'
    assert not p['milestones'][0]['work_allowed']
    post(c,mr(p)+'/deliver',{'url':'https://example.com/work'},409)
    p=post(c,root(p)+'/demo/advance',{'days':35})
    assert not p['payments'][0]['captures']
    p=post(c,root(p)+'/resume')
    assert p['payments'][-1]['authorization_id']!=old_id
    assert p['milestones'][0]['status']=='active'

def test_red_light_on_decline_and_billing_recovery(c):
    p=create(c)
    post(c,root(p)+'/demo/billing',{'fail_next':True})
    p=delivery(c,p)
    p=post(c,mr(p)+'/approve')
    assert p['milestones'][1]['status']=='billing_failed'
    assert p['milestones'][1]['funding_signal']=='red'
    assert p['status']=='billing_paused'
    post(c,mr(p,1)+'/deliver',{'url':'https://example.com/work'},409)
    post(c,root(p)+'/demo/billing',{'fail_next':False})
    p=post(c,mr(p,1)+'/fund')
    assert p['milestones'][1]['work_allowed']
    assert p['payments'][-2]['status']=='failed'

def test_saved_method_revocation_blocks_auto_renew(c):
    p=create(c)
    post(c,root(p)+'/vault/revoke')
    p=delivery(c,p)
    p=post(c,mr(p)+'/approve')
    assert p['milestones'][1]['status']=='queued'
    assert len(p['payments'])==1
    p=post(c,mr(p,1)+'/fund')
    assert p['milestones'][1]['status']=='awaiting_payment'

def test_manual_checkout_without_vault(c):
    p=create(c,vault=False)
    assert p['milestones'][0]['status']=='awaiting_payment'
    p=post(c,mr(p)+'/confirm-payment')
    assert p['milestones'][0]['status']=='active'

def test_revision_limit_and_addition_consent(c):
    p=create(c)
    p=post(c,mr(p)+'/changes',{'request':'Change the font color to green'})
    assert p['milestones'][0]['revisions_used']==1
    p=post(c,mr(p)+'/changes',{'request':'Change the heading color again'})
    addition=p['milestones'][-1]
    assert addition['status']=='proposed'
    assert addition['amount_cents']==5000
    assert len(p['payments'])==1
    for role in ['client','freelancer']:
        p=post(c,root(p)+'/milestones/'+addition['id']+'/accept-addition',{'role':role,'plan_hash':p['plan_hash']})
    assert p['milestones'][-1]['accepted']
    assert len(p['payments'])==1 # Still queued behind funded baseline work.

def test_dispute_blocks_full_and_partial(c):
    p=progress(c,create(c))
    p=post(c,mr(p)+'/dispute',{'request':'Deliverables do not match acceptance criteria.'})
    post(c,mr(p)+'/partial-settle',status=409)
    post(c,mr(p)+'/approve',status=409)
    p=post(c,root(p)+'/demo/advance',{'days':28})
    assert not p['payments'][0]['captures']
    p=post(c,root(p)+'/demo/advance',{'days':1})
    assert p['milestones'][0]['status']=='disputed'
    assert p['payments'][0]['status']=='expired'
    post(c,mr(p)+'/fund',status=409)

def test_expired_authorization_never_captures(c):
    p=delivery(c,create(c))
    p=post(c,root(p)+'/demo/advance',{'days':30})
    assert p['milestones'][0]['status']=='authorization_expired'
    assert not p['payments'][0]['captures']

def test_progress_replacement_requires_new_consent(c):
    p=progress(c,create(c))
    old=p['milestones'][0]['progress']['id']
    p=progress(c,p,False)
    assert not p['milestones'][0]['progress']['accepted_at']
    post(c,mr(p)+'/progress/accept',{'progress_id':old,'agreed':True},409)

def test_capture_failure_persists_retry_journal(c,monkeypatch):
    p=delivery(c,create(c));raw=store.get(p['id']);raw['mode']='sandbox';store.save(raw)
    calls=[]
    async def capture_fail(pay,op):calls.append(op['key']);raise HTTPException(502,'Uncertain capture response')
    monkeypatch.setattr(paypal,'milestone_capture',capture_fail)
    with pytest.raises(HTTPException):asyncio.run(agile.finish(raw,raw['milestones'][0]))
    saved=store.get(p['id'])
    assert saved['milestones'][0]['status']=='payment_attention'
    assert not saved['payments'][0]['captures']
    saved['milestones'][0]['status']='in_review'
    async def success(pay,op):calls.append(op['key']);return 'CONFIRMED-CAPTURE'
    monkeypatch.setattr(paypal,'milestone_capture',success)
    saved['vault']['status']='revoked' # Avoid another external hold in this unit test.
    asyncio.run(agile.finish(saved,saved['milestones'][0]))
    assert calls[0]==calls[1]
    assert len(store.get(p['id'])['payments'][0]['captures'])==1

def test_void_failure_after_partial_does_not_capture_twice(c,monkeypatch):
    p=progress(c,create(c));raw=store.get(p['id']);raw['mode']='sandbox';raw['vault']['status']='revoked'
    capture_calls=[];void_calls=[]
    async def cap(pay,op):capture_calls.append(op['key']);return 'PARTIAL-ID'
    async def fail_void(pay,op):void_calls.append(op['key']);raise HTTPException(502,'Void unavailable')
    monkeypatch.setattr(paypal,'milestone_capture',cap);monkeypatch.setattr(paypal,'milestone_void',fail_void)
    with pytest.raises(HTTPException):asyncio.run(agile.settle_partial(raw,raw['milestones'][0]))
    saved=store.get(p['id']);assert saved['payments'][0]['status']=='partially_captured'
    saved['milestones'][0]['status']='settling_partial'
    async def ok_void(pay,op):void_calls.append(op['key'])
    monkeypatch.setattr(paypal,'milestone_void',ok_void)
    asyncio.run(agile.settle_partial(saved,saved['milestones'][0]))
    assert len(capture_calls)==1 and void_calls[0]==void_calls[1]
    assert store.get(p['id'])['milestones'][1]['amount_cents']==36000

def test_legacy_routes_cannot_mutate_agile(c):
    p=create(c)
    assert c.post('/api/projects/'+p['id']+'/approve').status_code==409
    assert c.post('/api/projects/'+p['id']+'/deliver',json={'url':'https://example.com'}).status_code==409

def test_sandbox_cannot_time_travel(c,monkeypatch):
    p=create(c)
    monkeypatch.setattr(main,'MODE','sandbox');monkeypatch.setenv('FAIRFLOW_ADMIN_TOKEN','operator-token')
    r=c.post('/api/agile'+root(p)+'/demo/advance',json={'days':28},headers={'x-admin-token':'operator-token'})
    assert r.status_code==403

def test_restart_after_confirmed_capture_finishes_local_transition(c):
    p=delivery(c,create(c));raw=store.get(p['id']);m=raw['milestones'][0]
    m['settlement']='full'
    asyncio.run(agile.capture(raw,m,m['amount_cents'],'full_capture',True))
    assert store.get(p['id'])['milestones'][0]['status']=='in_review'
    asyncio.run(agile.scan(store.get(p['id'])))
    saved=store.get(p['id'])
    assert saved['milestones'][0]['status']=='completed'
    assert saved['milestones'][1]['status']=='active'
    assert len(saved['payments'][0]['captures'])==1

def test_restart_after_partial_and_void_merges_once(c):
    p=progress(c,create(c));raw=store.get(p['id']);m=raw['milestones'][0]
    m.update(status='settling_partial',settlement='partial')
    asyncio.run(agile.capture(raw,m,24000,'partial_capture',False))
    asyncio.run(agile.void(raw,m,'rollover_void'))
    asyncio.run(agile.scan(store.get(p['id'])))
    asyncio.run(agile.scan(store.get(p['id'])))
    saved=store.get(p['id'])
    assert saved['milestones'][0]['status']=='rolled_over'
    assert saved['milestones'][1]['amount_cents']==36000
    assert len(saved['milestones'][1]['carryovers'])==1

def test_emergency_pause_preserves_dispute(c):
    p=create(c)
    post(c,mr(p)+'/dispute',{'request':'Delivery criteria are disputed.'})
    post(c,root(p)+'/pause',{'request':'Emergency pause while resolving dispute.'})
    p=post(c,root(p)+'/resume')
    assert p['milestones'][0]['status']=='disputed'
    post(c,mr(p)+'/fund',status=409)

def test_last_milestone_remainder_becomes_new_milestone(c):
    p=create(c)
    for i in range(2):p=delivery(c,p,i);p=post(c,mr(p,i)+'/approve')
    path=mr(p,2)
    p=post(c,path+'/progress',{'percent':80,'evidence_url':'https://example.com/progress','completed_work':'Most handoff requirements have been delivered.','remaining_work':'Final documentation and handoff remain.'})
    post(c,path+'/progress/accept',{'progress_id':p['milestones'][2]['progress']['id'],'agreed':True})
    p=post(c,path+'/partial-settle')
    assert len(p['milestones'])==4
    assert p['milestones'][3]['amount_cents']==6000
    assert p['milestones'][3]['duration_days']==14
    assert p['milestones'][3]['status']=='active'
