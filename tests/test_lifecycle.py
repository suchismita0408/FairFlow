import os
os.environ['FAIRFLOW_MODE'] = 'demo'
os.environ['AI_MODE'] = 'demo'
import asyncio
from datetime import timedelta
import pytest
from fastapi.testclient import TestClient
import main
from app import store

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'test.db'))
    monkeypatch.setenv('AI_MODE', 'demo')
    monkeypatch.setattr(main, 'MODE', 'demo')
    with TestClient(main.app) as c: yield c

CONTRACT={'title':'Coffee landing page','task_description':'Build a static responsive landing page. No backend or database.', 'price_cents':80000,'allowed_revisions':1,'deliverables':['HTML and CSS landing page'],'exclusions':['Backend and database'],'hourly_rate_cents':5000}

def create(client):
    r=client.post('/api/projects',json={'contract':CONTRACT,'client_name':'Alex','freelancer_name':'Jordan'})
    assert r.status_code==201
    return r.json()['id']

def funded(client):
    pid=create(client)
    for role in ['client','freelancer']:
        assert client.post(f'/api/projects/{pid}/accept',json={'role':role}).status_code==200
    payment=client.post(f'/api/projects/{pid}/checkout').json()
    assert client.post(f"/api/payments/{payment['id']}/authorize").status_code==200
    return pid

def deliver(client,pid):
    r=client.post(f'/api/projects/{pid}/deliver',json={'url':'https://example.com/work','notes':'Ready'})
    assert r.status_code==200
    return r.json()

def test_full_lifecycle_and_idempotent_payment(client):
    pid=funded(client)
    p=deliver(client,pid)
    assert p['status']=='in_review'
    assert 71.9 < (main.datetime.fromisoformat(p['review_deadline'])-main.now()).total_seconds()/3600 <=72
    r=client.post(f'/api/projects/{pid}/approve')
    assert r.json()['status']=='completed'
    pay=r.json()['payments'][0]
    assert pay['status']=='captured'
    assert client.post(f"/api/payments/{pay['id']}/authorize").json()['payments'][0]['capture_id']==pay['capture_id']
    assert client.post(f'/api/projects/{pid}/approve').status_code==409

def test_checkout_requires_both_parties(client):
    pid=create(client)
    assert client.post(f'/api/projects/{pid}/checkout').status_code==409
    for role in ['client','freelancer']:
        client.post(f'/api/projects/{pid}/accept',json={'role':role})
    a=client.post(f'/api/projects/{pid}/checkout').json()
    b=client.post(f'/api/projects/{pid}/checkout').json()
    assert a['id']==b['id']
    assert client.post(f'/api/projects/{pid}/deliver',json={'url':'https://example.com'}).status_code==409

def test_revision_limit_and_upsell(client):
    pid=funded(client)
    deliver(client,pid)
    p=client.post(f'/api/projects/{pid}/changes',json={'request':'Change the heading color to green'}).json()
    assert p['revisions_used']==1 and p['review_deadline'] is None
    p=client.post(f'/api/projects/{pid}/changes',json={'request':'Change font spacing again'}).json()
    assert p['changes'][-1]['verdict']=='out_of_scope'
    assert p['changes'][-1]['amount_cents']==5000
    p=client.post(f'/api/projects/{pid}/changes',json={'request':'Add a database backend'}).json()
    assert p['changes'][-1]['amount_cents']==30000
    pay=p['payments'][-1]
    p=client.post(f"/api/payments/{pay['id']}/authorize").json()
    assert p['changes'][-1]['status']=='funded'
    deliver(client,pid)
    p=client.post(f'/api/projects/{pid}/approve').json()
    assert sum(x['amount_cents'] for x in p['payments'] if x['status']=='captured')==110000

def test_auto_release_and_no_early_release(client):
    pid=funded(client)
    deliver(client,pid)
    assert asyncio.run(main.run_due())==0
    p=client.post(f'/api/projects/{pid}/demo-expire').json()
    assert p['status']=='completed'
    assert asyncio.run(main.run_due())==0

def test_restart_catchup_uses_database_deadline(client):
    pid=funded(client)
    p=deliver(client,pid)
    p['review_deadline']=(main.now()-timedelta(hours=1)).isoformat()
    store.save(p)
    assert asyncio.run(main.run_due())==1
    assert store.get(pid)['status']=='completed'

def test_ambiguity_and_dispute_block_capture(client):
    pid=funded(client)
    deliver(client,pid)
    p=client.post(f'/api/projects/{pid}/changes',json={'request':'Make it more exciting overall'}).json()
    assert p['status']=='disputed' and p['review_deadline'] is None
    assert client.post(f'/api/projects/{pid}/approve').status_code==409
    assert client.post(f'/api/projects/{pid}/demo-expire').status_code==409
    assert client.post(f'/api/projects/{pid}/resolve',json={'request':'Both parties agreed to retain the original deliverable'}).json()['status']=='active'
    assert client.post(f'/api/projects/{pid}/approve').status_code==409

def test_input_validation_and_xss_is_data(client):
    assert client.post('/api/contracts/draft',json={'chat_history':'We discussed a project but no price or revision limit'}).status_code==422
    data=CONTRACT|{'price_cents':-1}
    assert client.post('/api/projects',json={'contract':data,'client_name':'A','freelancer_name':'B'}).status_code==422
    pid=funded(client)
    assert client.post(f'/api/projects/{pid}/deliver',json={'url':'javascript:alert(1)'}).status_code==422
    assert client.post('/api/projects',json={},headers={'Origin':'https://attacker.example'}).status_code==403

def test_environment_isolation_and_sandbox_auth(client,monkeypatch):
    pid=funded(client)
    monkeypatch.setattr(main,'MODE','sandbox')
    monkeypatch.setenv('FAIRFLOW_ADMIN_TOKEN','a-long-private-operator-token')
    assert client.get('/api/projects').status_code==401
    headers={'x-admin-token':'a-long-private-operator-token'}
    assert client.get('/api/projects',headers=headers).json()==[]
    assert client.get(f'/api/projects/{pid}',headers=headers).status_code==404
    assert client.post(f'/api/projects/{pid}/demo-expire',headers=headers).status_code==403

def test_capture_failure_is_not_success(client,monkeypatch):
    pid=funded(client)
    p=deliver(client,pid)
    p['mode']='sandbox';store.save(p)
    monkeypatch.setattr(main,'MODE','sandbox')
    monkeypatch.setenv('FAIRFLOW_ADMIN_TOKEN','token')
    async def fail(payment): raise main.HTTPException(502,'Provider unavailable')
    monkeypatch.setattr(main.paypal,'capture',fail)
    r=client.post(f'/api/projects/{pid}/approve',headers={'x-admin-token':'token'})
    assert r.status_code==502
    assert store.get(pid)['status']=='payment_attention'
    assert store.get(pid)['payments'][0]['status']=='authorized'

def test_demo_draft(client):
    r=client.post('/api/contracts/draft',json={'chat_history':'Build a static page for $800 with 2 revisions.'})
    assert r.status_code==200
    assert r.json()['price_cents']==80000

def test_static_and_health(client):
    assert client.get('/').status_code==200
    assert client.get('/api/health').json()['payment_mode']=='demo'
    assert client.get('/static/app.js').status_code==200
    assert client.post('/api/webhooks/paypal',json={'id':'fake'}).status_code==503

def test_verified_webhook_is_deduplicated(client,monkeypatch):
    monkeypatch.setattr(main,'MODE','sandbox')
    monkeypatch.setenv('PAYPAL_WEBHOOK_ID','WH-TEST')
    async def verify(*args,**kwargs): return {'verification_status':'SUCCESS'}
    monkeypatch.setattr(main.paypal,'call',verify)
    for _ in range(2):
        assert client.post('/api/webhooks/paypal',json={'id':'EV-1','event_type':'PAYMENT.CAPTURE.COMPLETED'}).status_code==200
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM webhook_events').fetchone()[0]==1

def test_invalid_webhook_rejected(client,monkeypatch):
    monkeypatch.setattr(main,'MODE','sandbox')
    monkeypatch.setenv('PAYPAL_WEBHOOK_ID','WH-TEST')
    async def verify(*args,**kwargs): return {'verification_status':'FAILURE'}
    monkeypatch.setattr(main.paypal,'call',verify)
    assert client.post('/api/webhooks/paypal',json={'id':'EV-FAKE'}).status_code==400
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM webhook_events').fetchone()[0]==0
